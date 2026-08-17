"""
Redis 异步连接池 + 企业级监控代理

=== 面试重点 ===
Q: MonitoredRedis 代理模式的价值？
A: 对业务代码透明——router 里还是 await redis.get(xxx)，
   但底层自动加了：慢查询告警、异常计数、调用耗时。
   不用改一行路由代码，监控能力和业务逻辑完全解耦。

Q: 为什么用代理而不是 yield 后置？
A: yield 只包围"取连接"这一步，路由里真正的 Redis 操作在 yield 外面。
   代理模式把每个命令都包住——get/set/incr 每个操作都有监控。
"""

import time

import redis.asyncio as aioredis

from app.config import settings
from app.logger import logger

# ── 企业级 Redis 监控代理 ──────────────────────────
# 面试点：不修改 redis-py 源码，用 Python 代理模式透明拦截所有命令
# 业务代码完全无感知，但运维能从日志里看到每条慢命令


# 自定义代理类名，Monitored = 可监控的，Redis = 缓存客户端
class MonitoredRedis:
    """
    Redis 客户端代理：透明拦截所有命令调用，加监控和容错

    面试点：只覆盖项目实际使用的 10 个方法，非完整代理
    原理：__getattr__ 劫持方法调用 → 包装成带监控的版本
    """

    # 慢查询阈值（毫秒），单条 redis 命令超过 100ms 判定为慢查询
    SLOW_QUERY_MS = 100

    # 被代理的方法列表
    _WRAPPED = {
        "get",
        "set",
        "setex",
        "delete",
        "incr",
        "decr",
        "zincrby",
        "zrevrange",
        "zrem",
        "zremrangebyscore",
        "zcard",
        "zadd",
        "sadd",
        "srem",
        "sismember",
        "scan",
        "ping",
    }

    def __init__(self, client: aioredis.Redis):
        self._client = client
    #Python 优先查找实例自身定义的属性：，__getattr__：属性查找失败后才执行
    # wrapper 是工厂模式：__getattr__ 是个工厂，每次有人调 redis.get，工厂现场造一个 wrapper
    # 函数出来，这个函数自带监控逻辑，然后用完就销毁。装饰器是模板印好的，闭包是现场捏的。
    def __getattr__(self, name: str):
        """
        当访问 obj.xxx，Python 在实例、类、父类整条继承链找不到属性 xxx 时，
        才会自动调用 __getattr__。
        劫持属性访问：业务代码调 redis.get() → 进入这里
        如果是 Redis 命令 → 包装监控逻辑；否则直接透传
        """
        # 将self._client中的name属性(“方法”（函数）和“字段”（数值/字符串/对象）的统称)
        # 给original：异步方法本身（可调用函数对象）
        original = getattr(self._client, name)

        if name not in self._WRAPPED:
            return original  # 非 Redis 命令，不包装
        #定义闭包 wrapper（只是定义函数，尚未执行！）
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await original(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000

                # 慢查询告警
                # args 是 wrapper(*args, **kwargs) 接收的元组
                # *args 作用：解包元组，把元组拆成独立多个参数

                if elapsed_ms > self.SLOW_QUERY_MS:
                    await logger.awarning(  # 异步日志方法
                        "redis.slow_query",
                        command=name,
                        args=str(args)[:200],  # 截断长参数，防止日志爆炸
                        # str(args)：直接把整个参数元组转字符串
                        duration_ms=round(elapsed_ms, 2),  # 保留小数
                    )
                return result

            except aioredis.RedisError:
                elapsed_ms = (time.perf_counter() - start) * 1000
                # 面试点：Redis 异常不打堆栈（网络抖动常见，堆栈噪音大）
                # 记录命令 + 耗时即可定位问题
                await logger.aerror(
                    "redis.error",
                    command=name,
                    args=str(args)[:200],
                    duration_ms=round(elapsed_ms, 2),
                )
                raise  # 重新抛出 → 全局异常处理器 RedisError → 503

        return wrapper

    # 返回的是函数本身，不是函数的执行结果。Python
    # 拿到这个函数后马上调用它——wrapper("article:1")，
    # 这时候 async def wrapper 里的代码才开始跑。
    """
Python 先执行 redis.get
找不到 .get 定义 → 调用 __getattr__("get")
内部生成并返回 wrapper 异步函数
⚠️ 此刻还没有执行任何 Redis 网络请求
紧跟着 ( "article:1" ) 就是函数调用
把参数传给刚才拿到的 wrapper：
python运行await wrapper("article:1")
wrapper 内部才执行：
python运行result = await original("article:1")
# original = _raw_redis.get
    """
    # ── 透传非方法属性 ────────────────────────────
    async def ping(self) -> bool:
        return await self._client.ping()


# ── 连接池 ─────────────────────────────────────────
# 面试点：两层包装
# 1. aioredis.from_url() → 原生连接池（网络层）
# 2. MonitoredRedis() → 监控代理（应用层）
# 两层职责分离，各自可独立替换
_raw_redis: aioredis.Redis = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,  # bytes → str
    max_connections=20,  # 连接池大小
    socket_timeout=5,  # 单条命令超时 5 秒
    retry_on_timeout=True,  # 超时自动重试一次
)

redis = MonitoredRedis(_raw_redis)


async def get_redis() -> MonitoredRedis:
    """
    FastAPI 依赖注入：获取 Redis 连接

    面试点：走 Depends 注入的两个理由
    1. 测试可替换（MockRedis 注入）
    2. 未来换 Redis 集群只需改这里的返回对象
    """
    # redis 是已经初始化完成的客户端实例，不是协程，不能用 await
    return redis
