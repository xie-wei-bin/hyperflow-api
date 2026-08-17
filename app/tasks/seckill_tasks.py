"""
秒杀异步任务 — Celery 消费者扣库存 + APScheduler 超时回库存

=== 面试重点 ===
Q: 为什么扣库存后在 Celery 里创建订单而不是 API 路由里？
A: API 路由只做 Redis 原子扣库存（<1ms），立刻返回结果给用户。
   创建订单（写 MySQL、发通知）是慢操作 → 交给 Celery Worker 异步执行。
   这就是"削峰"——1000 QPS 的秒杀请求，API 层恒定 <5ms 响应，
   真正的"重活"由 Worker 慢慢消化。

Q: 订单超时怎么回库存？
A: APScheduler 每 10 秒扫一次 pending 订单，超过 15 分钟未支付 → 取消订单 + 回滚 Redis 库存。
   配合你的分布式锁（RedisDistributedLock）→ 多实例只有一个执行。
"""

import asyncio
import time

from app.celery_app import celery_app
from app.logger import logger


# ── 消费任务：创建订单 ─────────────────────────────────


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    acks_late=True,
)
def create_seckill_order(self, user_id: int, product_id: int, amount: int = 1):
    """
    创建秒杀订单（消费者——API 路由只做 Redis 扣库存后立刻返回）

    面试点：acks_late=True → 任务执行完才 ack，Worker 崩溃消息回到队列——
    不会出现"扣了库存但没创建订单"的情况。

    实际流程：
    ① 检查 Redis 是否有扣库存记录（防重）
    ② INSERT INTO seckill_order（UNIQUE 约束兜底防重）
    ③ 发送通知（WebSocket + 邮件）
    """
    try:
        # ── 模拟写订单（生产环境：SQLAlchemy INSERT） ──
        logger.info(
            "seckill.order.creating",
            user_id=user_id,
            product_id=product_id,
            amount=amount,
        )
        time.sleep(0.1)  # 模拟数据库写入

        # ── 发送通知（异步，不阻塞订单创建） ──
        # notify_user.delay(user_id, f"秒杀订单已创建，请在 15 分钟内支付")
        # send_kafka_event("order.created", {...})  ← 事件总线发布

        logger.info(
            "seckill.order.created",
            user_id=user_id,
            product_id=product_id,
        )
        return {"status": "created", "user_id": user_id, "product_id": product_id}

    except Exception as exc:
        logger.error(
            "seckill.order.failed",
            user_id=user_id,
            product_id=product_id,
            retry_count=self.request.retries,
            error=str(exc),
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        # 死信：3 次全失败 → 人工介入
        logger.error(
            "seckill.order.dead_letter",
            user_id=user_id,
            product_id=product_id,
            message="创建订单失败 3 次，需人工介入",
        )
        return {"status": "dead_letter", "user_id": user_id, "product_id": product_id}


# ── 消费任务：取消超时订单 + 回滚库存 ─────────────────


@celery_app.task(bind=True, max_retries=2, acks_late=True)
def cancel_timeout_orders(self):
    """
    取消超时未支付订单 + 回滚 Redis 库存

    面试点：这个任务由 APScheduler 每 10 秒触发一次。
    配合 RedisDistributedLock → 多实例只有一个真正执行扫描。
    """
    # ── 模拟扫描 + 回滚（生产环境：SQLAlchemy SELECT + UPDATE） ──
    timeout_seconds = 15 * 60  # 15 分钟
    logger.info("seckill.timeout_scan.start", timeout_seconds=timeout_seconds)

    # SELECT * FROM seckill_order
    # WHERE status='pending' AND created_at < NOW() - INTERVAL 15 MINUTE
    timeout_orders: list[dict] = []  # 模拟查询结果

    for order in timeout_orders:
        logger.info(
            "seckill.order.canceling",
            order_id=order.get("id"),
            reason="超时未支付",
        )
        # ① 更新订单状态 → canceled
        # ② 回滚 Redis 库存: INCRBY seckill:stock:{product_id} 1
        # ③ 清除用户防重标记: DEL seckill:user:{product_id}:{user_id}

    logger.info(
        "seckill.timeout_scan.done",
        canceled_count=len(timeout_orders),
    )
    return {"canceled": len(timeout_orders)}


# ── APScheduler 定时调度（替代 Celery Beat，秒级精度） ──

# 面试点：Celery Beat 最小调度间隔是分钟级（crontab）。
# 秒杀场景需要 10 秒级的超时扫描，用 APScheduler 更合适。

# 启动方式（在 lifespan 中）：
# from apscheduler.schedulers.asyncio import AsyncIOScheduler
# scheduler = AsyncIOScheduler()
# scheduler.add_job(
#     run_timeout_scan, "interval", seconds=10,
#     id="seckill_timeout_scan",
# )
# scheduler.start()


async def run_timeout_scan(redis=None):
    """
    定时扫描超时订单 + 回滚库存（10 秒一次）

    面试点：① 分布式锁防重——多实例只有一个执行
           ② fail open——没拿到锁直接跳过，不阻塞定时器
    """
    from app.utils.distributed_lock import RedisDistributedLock

    lock = RedisDistributedLock(redis, "seckill:timeout_scan", ttl=15)
    acquired = await lock.acquire()
    if not acquired:
        return  # 其他实例正在执行，跳过

    try:
        cancel_timeout_orders.delay()
    finally:
        await lock.release()


# ── 库存对账任务（每天执行一次） ──


async def run_stock_reconciliation(redis, db):
    """
    库存对账：Redis 库存 vs MySQL 实际剩余

    面试点：Redis 是内存数据库，极端情况（宕机/主从切换）数据可能丢失。
    每天凌晨对比 Redis 库存和 DB 实际库存，不一致时以 DB 为准修正 Redis。
    """
    # SELECT product_id, total_stock FROM seckill_product
    # for each product:
    #   actual_sold = SELECT COUNT(*) FROM seckill_order WHERE product_id=? AND status!='timeout_cancel'
    #   actual_stock = total_stock - actual_sold
    #   redis_stock = await redis.get(f"seckill:stock:{product_id}")
    #   if actual_stock != redis_stock:
    #       await redis.set(f"seckill:stock:{product_id}", actual_stock)  # DB 为准
    logger.info("seckill.reconciliation.start")
    # ...
    logger.info("seckill.reconciliation.done")
