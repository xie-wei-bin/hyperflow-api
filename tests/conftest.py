"""
测试配置 — async client fixture + Mock DB + Mock Redis

=== 面试重点 ===
Q: 为什么测试不用真实 MySQL 和 Redis？
A: 1. 速度：SQLite 比 MySQL 快，测试跑完不到 15 秒
   2. 环境无关：CI 服务器不需要装 MySQL/Redis，拉代码就能跑
   3. 隔离：每个测试前后自动建表/删表，测试之间不互相污染
   4. 确定性：Mock Redis 没有网络延迟，行为完全可控

Q: dependency_overrides 机制是什么？
A: FastAPI 允许在运行时替换依赖：
   app.dependency_overrides[get_db] = override_get_db
   路由函数里 Depends(get_db) 不会调用真正的 get_db
   而是调用 override_get_db（返回 SQLite session）
   测试结束后可以清除 overrides 恢复原状

Q: MockRedis 为什么不直接用 fakeredis 库？
A: fakeredis 是成熟方案，但为了零外部依赖选择了手写。
   手写的 MockRedis 覆盖了我们实际使用的核心方法，
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
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models.base import Base
from app.redis_client import get_redis

# 面试点：SQLite+aiosqlite 替代 MySQL，测试无需外部依赖
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)


# SQLite 默认不开启外键约束，手动 PRAGMA
@event.listens_for(test_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """开启 SQLite 外键约束以支持 ondelete CASCADE"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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

    面试点：只实现项目真实使用的核心方法
    不需要完整仿制所有 Redis 命令，按需实现即可
    """

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

    async def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        """ZREVRANGE：按分数降序取排名，start=0 stop=19 取 Top 20"""
        import json

        zset: dict[str, float] = json.loads(self._data.get(key, "{}"))
        # 按分数降序排列
        sorted_items = sorted(zset.items(), key=lambda x: x[1], reverse=True)
        return [member for member, _ in sorted_items[start:stop + 1]]

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

    # ── ZSet 方法（滑动窗口限流需要）──
    # 内部用 dict[str, dict[str, float]] 模拟：{key: {member: score}}

    def __init__(self):
        self._data: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}
        self._ttl: dict[str, int] = {}
        self._zsets: dict[str, dict[str, float]] = {}  # 新增
        self._scripts: dict[str, str] = {}  # SCRIPT LOAD 缓存

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        """ZADD key score member"""
        if key not in self._zsets:
            self._zsets[key] = {}
        added = 0
        for member, score in mapping.items():
            if member not in self._zsets[key]:
                added += 1
            self._zsets[key][member] = score
        return added

    async def zcard(self, key: str) -> int:
        """ZCARD key"""
        return len(self._zsets.get(key, {}))

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        """ZREMRANGEBYSCORE key min max"""
        if key not in self._zsets:
            return 0
        removed = 0
        to_remove = []
        for member, score in self._zsets[key].items():
            if min_score <= score <= max_score:
                to_remove.append(member)
                removed += 1
        for member in to_remove:
            del self._zsets[key][member]
        return removed

    async def zrange(self, key: str, start: int, stop: int, withscores: bool = False):
        """ZRANGE key start stop [WITHSCORES] — 按 score 升序"""
        if key not in self._zsets:
            return []
        sorted_items = sorted(self._zsets[key].items(), key=lambda x: x[1])
        if stop == -1:
            stop = len(sorted_items) - 1
        result = sorted_items[start:stop + 1]
        if withscores:
            flat = []
            for member, score in result:
                flat.append(member)
                flat.append(str(score))
            return flat
        return [member for member, _ in result]

    async def expire(self, key: str, seconds: int) -> bool:
        """EXPIRE key seconds — Mock 总是返回 True"""
        return True

    # ── Lua 脚本支持（滑动窗口限流需要）──

    async def script_load(self, script: str) -> str:
        """SCRIPT LOAD — 缓存脚本，返回假 SHA"""
        import hashlib
        sha = hashlib.sha1(script.encode()).hexdigest()
        self._scripts[sha] = script
        return sha

    async def evalsha(self, sha: str, numkeys: int, *args):
        """EVALSHA — 执行缓存的 Lua 脚本（Python 模拟滑动窗口逻辑）"""
        if sha not in self._scripts:
            raise Exception("NOSCRIPT No matching script")
        # 不执行真实 Lua，直接在 Python 里实现滑动窗口逻辑
        key = args[0]
        window_ms = int(args[1]) * 1000
        max_requests = int(args[2])
        now = int(args[3])
        request_id = str(args[4])

        # ① 清理窗口外过期记录
        window_start = now - window_ms
        if key in self._zsets:
            to_remove = []
            for member, score in self._zsets[key].items():
                if score <= window_start:
                    to_remove.append(member)
            for member in to_remove:
                del self._zsets[key][member]

        # ② 统计当前窗口请求数
        count = len(self._zsets.get(key, {}))

        # ③ 判断 + 记录
        if count < max_requests:
            if key not in self._zsets:
                self._zsets[key] = {}
            self._zsets[key][request_id] = float(now)
            remaining = max_requests - count - 1
            # 计算 reset_time
            if self._zsets[key]:
                oldest_score = min(s.values() for s in [self._zsets[key]])
                reset_time = int(oldest_score) + window_ms
            else:
                reset_time = now + window_ms
            return [1, remaining, reset_time]
        else:
            if key in self._zsets and self._zsets[key]:
                oldest_score = min(s.values() for s in [self._zsets[key]])
                reset_time = int(oldest_score) + window_ms
            else:
                reset_time = now + window_ms
            return [0, 0, reset_time]


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
    每个测试前后清空数据库 + 清理 Mock Redis

    面试点：不删表重建，而是清空数据——
    避免 SQLite drop_all 外键约束问题，更快更稳定
    """
    # 首次运行建表
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── RBAC 初始化（测试环境也需要权限数据） ──
    from app.utils.rbac_seed import seed_rbac

    async with test_async_session() as seed_session:
        await seed_rbac(seed_session)
        await seed_session.commit()

    # 重置限流器状态（避免跨测试累加导致 429）
    mock_redis._data.clear()
    mock_redis._sets.clear()
    mock_redis._ttl.clear()
    mock_redis._zsets.clear()
    mock_redis._scripts.clear()

    yield

    # 测试后清空所有表数据（反向遍历处理外键依赖）
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


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
