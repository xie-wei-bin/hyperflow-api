"""
数据库连接 — SQLAlchemy 2.0 异步引擎 + session 工厂

=== 面试重点 ===
Q: 为什么用异步（async）而不是同步？
A: FastAPI 本身就是异步框架。同步 ORM 会在查询数据库时阻塞整个线程，
   异步 ORM 在等待数据库响应时可以处理其他请求。
   打个比方：同步 = 餐厅服务员站你桌边等你点完；异步 = 服务员让你慢慢看，先去服务别人。
   在 I/O 密集型场景（数据库读写），异步能提升 3-10 倍吞吐量。

Q: URL.create() 相比直接写连接字符串有什么优势？
A: 1. 自动处理特殊字符转义（密码含 @ 符号时直接拼字符串会炸）
   2. query={"charset": "utf8mb4"} 结构清晰，不会拼错
   3. IDE 有类型提示

Q: pool_size=20, max_overflow=10 怎么定的？
A: pool_size(常驻 20) + max_overflow(临时 10) = 最多 30 连接。
   原则：连接数 ≈ (CPU 核数 × 2) + 预期并发数
   太少 → 请求排队等连接；太多 → MySQL 内存吃紧
   生产环境通常先设 20+10，压测后再调

Q: pool_pre_ping=True 干什么？
A: 从连接池取连接前先发 SELECT 1 测试是否还活着。
   MySQL 默认 8 小时无活动会断开，没有 pre_ping 会报 "MySQL server has gone away"

Q: expire_on_commit=False 为什么？
A: 提交后对象属性不过期。设为 False 后，commit 之后还能继续访问
   article.title，不会触发额外的 SELECT 查询，减少数据库负载
"""

from collections.abc import AsyncGenerator

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# 面试点：URL.create() 是 SQLAlchemy 推荐方式，自动转义 + 结构化
url = URL.create(
    drivername="mysql+aiomysql",  # aiomysql = 异步 MySQL 驱动
    username=settings.DB_USER,
    password=settings.DB_PASSWORD,
    host=settings.DB_HOST,
    port=settings.DB_PORT,
    database=settings.DB_NAME,
    query={"charset": "utf8mb4"},  # utf8mb4 支持 emoji（😀 � 等 4 字节字符）
)

# 面试点：create_async_engine 只创建引擎对象，不建立连接
# 连接在第一次查询时才建立（惰性连接）
engine = create_async_engine(
    url,
    pool_size=20,  # 常驻连接数
    max_overflow=10,  # 溢出连接数（总共最多 30）
    pool_pre_ping=True,  # 连接前检测：发 SELECT 1 验证连接存活
    pool_recycle=3600,  # 1 小时强制回收，防止 MySQL 端断开
    echo=False,  # False=不打印 SQL，True=开发调试时打印每条 SQL
)

# 面试点：async_sessionmaker[AsyncSession] 是泛型工厂
# 每次调用 async_session() 返回一个新的 AsyncSession 对象
async_session = async_sessionmaker[AsyncSession](
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # commit 后对象属性不过期，避免额外查询
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    AsyncGenerator 全称异步生成器，来自标准库 typing，专门标注带 yield 的 async def 函数的返回类型
    第一个参数：yield 向外抛出什么对象（你的代码是 AsyncSession）
    async_session() 调用后得到的实例就是 AsyncSession 类型
    第二个参数：外部调用 .send(xxx) 时传入的值，业务里几乎不用，统一填 None
    FastAPI 依赖注入：每次请求自动创建 session，请求结束自动 commit/rollback

    === 面试重点 ===
    Q: 为什么用 Depends(get_db) 而不是直接调用？
    A: FastAPI 的 Depends 机制：
       1. 请求来了 → 创建 session
       2. 路由处理中使用 session
       3. 正常结束 → yield → commit
       4. 出现异常 → rollback → raise
       路由函数完全不关心 session 的生命周期

    Q: async with 和 async for/yield 有什么区别？
       async with 适用"拿→用→关"模式（Redis 连接）
       async generator 适用"拿→给路由用→路由结束自动清理"模式（DB session）

    Q: 为什么 commit 在 yield 之后而不是在路由里？
    A: 业务代码不应该关心事务管理。路由只做逻辑，事务由框架保证。
       如果在路由里 commit，忘记调用的风险很高。

       async_session()是异步方法，所以要用async with
连接池 ──取──→ Session ──yield──→ 路由用 ──return──→ commit/rollback ──→ 还回连接池
                 （出生）              （活着）              （死亡）
                                        │
                                    增删改查
                                    flush
                                    refresh
                                    （都在事务保护下）

  你的每一个 HTTP 请求都是这个流程，不需要手动管理，FastAPI + Depends 全自动。
    """
    async with async_session() as session:  # ← 每次 NEW
        try:
            yield session  # 交给路由使用
            await session.commit()  # 路由正常结束 → 提交
        except Exception:
            await session.rollback()  # 任何异常 → 回滚
            raise  # 继续向上抛，由异常处理器捕获
