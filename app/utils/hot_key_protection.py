"""
热点 Key 防护 — 防止缓存击穿 + 热点探测

=== 面试重点 ===
Q: 什么是热点 Key？
A: 秒杀商品的库存 key、爆款文章的详情缓存——瞬间大量请求集中在同一个 key。
   如果缓存刚好过期，所有请求同时打向数据库 → 缓存击穿。

Q: 怎么解决？
A: 三重防护：
   ① 热点探测：滑动窗口统计 key 的访问频率，超过阈值标记为热点
   ② 互斥锁加载：热点 key 过期时只放一个请求去查库，其他等待（你已有分布式锁）
   ③ 永不过期 + 异步刷新：热点 key 不设 TTL，后台定期刷新

Q: 你博客项目怎么用？
A: 文章详情 Cache-Aside 有缓存击穿风险——爆款文章缓存过期瞬间，大量请求打 DB。
   热点探测 → 标记为热点 key → 互斥锁加载 + 异步刷新。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class HotKeyDetector:
    """
    热点 Key 探测器 — 滑动窗口统计访问频率

    使用：
        detector = HotKeyDetector(window_seconds=10, threshold=100)
        if detector.is_hot("article:cache:my-article"):
            # 热点 key：互斥锁加载 + 异步刷新，不直接查库
        else:
            # 普通 key：Cache-Aside 正常流程
    """

    def __init__(self, window_seconds: int = 10, threshold: int = 100):
        self._window = window_seconds
        self._threshold = threshold
        # key → [timestamp, timestamp, ...]
        self._access_log: dict[str, list[float]] = defaultdict(list)
        self._hot_keys: set[str] = set()
        self._lock = asyncio.Lock()

    def record_access(self, key: str) -> None:
        """记录一次 key 访问"""
        now = time.monotonic()
        self._access_log[key].append(now)

    def is_hot(self, key: str) -> bool:
        """
        判断 key 是否为热点（最近 window 秒内访问次数 >= threshold）
        """
        now = time.monotonic()
        cutoff = now - self._window

        # 清理过期记录
        if key in self._access_log:
            self._access_log[key] = [
                t for t in self._access_log[key] if t > cutoff
            ]
            if not self._access_log[key]:
                del self._access_log[key]
                self._hot_keys.discard(key)
                return False

            if len(self._access_log[key]) >= self._threshold:
                self._hot_keys.add(key)
                return True

        self._hot_keys.discard(key)
        return False

    @property
    def hot_keys(self) -> set[str]:
        return self._hot_keys.copy()


class HotKeyProtectedCache:
    """
    热点保护缓存 — 互斥锁加载 + 异步刷新

    面试点：这就是缓存击穿的解决方案——热点 key 过期时：
    ① SET lock:key NX EX（互斥锁，只有一个请求去查库）
    ② 其他请求 sleep + 重试读缓存
    ③ 拿锁的请求查库 → 写缓存 → 释放锁

    使用：
        cache = HotKeyProtectedCache(redis, detector)
        data = await cache.get("article:cache:slug", fetch_func=lambda: db_query(...))
    """

    def __init__(self, redis, detector: HotKeyDetector | None = None):
        self._redis = redis
        self._detector = detector

    async def get(
        self,
        key: str,
        fetch_func,
        ttl: int = 600,
        hot_ttl: int | None = None,  # None = 永不过期
    ) -> dict:
        """获取缓存，热点 key 自动使用互斥锁加载"""
        # ① 先查缓存
        cached = await self._redis.get(key)
        if cached:
            if self._detector:
                self._detector.record_access(key)
                if self._detector.is_hot(key):
                    # 热点 key：异步触发刷新（不阻塞当前请求）
                    asyncio.create_task(self._async_refresh(key, fetch_func, hot_ttl))
            return cached

        # ② 缓存未命中 → 判断是否热点
        is_hot = self._detector and self._detector.is_hot(key)

        if is_hot:
            # ③ 热点 key：互斥锁加载
            lock_key = f"mutex:{key}"
            lock_acquired = await self._redis.set(lock_key, "1", nx=True, ex=10)

            if lock_acquired:
                # 拿到锁 → 查库 → 写缓存
                try:
                    data = await fetch_func()
                    if hot_ttl is not None:
                        await self._redis.setex(key, hot_ttl, data)
                    else:
                        await self._redis.set(key, data)  # 永不过期
                    return data
                finally:
                    await self._redis.delete(lock_key)
            else:
                # 没拿到锁 → 等其他请求加载完
                for _ in range(20):  # 最多等 2 秒
                    await asyncio.sleep(0.1)
                    cached = await self._redis.get(key)
                    if cached:
                        return cached
                # 超时 → 降级：直接查库
                return await fetch_func()
        else:
            # ④ 普通 key：直接查库（Cache-Aside 标准流程）
            data = await fetch_func()
            await self._redis.setex(key, ttl, data)
            return data

    async def _async_refresh(self, key: str, fetch_func, ttl: int | None = None):
        """异步刷新热点 key（后台执行，不阻塞请求）"""
        try:
            data = await fetch_func()
            if ttl is not None:
                await self._redis.setex(key, ttl, data)
            else:
                await self._redis.set(key, data)
        except Exception:
            pass  # 刷新失败不影响当前请求——缓存旧数据还在
