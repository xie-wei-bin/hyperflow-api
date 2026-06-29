"""
阅读量异步回写 — 每 5 分钟从 Redis 批量同步到 MySQL

=== 面试重点 ===
Q: 为什么不在每次阅读时直接 UPDATE MySQL？
A: 热门文章每秒可能有上千次阅读，MySQL 逐行 UPDATE 行锁成为瓶颈。
   方案：Redis INCR 扛住高并发写入 → 5 分钟批量回写一次 MySQL
   1000 次 Redis INCR ≈ 5ms，1000 次 MySQL UPDATE ≈ 500ms，100 倍性能差距

Q: 批量回写如果丢失怎么办（进程崩溃）？
A: Redis 数据是易失的，崩溃后阅读量计数器重置。
   这是有意设计：阅读量不是金融数据，丢几分钟的增量可接受。
   如果要求零丢失，用 MySQL 同步写 + 异步读（CQRS 模式）

Q: 为什么用 asyncio.create_task 而不是 Celery？
A: Celery 需要额外的 Worker 进程 + Broker（RabbitMQ/Redis）→ 架构复杂度翻倍
   当前场景只有一个轻量定时任务，asyncio.create_task 完全够用
   数据量上百万后考虑迁移到 Celery Beat 或 APScheduler
"""

import asyncio

from sqlalchemy import update

from app.database import async_session
from app.logger import logger
from app.models.article import Article
from app.redis_client import redis


async def sync_view_counts() -> None:
    """扫描 blog:article:view:* 键 → 累加到 MySQL → 清除 Redis 计数器"""
    try:
        cursor = 0
        updates: dict[int, int] = {}

        # SCAN 渐进式遍历，不阻塞 Redis（禁止用 KEYS *）
        while True:
            cursor, keys = await redis.scan(cursor, match="blog:article:view:*", count=100)
            for key in keys:
                article_id = int(key.split(":")[-1])
                count_str = await redis.get(key)
                if count_str:
                    count = int(count_str)
                    if count > 0:
                        updates[article_id] = count
            if cursor == 0:
                break

        if updates:
            async with async_session() as session:
                for article_id, count in updates.items():
                    stmt = (
                        update(Article)
                        .where(Article.id == article_id)
                        .values(view_count=Article.view_count + count)  # 累加，不是覆盖
                    )
                    await session.execute(stmt)
                    await redis.delete(f"blog:article:view:{article_id}")
                await session.commit()
            await logger.ainfo(
                "sync.view_counts", synced_articles=len(updates), total_views=sum(updates.values())
            )
    except Exception:
        await logger.aerror("sync.view_counts.failed", exc_info=True)


async def run_sync_loop(interval_seconds: int = 300) -> None:
    """后台定时循环：每 interval_seconds 秒执行一次回写（默认 5 分钟）"""
    await logger.ainfo("sync.loop.started", interval_seconds=interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        await sync_view_counts()
