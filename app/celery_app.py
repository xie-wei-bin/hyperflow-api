"""
Celery 异步任务队列 — 配置与实例（Worker + Beat 定时调度）

=== 面试重点 ===
Q: 为什么用 Celery 而不是 asyncio.create_task？
A: 二者使用场景不同：
   - asyncio.create_task：轻量后台任务，和主进程同生命周期，适合"扔后台不管"
   - Celery Worker：重量级异步任务（发邮件），独立进程，支持重试/超时/优先级
   - Celery Beat：分布式定时调度，替代 while True + sleep，支持 crontab 表达式

Q: 什么时候选 Celery Beat 而不是 while True + sleep？
A: 三个信号：
   1. 多实例部署 → sleep 会导致每个实例都执行，数据重复
   2. 需要 cron 表达式 → "每天凌晨 3 点" 用 sleep 算偏移很脆弱
   3. 需要可观测 → Celery Flower 可视化面板，sleep 只能看日志

Q: Beat 和 Worker 的关系？
A: Beat 是调度器（只发指令不干活），Worker 是执行器（只干活不调度）。
   Beat 按时把任务消息丢进 Redis → Worker 从 Redis 取任务执行。
   所以至少需要两个进程：celery beat + celery worker。

Q: 怎么启动？
A:
   # Worker（执行任务）
   celery -A app.celery_app worker --loglevel=info --concurrency=4
   # Beat（定时调度）
   celery -A app.celery_app beat --loglevel=info
   # 或者一条命令同时启动（仅开发环境）
   celery -A app.celery_app worker --beat --loglevel=info --concurrency=4
"""

from datetime import timedelta
from urllib.parse import urlparse, urlunparse

from celery import Celery
from celery.schedules import crontab

from app.config import settings


def _swap_redis_db(url: str, db: int) -> str:
    """安全替换 Redis URL 中的数据库编号，不依赖字符串匹配"""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{db}"))


# 面试点：Redis 作为 Broker（消息队列）+ Backend（结果存储）
# broker 用 db=1，result_backend 用 db=2，和应用缓存 db=0 隔离
celery_app = Celery(
    "blog_system",
    broker_url=_swap_redis_db(settings.REDIS_URL, 1),
    result_backend=_swap_redis_db(settings.REDIS_URL, 2),
    # 面试点：include 显式声明任务模块，比 autodiscover 更可控
    include=[
        "app.tasks.email_tasks",
        "app.tasks.scheduled_tasks",
    ],
)

# 面试点：Celery 配置项详解
celery_app.conf.update(
    # 任务序列化 — json 比 pickle 安全（pickle 可执行任意代码）
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务确认机制 — 任务执行完才从队列删除
    task_acks_late=True,
    # Worker 预取数量 — 设为 1 保证公平调度
    worker_prefetch_multiplier=1,
    # 任务超时
    task_soft_time_limit=120,  # ZSet 全量修正可能耗时较长
    task_time_limit=180,
    # 结果过期时间
    result_expires=3600,
    # 重试策略
    task_default_retry_delay=60,
    task_max_retries=3,
)

# ── Celery Beat 定时调度配置 ─────────────────
# 面试点：这是"企业级定时任务"的核心——用 crontab 表达式替代 while True + sleep
# schedule 支持三种格式：
#   timedelta(seconds=300) — 每 N 秒
#   crontab(minute='*/5')  — cron 表达式
#   300.0                   — 整数秒数（等同于 timedelta）
celery_app.conf.beat_schedule = {
    # ① 阅读量回写：每 5 分钟
    "sync-view-counts-every-5min": {
        "task": "scheduled.sync_view_counts",
        "schedule": timedelta(seconds=300),
        "options": {"queue": "periodic"},
    },
    # ② ZSet 脏数据清理：每 5 分钟（错开 2 分钟，避免和回写同时执行）
    "cleanup-zset-every-5min": {
        "task": "scheduled.cleanup_zset",
        "schedule": timedelta(seconds=320),
        "options": {"queue": "periodic"},
    },
    # ③ ZSet 全量修正：每天凌晨 3 点
    "repair-zset-daily-3am": {
        "task": "scheduled.repair_zset",
        "schedule": crontab(minute=0, hour=3),
        "options": {"queue": "periodic"},
    },
    # ④ ZSet 冷启动预热：每 30 分钟
    "warmup-zset-every-30min": {
        "task": "scheduled.warmup_zset",
        "schedule": timedelta(seconds=1800),
        "options": {"queue": "periodic"},
    },
    # ⑤ Prometheus 业务指标更新：每 1 分钟
    "update-business-metrics-every-1min": {
        "task": "scheduled.update_business_metrics",
        "schedule": timedelta(seconds=60),
        "options": {"queue": "periodic"},
    },
}

# 面试点：beat_schedule 里 queue 字段的作用？
# 默认所有任务进 celery 队列（和邮件任务混在一起）。
# 指定 queue="periodic" 后，可以启动专用 Worker 只消费定时任务：
#   celery -A app.celery_app worker -Q periodic --concurrency=2
# 这样邮件 Worker 不会被定时任务阻塞，定时任务也不会被邮件高峰期影响。
