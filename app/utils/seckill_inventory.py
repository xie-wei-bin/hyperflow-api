"""
Redis + Lua 原子库存操作 — 秒杀核心

=== 面试重点 ===
Q: 秒杀扣库存为什么不能用 MySQL UPDATE？
A: MySQL 行锁在高并发下是瓶颈——1000 人同时抢，行锁排队，RT 指数增长。
   Redis Lua 脚本单线程原子执行：一次网络往返完成检查+扣减+防重，O(1)。

Q: 为什么 Lua 而不是多条 Redis 命令？
A: DECR + 判断分两步不是原子的——
   协程A DECR → 协程B DECR → A 判断 <0 → B 判断 <0 → 两个都以为扣成功。
   Lua 在 Redis 内部单线程执行，天然原子，不用分布式锁。

Q: 这和博客项目有什么关系？
A: 博客本身不需要秒杀。但这个模块展示了：
   ① Redis Lua 原子操作（你已有滑动窗口限流的 Lua 基础）
   ② CAS 乐观扣减模式（库存检查 + 扣减 + 防重 三步原子）
   ③ 和你的分布式锁配合——锁防多实例重复执行，Lua 防单实例内并发
"""

from __future__ import annotations

import time
from typing import Any


# ── Lua 脚本：原子扣库存 ──────────────────────────────
# KEYS[1]: 库存 key（如 "seckill:stock:1"）
# KEYS[2]: 用户防重 key（如 "seckill:user:1:123"）
# ARGV[1]: 扣减数量
# ARGV[2]: 防重 TTL（秒）
#
# 返回值: {1, remaining} 扣减成功 / {0, reason} 失败
#   0: 库存不足 / 1: 已购买（防重）/ 2: 扣减成功

SECILL_INVENTORY_LUA = """
local stock_key = KEYS[1]
local user_key = KEYS[2]
local amount = tonumber(ARGV[1])
local dedup_ttl = tonumber(ARGV[2])

-- ① 防重检查：用户是否已购买
if redis.call('EXISTS', user_key) == 1 then
    return {0, '已参与，请勿重复购买'}
end

-- ② 库存检查
local stock = tonumber(redis.call('GET', stock_key) or '0')
if stock < amount then
    return {0, '库存不足'}
end

-- ③ 原子扣减
local remaining = redis.call('DECRBY', stock_key, amount)

-- ④ 标记用户已购买（防重）
redis.call('SETEX', user_key, dedup_ttl, '1')

return {1, remaining}
"""


class SeckillInventory:
    """
    Redis + Lua 原子库存管理器

    使用：
        inv = SeckillInventory(redis)
        ok, result = await inv.deduct("seckill:stock:1", "seckill:user:1:123", 1)
        if ok:
            # 扣减成功 → 创建订单（异步 Celery）
            create_order.delay(user_id=123, product_id=1)
    """

    def __init__(self, redis):
        self._redis = redis
        self._sha: str | None = None

    async def _ensure_script(self) -> str:
        if self._sha is None:
            self._sha = await self._redis.script_load(SECILL_INVENTORY_LUA)
        return self._sha

    async def deduct(
        self,
        stock_key: str,
        user_key: str,
        amount: int = 1,
        dedup_ttl: int = 3600,
    ) -> tuple[bool, Any]:
        """
        原子扣减库存

        返回: (成功?, 剩余库存或失败原因)
        """
        try:
            sha = await self._ensure_script()
            result = await self._redis.evalsha(
                sha,
                2,  # 2 个 KEY
                stock_key,        # KEYS[1]
                user_key,         # KEYS[2]
                amount,           # ARGV[1]
                dedup_ttl,        # ARGV[2]
            )
            success = bool(result[0])
            detail = result[1]
            return success, detail
        except Exception as e:
            # Redis 故障 → 降级：拒绝所有扣减，不卖比超卖安全
            return False, f"库存服务异常: {e}"

    async def init_stock(self, product_id: int, total: int) -> None:
        """初始化商品库存（秒杀开始前执行）"""
        await self._redis.set(f"seckill:stock:{product_id}", total)
        # 面试点：预热——库存 key 常驻内存，避免第一次访问时惰性加载的延迟

    async def get_stock(self, product_id: int) -> int:
        """查询剩余库存"""
        stock = await self._redis.get(f"seckill:stock:{product_id}")
        return int(stock) if stock else 0

    async def rollback(self, product_id: int, amount: int = 1) -> None:
        """回滚库存（订单超时未支付时调用）"""
        await self._redis.incrby(f"seckill:stock:{product_id}", amount)
