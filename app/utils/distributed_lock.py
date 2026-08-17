"""
Redis 分布式锁 — 跨进程/跨实例的互斥锁

=== 面试重点 ===
Q: asyncio.Lock 和 Redis 分布式锁有什么区别？
A: asyncio.Lock 只能保护单进程内的协程并发。
   多实例部署时（K8s 多副本），每个进程有自己的 Lock，互相不知道对方存在。
   Redis 分布式锁通过 SET NX + TTL 实现跨实例互斥——
   多个实例同时执行 sync_view_counts，只有一个能拿到锁，其他跳过。
   这就是秒杀系统的核心：多实例防止重复扣库存。

Q: 为什么用 SET NX + TTL 而不是 Redlock？
A: Redlock（红锁算法）解决的是 Redis 主从切换时的锁安全问题，
   需要多个独立 Redis 节点，复杂度高。当前单 Redis 实例用 SET NX + TTL
   已经足够覆盖"多实例定时任务防重"的场景。锁持有时间极短（<1s），
   即使 TTL 到期释放，影响也仅限于一次重复同步——不是金融扣款。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

from app.logger import logger


class RedisDistributedLock:
    """
    Redis 分布式锁 — 跨实例互斥

    使用：
        lock = RedisDistributedLock(redis, "sync:view_counts", ttl=30)
        async with lock:
            await sync_view_counts()  # 同一时间只有一个实例执行

    面试点：SET key value NX EX ttl
    - NX (Not eXists)：只在 key 不存在时写入 → 锁的互斥性
    - EX：设置过期时间 → 防止死锁（持锁进程崩溃不会永久阻塞）
    - value 用 UUID：释放时校验——只删自己持有的锁（不删别人的）
    """

    def __init__(self, redis, lock_key: str, ttl: int = 30):
        self._redis = redis
        self._lock_key = f"lock:{lock_key}"
        self._ttl = ttl
        self._lock_value: str | None = None

    async def acquire(self) -> bool:
        """尝试获取锁，返回 True=成功, False=被其他实例持有"""
        self._lock_value = str(uuid.uuid4())
        # 面试点：SET NX EX 原子操作——一条命令完成"如果不存在就写入并设过期"
        result = await self._redis.set(
            self._lock_key,
            self._lock_value,
            nx=True,
            ex=self._ttl,
        )
        if result:
            await logger.ainfo(
                "distributed_lock.acquired",
                key=self._lock_key,
                ttl=self._ttl,
            )
        return bool(result)

    async def release(self) -> bool:
        """释放锁（Lua 原子：校验 value → 匹配才删）"""
        if not self._lock_value:
            return False

        # 面试点：Lua 脚本保证"先校验再删除"的原子性。
        # 如果先 GET 再 DEL，中间可能被其他实例插入 → 删了别人的锁。
        lua_release = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        try:
            result = await self._redis.eval(
                lua_release, 1, self._lock_key, self._lock_value,
            )
            if result:
                await logger.ainfo(
                    "distributed_lock.released",
                    key=self._lock_key,
                )
            return bool(result)
        except Exception:
            await logger.aerror(
                "distributed_lock.release_failed",
                key=self._lock_key,
            )
            return False

    async def __aenter__(self):
        # 自旋等待获取锁（最多等 10 次，每次 100ms）
        for attempt in range(10):
            if await self.acquire():
                return self
            await asyncio.sleep(0.1)
        raise LockAcquireTimeout(f"获取分布式锁超时: {self._lock_key}")

    async def __aexit__(self, *args):
        await self.release()


class LockAcquireTimeout(Exception):
    """获取分布式锁超时"""


async def with_distributed_lock(
    redis,
    lock_key: str,
    ttl: int = 30,
) -> bool:
    """
    便捷函数：获取锁 → 返回 True；没拿到锁 → 返回 False（不等待）

    用于定时任务：只执行一次，没拿到锁直接跳过
    """
    lock = RedisDistributedLock(redis, lock_key, ttl)
    return await lock.acquire()
