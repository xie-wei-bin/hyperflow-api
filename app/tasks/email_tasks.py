"""
邮件通知异步任务 — Celery 异步发送

=== 面试重点 ===
Q: 为什么不直接在请求里发邮件？
A: 发邮件需要连 SMTP 服务器，可能耗时 3-10 秒。
   如果在请求里同步发邮件 → 用户提交评论后浏览器转圈 5 秒才返回。
   用 Celery 异步 → API 立刻返回 201，邮件后台慢慢发。

Q: 邮件发送失败怎么办？
A: Celery 的 autoretry_for 自动重试：
   1. 第 1 次失败 → 60 秒后重试
   2. 第 2 次失败 → 120 秒后重试
   3. 第 3 次失败 → 240 秒后重试
   3 次全部失败 → 记录错误日志，人工介入。
   这就是 Celery 对比 asyncio.create_task 的核心优势之一。

Q: 为什么不真的连 SMTP？
A: 本项目是演示用途，开发环境不发真邮件。
   实际生产环境只需要替换 _send_email 函数的实现：
   import aiosmtplib
   await aiosmtplib.send(message, hostname="smtp.company.com", port=587)
   Celery 任务签名不变，调用方无感知。
"""

import asyncio
import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from app.celery_app import celery_app

logger = logging.getLogger("celery.email")


def _simulate_send_email(to_email: str, subject: str, body: str) -> bool:
    """
    模拟邮件发送（开发环境）

    面试点：这个函数在生产环境替换为真实的 SMTP 调用。
    Python 生态常用 aiosmtplib 做异步 SMTP，配合 email.mime 构建邮件。
    返回 True 表示发送成功。
    """
    # 模拟 0.2-1.5 秒的 SMTP 延迟
    import random
    import time

    delay = random.uniform(0.2, 1.5)
    time.sleep(delay)

    # 模拟 5% 失败率（测试重试机制）
    if random.random() < 0.05:
        raise ConnectionError("模拟 SMTP 连接超时")

    logger.info(
        "邮件发送成功: to=%s, subject=%s, delay=%.2fs",
        to_email, subject, delay,
    )
    return True


@celery_app.task(
    name="email.send_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
)
def send_email_notification(
    self,
    to_email: str,
    subject: str,
    body: str,
):
    """
    发送邮件通知（Celery 异步任务）

    面试点：bind=True 让任务可以访问 self（任务实例），
    self.retry() 是 Celery 内置的重试方法，延迟指数退避。

    面试点：autoretry_for 自动捕获指定异常并重试，不用每个 except 块里写 self.retry()。
    注意不要对业务异常重试（如"邮箱不存在"），只对临时故障重试（如连接超时）。

    调用方式（在路由里）：
        send_email_notification.delay(
            to_email="user@example.com",
            subject="有人评论了你的文章",
            body="..."
        )
    .delay() 是 Celery 的异步调用语法，等同于 .apply_async()。
    """
    try:
        _simulate_send_email(to_email, subject, body)
        return {"status": "ok", "to": to_email}
    except (ConnectionError, TimeoutError, OSError) as exc:
        # 面试点：exc=exc 传递原始异常，Worker 日志会记录完整堆栈
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            logger.error(
                "邮件发送最终失败（已重试 %d 次）: to=%s, subject=%s",
                self.max_retries, to_email, subject,
            )
            return {"status": "failed", "to": to_email, "reason": str(exc)}
