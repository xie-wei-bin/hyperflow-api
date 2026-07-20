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
    """扫描 blog:article:view:* 键 → 累加到 MySQL → 清除 Redis 计数器
    返回值解包：redis.scan
cursor：下一轮遍历的新游标
keys：本轮取出的 Redis 键列表
    """
    try:
        cursor = 0#游标，Redis scan 分页遍历的标记；0 代表从头开始，遍历完会回到 0
        updates: dict[int, int] = {}#待更新数据缓存字典，存放 {文章 ID: 新增阅读量}

        # SCAN 渐进式遍历，不阻塞 Redis（禁止用 KEYS *）
        while True:
                                #match：匹配过滤，只取出前缀blog:article:view:的 key
            cursor, keys = await redis.scan(cursor, match="blog:article:view:*", count=100)
            for key in keys:
                article_id = int(key.split(":")[-1])
                count_str = await redis.get(key)#Redis 读取指定 key 的值str类型
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
                        update(Article)#Article ORM类名
                        .where(Article.id == article_id)
                        .values(view_count=Article.view_count + count)  # 累加，不是覆盖
                    )#view_count数据库列名
                    await session.execute(stmt)#执行构造好的 update 更新 SQL
                    await redis.delete(f"blog:article:view:{article_id}")#删除指定 Redis key
                await session.commit()
            await logger.ainfo(
                "sync.view_counts", synced_articles=len(updates), total_views=sum(updates.values())
            )
    except Exception:
        await logger.aerror("sync.view_counts.failed", exc_info=True)


async def cleanup_zset() -> None:
    """
    清理 ZSet 中的脏数据：
    - 已删除/下架的文章从排行榜移除
    - 长期 0 分文章自动淘汰，节省内存
    """
    try:
        from sqlalchemy import select as _select

        # ① 移除已删除/下架的文章
        raw = await redis.zrevrange("blog:article:hot", 0, -1)
        if raw:
            async with async_session() as session:
                result = await session.execute(
                    _select(Article.id).where(
                        Article.id.in_([int(i) for i in raw]),
                        (Article.is_deleted == True)  # noqa: E712
                        | (Article.status != "published"),
                    )
                )
                invalid_ids = {str(row[0]) for row in result.all()}
                if invalid_ids:
                    await redis.zrem("blog:article:hot", *invalid_ids)
                    await logger.ainfo("zset.cleanup", removed=len(invalid_ids))

        # ② 移除长期 0 分的冷文章
        await redis.zremrangebyscore("blog:article:hot", "-inf", "0")

        # ③ 物理清理：删除 30 天前软删除且无子回复的废弃评论
        from app.models.comment import Comment as _Comment
        from sqlalchemy import delete as _delete

        async with async_session() as session:
            # 找到 is_deleted=True 且没有任何子回复的评论
            orphan = _select(_Comment.id).where(
                _Comment.is_deleted == True,  # noqa: E712
                _Comment.id.notin_(
                    _select(_Comment.parent_id).where(_Comment.parent_id.isnot(None))
                ),
            )
            stmt = _delete(_Comment).where(_Comment.id.in_(orphan))
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount:
                await logger.ainfo("comment.cleanup", deleted=result.rowcount)
    except Exception:
        await logger.aerror("zset.cleanup.failed", exc_info=True)


async def repair_zset() -> None:
    """
    全量修正 ZSet 分数：从 MySQL 重新计算 → ZADD 覆盖
    解决 ZINCRBY 长期增量导致的分数漂移问题
    ZADD 覆盖写，ZINCRBY 增量写，二者互补
    """
    try:
        from sqlalchemy import select as _select, func as _func
        from app.config import settings as _s
        from app.models.comment import Comment as _Comment

        async with async_session() as session:
            # 查询所有已发布文章的阅读量
            result = await session.execute(
                _select(Article.id, Article.view_count)
                .where(Article.status == "published", Article.is_deleted == False)  # noqa: E712
            )
            articles = {row[0]: row[1] for row in result.all()}
            if not articles:
                return

            ids = list(articles.keys())

            # 批量查点赞数
            like_result = await session.execute(
                _select(Like.article_id, _func.count().label("cnt"))
                .where(Like.article_id.in_(ids))
                .group_by(Like.article_id)
            )
            like_counts = {row.article_id: row.cnt for row in like_result.all()}

            # 批量查评论数
            comment_result = await session.execute(
                _select(_Comment.article_id, _func.count().label("cnt"))
                .where(_Comment.article_id.in_(ids))
                .group_by(_Comment.article_id)
            )
            comment_counts = {row.article_id: row.cnt for row in comment_result.all()}

            # 计算精确热度分 → ZADD 覆盖写入
            mapping = {}
            for aid, views in articles.items():
                score = (
                    views * _s.HOT_RANK_VIEW_WEIGHT
                    + like_counts.get(aid, 0) * _s.HOT_RANK_LIKE_WEIGHT
                    + comment_counts.get(aid, 0) * _s.HOT_RANK_COMMENT_WEIGHT
                )
                if score > 0:
                    mapping[str(aid)] = score

            if mapping:
                await redis.zadd("blog:article:hot", mapping)
                await logger.ainfo("zset.repair", repaired=len(mapping))
    except Exception:
        await logger.aerror("zset.repair.failed", exc_info=True)


async def warmup_zset() -> None:
    """冷启动预热：Redis 为空时从 MySQL 同步热度数据到 ZSet"""
    try:
        # 检查 ZSet 是否为空
        count = await redis.zcard("blog:article:hot")
        if count > 0:
            return  # 已有数据，跳过预热

        from sqlalchemy import select as _select
        from app.config import settings as _s

        async with async_session() as session:
            result = await session.execute(
                _select(Article.id, Article.view_count)
                .where(Article.status == "published", Article.is_deleted == False)  # noqa: E712
                .order_by(Article.view_count.desc())
                .limit(500)
            )
            rows = result.all()
            if rows:
                # 用 ZADD 批量写入，热度分 = 阅读×1
                mapping = {str(r[0]): r[1] * _s.HOT_RANK_VIEW_WEIGHT for r in rows}
                await redis.zadd("blog:article:hot", mapping)
                await logger.ainfo("zset.warmup", articles=len(rows))
    except Exception:
        await logger.aerror("zset.warmup.failed", exc_info=True)


async def run_sync_loop(interval_seconds: int = 300) -> None:
    """后台定时循环：每 5 分钟回写阅读量 + 清理脏数据，每 1 小时全量修正分数"""
    loop_count = 0
    await warmup_zset()
    await logger.ainfo("sync.loop.started", interval_seconds=interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        await sync_view_counts()
        await cleanup_zset()
        loop_count += 1
        if loop_count % 12 == 0:  # 每 12 轮 = 1 小时 → 全量修正分数
            await repair_zset()
