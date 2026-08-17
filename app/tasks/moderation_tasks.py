"""
评论审核队列 — Celery 生产者-消费者模式 + 死信队列

=== 面试重点 ===
Q: 这跟秒杀系统有什么关系？
A: 秒杀系统用 MQ 做异步削峰：下单请求 → RabbitMQ/Kafka → 消费者慢慢处理订单。
   本模块是同一套模式——评论发布 → Celery Task（Broker: Redis） → 消费者异步审核。
   架构完全一样，只是业务场景从"下单"换成"评论审核"。

Q: 为什么用 Celery 而不是直接 await 审核？
A: ① 解耦：评论接口立刻返回 201，审核结果异步回调
   ② 削峰：大量评论不会阻塞 API 主线程
   ③ 重试：审核临时失败（网络抖动）自动重试 3 次
   ④ 死信：3 次全部失败 → 转人工审核队列，不丢数据

Q: 这对应 RabbitMQ 的哪些概念？
A: task.delay() = Producer → Exchange
   Redis Broker   = Queue
   Celery Worker  = Consumer + ack
   max_retries=3  = DLX (Dead Letter Exchange)
   重试指数退避    = retry_backoff
"""

import random
import time

from app.celery_app import celery_app
from app.logger import logger


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,  # 首次重试等 10 秒
    autoretry_for=(Exception,),
    retry_backoff=True,       # 指数退避：10s → 20s → 40s
    retry_backoff_max=120,    # 最多等 120 秒
    acks_late=True,           # 执行完成后才 ack → 中途崩溃消息不丢
)
def moderate_comment(self, comment_id: int, content: str, user_id: int):
    """
    审核单条评论

    面试点：Celery 任务的 bind=True → self 参数是任务实例，可用 self.retry() 手动重试。
    acks_late=True → 任务执行完才发 ack，Worker 崩溃消息回到队列重新投递。

    实际审核逻辑：
    1. 敏感词过滤（正则匹配）
    2. AI 审核（调 LLM API 判断是否违规）
    3. 更新评论状态（通过/拒绝/人工复审）
    """
    try:
        # ── 模拟审核逻辑（生产环境替换为真实 AI 审核） ──
        logger.info(
            "moderation.started",
            comment_id=comment_id,
            user_id=user_id,
            content_preview=content[:50],
        )

        # ① 敏感词快速过滤（不调 AI，省成本）
        spam_keywords = ["赌博", "色情", "广告推广", "代办证件"]
        for keyword in spam_keywords:
            if keyword in content:
                logger.info(
                    "moderation.rejected",
                    comment_id=comment_id,
                    reason=f"命中敏感词: {keyword}",
                )
                _update_comment_status(comment_id, "rejected", f"命中敏感词: {keyword}")
                return {"comment_id": comment_id, "result": "rejected", "reason": keyword}

        # ② AI 审核（模拟——生产换 LLM API）
        # 模拟 5% 概率临时失败（触发重试）
        if random.random() < 0.05:
            raise Exception("AI 审核服务临时不可用")

        # 模拟审核延迟
        time.sleep(0.5)

        # ③ 审核通过
        logger.info("moderation.approved", comment_id=comment_id)
        _update_comment_status(comment_id, "approved", "")
        return {"comment_id": comment_id, "result": "approved"}

    except Exception as exc:
        logger.error(
            "moderation.retry",
            comment_id=comment_id,
            retry_count=self.request.retries,
            error=str(exc),
        )
        # 面试点：手动重试——超过 max_retries 后不再重试，进入死信处理
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        else:
            # 死信：3 次重试全失败 → 转人工审核
            _send_to_dead_letter(comment_id, content, user_id)
            return {"comment_id": comment_id, "result": "dead_letter"}


def _update_comment_status(comment_id: int, status: str, reason: str):
    """
    更新评论审核状态（生产环境调数据库）

    面试点：审核结果异步回写——与 API 主流程解耦。
    即使审核需要 5 秒，用户发评论的接口 50ms 就返回了。
    """
    # 生产环境：通过 SQLAlchemy 异步更新 comment 表
    # await db.execute(update(Comment).where(Comment.id == comment_id).values(...))
    logger.info("comment.status_updated", comment_id=comment_id, status=status)


def _send_to_dead_letter(comment_id: int, content: str, user_id: int):
    """
    死信队列——3 次重试全失败，转人工处理

    面试点：这就是 RabbitMQ 的 DLX（Dead Letter Exchange）。
    自动重试耗尽后不丢数据，转入人工复审队列。
    """
    logger.warning(
        "moderation.dead_letter",
        comment_id=comment_id,
        user_id=user_id,
        content=content[:100],
        message="自动审核失败 3 次，转入人工复审队列",
    )
    # 生产环境：写入 dead_letter 表或发钉钉/飞书通知管理员


# ── 便捷调用 ──


def submit_comment_for_moderation(comment_id: int, content: str, user_id: int):
    """
    提交评论审核（生产者）

    用法（在 router 中）：
        submit_comment_for_moderation(comment.id, comment.content, comment.user_id)

    面试点：.delay() 就是生产者——把任务发送到 Redis Broker，
    Celery Worker 异步消费。API 路由立刻返回，不等待审核结果。
    这就是 MQ 削峰——不管评论量多大，API 响应时间恒定。
    """
    moderate_comment.delay(
        comment_id=comment_id,
        content=content,
        user_id=user_id,
    )
