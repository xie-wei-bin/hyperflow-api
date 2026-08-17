"""
滑动窗口限流器 — Redis + Lua 原子实现

=== 面试重点 ===
Q: 为什么用滑动窗口而不是固定窗口？
A: 固定窗口（"每分钟 5 次"）在窗口边界有临界突刺缺陷：
   59 秒发 5 次 + 下一秒（新窗口）再发 5 次 = 2 秒内 10 次请求
   滑动窗口没有固定边界，任何 N 秒窗口内最多 M 次请求，彻底消除突刺。

Q: 为什么用 Lua 脚本而不是多条 Redis 命令？
A: 滑动窗口需要三步操作：
   1. ZREMRANGEBYSCORE 清理过期记录
   2. ZCARD 统计当前窗口请求数
   3. ZADD 记录本次请求 + EXPIRE 设置过期
   三条命令不是原子的——并发时两个请求可能同时通过检查再各自 ZADD。
   Lua 脚本在 Redis 服务端单线程执行，天然原子，不用分布式锁。

Q: 和 slowapi 内置方案的区别？
A: | | slowapi 内置 | 本项目自实现 |
   |---|---|-------------|
   | Redis 驱动 | 同步 redis-py（阻塞 asyncio）| 异步 aioredis（零阻塞）|
   | 算法 | 令牌桶（依赖 limits 库）| 滑动窗口（自研 Lua）|
   | 依赖 | slowapi + limits + redis-py | 仅 aioredis（项目已有）|
   | 控制力 | 黑盒 | 完全自主可控 |

Q: 为什么用 ZSet 而不是 String + TTL？
A: ZSet 的 score 存时间戳，天然支持滑动窗口：
   - ZREMRANGEBYSCORE 一键删除窗口外的旧记录
   - ZCARD 即时统计窗口内请求数
   - 不需要在应用层维护时间窗口逻辑
   String 方案需要手动管理多个 key（如 rate:ip:second_1, rate:ip:second_2...），复杂且不精确。
"""

import time
from typing import Optional

import redis.asyncio as aioredis

# ── Lua 脚本：滑动窗口限流（原子执行） ──
# KEYS[1]: 限流 key（如 "rate:192.168.1.1:/api/auth/login"）
# ARGV[1]: 窗口大小（秒）
# ARGV[2]: 窗口内最大请求数
# ARGV[3]: 当前时间戳（毫秒）
# ARGV[4]: 唯一请求 ID（时间戳+随机数，防同一毫秒内重复）
#
# 返回值: {allowed, remaining, reset_time}
#   allowed:  1=放行, 0=拒绝
#   remaining: 剩余可用次数
#   reset_time: 下一次窗口重置的毫秒时间戳（前端可用来倒计时）

SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window_ms = tonumber(ARGV[1]) * 1000
local max_requests = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local request_id = ARGV[4]

-- ① 清理窗口外的过期记录
local window_start = now - window_ms
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

-- ② 统计当前窗口内请求数
local count = redis.call('ZCARD', key)

-- ③ 判断 + 记录
if count < max_requests then
    redis.call('ZADD', key, now, request_id)
    redis.call('EXPIRE', key, math.ceil(window_ms / 1000) + 1)
    local remaining = max_requests - count - 1
    -- 计算下次重置时间（最早记录的过期时间）
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_time = 0
    if #oldest > 0 then
        reset_time = tonumber(oldest[2]) + window_ms
    end
    return {1, remaining, reset_time}
else
    -- 被限流，返回最近可重试时间
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_time = 0
    if #oldest > 0 then
        reset_time = tonumber(oldest[2]) + window_ms
    end
    return {0, 0, reset_time}
end
"""


class SlidingWindowLimiter:
    """
    Redis + Lua 滑动窗口限流器

    使用方式：
        limiter = SlidingWindowLimiter(redis)
        allowed, remaining, reset_time = await limiter.check(
            key="rate:192.168.1.1:/api/auth/login",
            max_requests=10,
            window_seconds=60,
        )
    """

    def __init__(self, redis: aioredis.Redis):
        self._redis = redis
        # 面试点：SCRIPT LOAD 预加载 Lua 脚本到 Redis，后续用 EVALSHA 调用
        # EVALSHA 比 EVAL 快（只传 SHA 哈希，不用每次传完整脚本）
        self._script_sha: Optional[str] = None

    async def _ensure_script_loaded(self) -> str:
        """懒加载 Lua 脚本：首次调用 SCRIPT LOAD，后续用缓存的 SHA"""
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(SLIDING_WINDOW_LUA)
        return self._script_sha

    async def check(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """
        检查请求是否允许

        返回: (allowed, remaining, reset_time_ms)
        - allowed: True=放行, False=限流
        - remaining: 剩余可用次数
        - reset_time_ms: 窗口重置的毫秒时间戳
        """
        now_ms = int(time.time() * 1000)
        request_id = f"{now_ms}-{_random_hex(6)}"

        try:
            sha = await self._ensure_script_loaded()
            result = await self._redis.evalsha(
                sha,
                1,  # 1 个 KEY
                key,
                window_seconds,
                max_requests,
                now_ms,
                request_id,
            )
        except Exception:
            # 面试点：Redis 挂了怎么办？降级放行，不阻塞业务
            # 限流是防护手段，不是核心功能——Redis 故障时宁可无限流也不能拒绝所有请求
            return True, max_requests, 0

        allowed = bool(result[0])
        remaining = int(result[1])
        reset_time = int(result[2])
        return allowed, remaining, reset_time


def _random_hex(n: int) -> str:
    """生成随机 hex 字符串，用作请求唯一标识"""
    import secrets

    return secrets.token_hex(n // 2)[:n]
