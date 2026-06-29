"""
测试配置 — async client fixture + Mock DB + Mock Redis

=== 面试重点 ===
Q: 为什么测试不用真实 MySQL 和 Redis？
A: 1. 速度：SQLite 内存模式比 MySQL 快 10 倍+，12 个测试跑完不到 5 秒
   2. 环境无关：CI 服务器不需要装 MySQL/Redis，拉代码就能跑
   3. 隔离：每次测试前后自动建表/删表，测试之间不互相污染
   4. 确定性：Mock Redis 没有网络延迟，行为完全可控

Q: dependency_overrides 机制是什么？
A: FastAPI 允许在运行时替换依赖：
   app.dependency_overrides[get_db] = override_get_db
   路由函数里 Depends(get_db) 不会调用真正的 get_db
   而是调用 override_get_db（返回 SQLite session）
   测试结束后可以清除 overrides 恢复原状

Q: MockRedis 为什么不直接用 fakeredis 库？
A: fakeredis 是成熟方案，但为了零外部依赖选择了手写。
   手写的 MockRedis 覆盖了我们实际使用的 7 个方法，
   够用且比 fakeredis 更轻。如果项目变大，再换成 fakeredis 即可

Q: autouse=True 什么含义？
A: 每个测试函数执行前自动运行 setup_database，不需要手动声明
   yield 之前 = setup（建表 + 清理 mock）
   yield 之后 = teardown（删表）
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models.base import Base
from app.redis_client import get_redis

# 面试点：SQLite+aiosqlite 替代 MySQL，测试无需外部依赖
# /./test.db 是相对路径，测试结束后可删除
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_async_session = async_sessionmaker[AsyncSession](
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """用 SQLite 替代 MySQL"""
    async with test_async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class MockRedis:
    """
    内存 Redis 模拟

    面试点：只实现项目真实使用的 7 个方法
    不需要完整仿制所有 Redis 命令，按需实现即可
    """

    def __init__(self):
        self._data: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}
        self._ttl: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value
        self._ttl[key] = ttl

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)
            self._ttl.pop(key, None)

    async def incr(self, key: str) -> int:
        val = int(self._data.get(key, "0")) + 1
        self._data[key] = str(val)
        return val

    async def decr(self, key: str) -> int:
        val = int(self._data.get(key, "0")) - 1
        self._data[key] = str(val)
        return val

    async def zincrby(self, key: str, amount: float, value: str) -> float:
        import json

        zset: dict[str, float] = json.loads(self._data.get(key, "{}"))
        zset[value] = zset.get(value, 0) + amount
        self._data[key] = json.dumps(zset)
        return zset[value]

    async def sadd(self, key: str, *values: str) -> int:
        if key not in self._sets:
            self._sets[key] = set()
        added = sum(1 for v in values if v not in self._sets[key])
        self._sets[key].update(values)
        return added

    async def srem(self, key: str, *values: str) -> int:
        if key not in self._sets:
            return 0
        removed = sum(1 for v in values if v in self._sets[key])
        self._sets[key].difference_update(values)
        return removed

    async def sismember(self, key: str, value: str) -> bool:
        return key in self._sets and value in self._sets[key]

    async def ping(self) -> bool:
        return True


mock_redis = MockRedis()


async def override_get_redis():
    """用 Mock Redis 替代真实 Redis"""
    return mock_redis


# 面试点：在 app 启动后替换依赖，所有 Depends 都指向测试版本
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_redis] = override_get_redis


@pytest.fixture(scope="session")
def event_loop():
    """为整个测试 session 创建事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def setup_database():
    """
    每个测试前后自动建表/删表 + 清理 Mock Redis

    面试点：隔离性 — 测试 A 创建的用户不会影响测试 B
    如果测试 B 依赖测试 A 的数据，一旦 A 失败 B 也跟着失败
    每个测试独立是单元测试的基本要求
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    mock_redis._data.clear()
    mock_redis._sets.clear()
    mock_redis._ttl.clear()
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """
    异步 HTTP 测试客户端

    面试点：ASGITransport 直连 FastAPI app，不需要启动真实服务器
    比 requests 库更接近真实请求，且不需要占用端口
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
