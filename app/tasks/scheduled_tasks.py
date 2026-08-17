"""
Celery Beat 定时调度任务 — 企业级定时任务

=== 面试重点 ===
Q: Celery Beat 和 while True + sleep 的区别？
A: 三个关键差异：
   1. **可靠性**：Beat 独立进程，API 进程崩溃不影响定时任务；sleep 和 API 同进程，崩了全丢
   2. **分布式**：多实例部署时，Beat 只有一个节点发调度（防止重复执行），Worker 多节点并行消费
   3. **可观测**：Celery Flower 可视化管理面板，查看任务执行历史、失败重试、执行耗时
   对比：while True + sleep 是"能用"，Celery Beat 是"生产级"

Q: 为什么不同时保留 asyncio.create_task 和 Celery Beat？
A: 双重运行会导致重复执行（比如阅读量回写两次，数据翻倍）。
   Celery Beat 替代旧方案后，main.py 里的 asyncio.create_task 就可以删除了。

Q: schedule 配置说明？
A: - 整数秒数（300.0）：每 N 秒执行
   - crontab(minute=0, hour=3)：每天凌晨 3 点
   - crontab(minute='*/30')：每 30 分钟
   - timedelta(seconds=300)：每 300 秒（和整数等价，但更易读）

Q: 怎么启动 Celery Beat？
A: 需要两个进程：
   1. Worker：celery -A app.celery_app worker --loglevel=info --concurrency=4
   2. Beat：celery -A app.celery_app beat --loglevel=info
   生产环境用 supervisor/systemd 分别管理两个进程。
"""

import asyncio
from datetime import timedelta

from celery import shared_task
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from sqlalchemy import func, select as _select, update

from app.celery_app import celery_app

logger = get_task_logger(__name__)


# ── 任务 1：阅读量回写（每 5 分钟） ──

@celery_app.task(
    name="scheduled.sync_view_counts",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def sync_view_counts_task(self):
    """
    从 Redis 扫描 blog:article:view:* → 批量 UPDATE MySQL

    面试点：为什么 autoretry_for=(Exception,) 而不是特定异常？
    这是兜底策略——任何异常都重试（网络闪断、Redis 超时、MySQL 连接池耗尽）。
    生产环境建议细化为 (TimeoutError, ConnectionError)。
    """
    import asyncio as _asyncio

    async def _run():
        from app.utils.sync_views import sync_view_counts

        await sync_view_counts()

    try:
        _asyncio.run(_run())
    except Exception as exc:
        logger.error("sync_view_counts 执行失败: %s", exc)
        raise self.retry(exc=exc)


# ── 任务 2：ZSet 脏数据清理（每 5 分钟） ──

@celery_app.task(
    name="scheduled.cleanup_zset",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def cleanup_zset_task(self):
    """
    清理 ZSet 排行榜脏数据：
    - 移除已删除/下架文章
    - 移除 0 分冷文章
    - 物理清理废弃评论
    """
    import asyncio as _asyncio

    async def _run():
        from app.utils.sync_views import cleanup_zset

        await cleanup_zset()

    try:
        _asyncio.run(_run())
    except Exception as exc:
        logger.error("cleanup_zset 执行失败: %s", exc)
        raise self.retry(exc=exc)


# ── 任务 3：ZSet 全量修正（每天凌晨 3 点） ──

@celery_app.task(
    name="scheduled.repair_zset",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
    autoretry_for=(Exception,),
)
def repair_zset_task(self):
    """
    全量重新计算 ZSet 热度分 —— 修正长期 ZINCRBY 增量漂移
    每天凌晨 3 点执行（业务低峰期），避免影响用户请求
    """
    import asyncio as _asyncio

    async def _run():
        from app.utils.sync_views import repair_zset

        await repair_zset()

    try:
        _asyncio.run(_run())
    except Exception as exc:
        logger.error("repair_zset 执行失败: %s", exc)
        raise self.retry(exc=exc)


# ── 任务 4：ZSet 冷启动预热（应用启动时 + 每 30 分钟） ──

@celery_app.task(
    name="scheduled.warmup_zset",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    autoretry_for=(Exception,),
)
def warmup_zset_task(self):
    """
    冷启动预热：Redis ZSet 为空时从 MySQL 同步热度数据
    每 30 分钟检查一次，避免 Redis 重启后排行榜丢失
    """
    import asyncio as _asyncio

    async def _run():
        from app.utils.sync_views import warmup_zset

        await warmup_zset()

    try:
        _asyncio.run(_run())
    except Exception as exc:
        logger.error("warmup_zset 执行失败: %s", exc)
        raise self.retry(exc=exc)


# ── 任务 5：Prometheus 业务指标更新（每 1 分钟） ──

@celery_app.task(
    name="scheduled.update_business_metrics",
    bind=True,
    max_retries=1,
    default_retry_delay=10,
    autoretry_for=(Exception,),
)
def update_business_metrics_task(self):
    """
    更新 Prometheus 业务 Gauge 指标：
    - blog_articles_total（按 draft/published 分组）
    - blog_users_total
    - blog_ws_connections（WebSocket 活跃连接数）
    """
    import asyncio as _asyncio

    async def _run():
        from app.database import async_session
        from app.middleware.metrics import (
            article_total_gauge,
            user_total_gauge,
            ws_connections_gauge,
        )
        from app.models.article import Article
        from app.models.user import User
        from app.utils.ws_manager import manager

        async with async_session() as db:
            # 按状态统计文章数
            result = await db.execute(
                _select(Article.status, func.count())
                .where(Article.is_deleted == False)  # noqa: E712
                .group_by(Article.status)
            )
            for status, count in result.all():
                article_total_gauge.labels(status=status).set(count)

            # 总用户数
            user_count = await db.scalar(_select(func.count()).select_from(User))
            if user_count is not None:
                user_total_gauge.set(user_count)

        # WebSocket 连接数（内存数据，2025.08 适配多连接：用 online_count 统计连接而非用户数）
        ws_connections_gauge.set(manager.online_count)

    try:
        _asyncio.run(_run())
    except Exception as exc:
        logger.warning("update_business_metrics 执行失败: %s", exc)
        raise self.retry(exc=exc)
