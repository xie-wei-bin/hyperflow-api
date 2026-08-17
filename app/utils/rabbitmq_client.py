"""
RabbitMQ 异步客户端 — 评论审核队列

=== 面试重点 ===
Q: RabbitMQ 和 Redis Pub/Sub 有什么区别？
A: Redis Pub/Sub 是"即发即忘"——没有消费者就丢了，没有 ack、没有 DLX。
   RabbitMQ 是"可靠投递"——Exchange 路由 → Queue 持久化 → Consumer ack →
   失败 nack → DLX 死信队列 → 人工处理。消息不会凭空消失。

Q: Exchange 三种类型？
A: direct（routing_key 精确匹配）→ 评论审核：comment.new → moderation_queue
   topic（routing_key 模糊匹配 *.news #.all）→ 通知分发
   fanout（广播所有队列）→ 缓存失效

Q: 为什么不用 Redis 替代 RabbitMQ？
A: Redis Stream + Consumer Group 可以替代部分 RabbitMQ 功能（ack、消费者组），
   但 RabbitMQ 的 Exchange 路由、DLX 死信、消息 TTL、优先级队列——这些 Redis 没有。
   选型：消息不能丢 → RabbitMQ。消息可以丢（通知/缓存）→ Redis Pub/Sub 够用。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Callable

from app.logger import logger


# ── RabbitMQ 连接管理 ──────────────────────────────────


class RabbitMQClient:
    """
    RabbitMQ 异步客户端（基于 aio_pika）

    安装: pip install aio-pika

    使用:
        rabbit = RabbitMQClient("amqp://guest:guest@localhost/")
        await rabbit.connect()

        # 声明 Exchange + Queue + Binding
        exchange = await rabbit.declare_exchange("blog.events", "topic")
        queue = await rabbit.declare_queue(
            "comment.moderation",
            dlx_exchange="blog.dlx",     # 死信 Exchange
            dlx_routing_key="comment.dead",  # 死信 routing_key
        )
        await queue.bind(exchange, "comment.created")

        # 生产者
        await rabbit.publish("blog.events", "comment.created", {"comment_id": 1})

        # 消费者
        await rabbit.consume("comment.moderation", handle_comment)
    """

    def __init__(self, url: str = "amqp://guest:guest@localhost/"):
        self._url = url
        self._connection = None
        self._channel = None
        self._exchanges: dict[str, Any] = {}
        self._queues: dict[str, Any] = {}
        self._consumer_tasks: list[asyncio.Task[Any]] = []

    async def connect(self) -> None:
        """建立连接 + Channel（生产环境用连接池）"""
        try:
            import aio_pika

            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel()
            # 面试点：prefetch_count=10 → 每个消费者最多同时处理 10 条未确认消息
            # 防止某个消费者被慢任务拖垮，其他消息可以分发给空闲消费者
            await self._channel.set_qos(prefetch_count=10)
            await logger.ainfo("rabbitmq.connected", url=self._url)
        except ImportError:
            await logger.aerror(
                "rabbitmq.no_driver",
                message="请安装 aio-pika: pip install aio-pika",
            )
            raise
        except Exception as e:
            await logger.aerror("rabbitmq.connect_failed", error=str(e))
            raise

    async def declare_exchange(
        self,
        name: str,
        type_: str = "topic",  # direct / topic / fanout
        durable: bool = True,
    ):
        """
        声明 Exchange

        面试点：
        - durable=True：Exchange 持久化到磁盘，RabbitMQ 重启不丢
        - 类型选择：direct=精确匹配 / topic=模糊匹配 / fanout=广播
        """
        from aio_pika import ExchangeType

        type_map = {
            "direct": ExchangeType.DIRECT,
            "topic": ExchangeType.TOPIC,
            "fanout": ExchangeType.FANOUT,
        }
        exchange = await self._channel.declare_exchange(
            name,
            type=type_map.get(type_, ExchangeType.TOPIC),
            durable=durable,
        )
        self._exchanges[name] = exchange
        await logger.ainfo("rabbitmq.exchange_declared", name=name, type=type_)
        return exchange

    async def declare_queue(
        self,
        name: str,
        durable: bool = True,
        dlx_exchange: str | None = None,
        dlx_routing_key: str | None = None,
        message_ttl: int | None = None,  # 消息过期时间（毫秒）
    ):
        """
        声明 Queue

        面试点：DLX（Dead Letter Exchange）——消息被 nack/reject 或 TTL 过期后，
        不会直接丢弃，而是路由到指定的死信 Exchange。
        死信队列 = 人工复审的"兜底"——审核 3 次失败 → DLX → 管理员手动处理。
        """
        from aio_pika import Queue

        arguments = {}
        if dlx_exchange:
            arguments["x-dead-letter-exchange"] = dlx_exchange
        if dlx_routing_key:
            arguments["x-dead-letter-routing-key"] = dlx_routing_key
        if message_ttl:
            arguments["x-message-ttl"] = message_ttl

        queue = await self._channel.declare_queue(
            name,
            durable=durable,
            arguments=arguments,
        )
        self._queues[name] = queue
        await logger.ainfo(
            "rabbitmq.queue_declared",
            name=name,
            dlx=dlx_exchange,
        )
        return queue

    async def publish(
        self,
        exchange_name: str,
        routing_key: str,
        data: dict,
        persistent: bool = True,
    ) -> None:
        """
        发布消息到 Exchange（生产者）

        面试点：delivery_mode=2（persistent）→ 消息持久化到磁盘。
        RabbitMQ 重启后消息还在。配合 durable Exchange + durable Queue，
        三层持久化——消息不会因为重启丢失。
        """
        from aio_pika import DeliveryMode, Message

        exchange = self._exchanges.get(exchange_name)
        if exchange is None:
            raise ValueError(f"Exchange 未声明: {exchange_name}")

        body = json.dumps(data, default=str).encode()
        message = Message(
            body,
            delivery_mode=DeliveryMode.PERSISTENT if persistent else DeliveryMode.NOT_PERSISTENT,
            content_type="application/json",
        )
        await exchange.publish(message, routing_key=routing_key)
        await logger.ainfo(
            "rabbitmq.published",
            exchange=exchange_name,
            routing_key=routing_key,
        )

    async def consume(
        self,
        queue_name: str,
        handler: Callable[[dict], Any],
    ) -> asyncio.Task[Any]:
        """
        消费队列消息（消费者）

        面试点：手动 ack——消费者处理成功后调 message.ack()，失败调 message.nack(requeue=True)。
        自动 ack（auto_ack=True）是危险的——消息从队列删除后才处理，处理失败消息丢了。
        手动 ack 保证"处理成功才删，失败重新入队或进 DLX"。
        """
        queue = self._queues.get(queue_name)
        if queue is None:
            raise ValueError(f"Queue 未声明: {queue_name}")

        async def _consumer():
            async with queue.iterator() as iterator:
                async for message in iterator:
                    async with message.process(requeue=True):
                        # message.process() 上下文：
                        #   正常退出 → auto ack
                        #   异常退出 → nack + requeue
                        try:
                            body = json.loads(message.body.decode())
                            await handler(body)
                        except Exception as e:
                            await logger.aerror(
                                "rabbitmq.handler_failed",
                                queue=queue_name,
                                error=str(e),
                            )
                            raise  # 触发 nack + requeue

        task = asyncio.create_task(_consumer())
        self._consumer_tasks.append(task)
        return task

    async def close(self) -> None:
        """关闭连接"""
        for task in self._consumer_tasks:
            task.cancel()
        if self._connection:
            await self._connection.close()
        await logger.ainfo("rabbitmq.closed")


# ── 博客评论审核：完整 RabbitMQ 架构 ──────────────────


async def setup_comment_moderation_pipeline(
    rabbit: RabbitMQClient,
) -> tuple[Any, Any]:
    """
    评论审核管道（Exchange + DLX + Queue 完整配置）

    Exchange 拓扑:
      blog.events (topic)
        ├── comment.created → comment.moderation.queue（审核队列）
        │                      │ nack/reject → blog.dlx (topic)
        │                      │                └── comment.dead → comment.dead.queue（死信）
        └── comment.approved → comment.notification.queue（通知队列）

    面试点：一条评论的完整生命周期——
    ① 用户发评论 → publish("blog.events", "comment.created")
    ② Topic Exchange 路由到 comment.moderation.queue
    ③ 消费者审核 → 通过则 ack + publish("blog.events", "comment.approved")
    ④ 审核失败 nack → DLX → comment.dead.queue → 人工复审
    """
    # ① 声明 Exchange
    events_exchange = await rabbit.declare_exchange("blog.events", "topic")
    dlx_exchange = await rabbit.declare_exchange("blog.dlx", "topic")

    # ② 声明死信队列
    dead_queue = await rabbit.declare_queue("comment.dead.queue")
    await dead_queue.bind(dlx_exchange, "comment.dead")

    # ③ 声明审核队列（绑定 DLX）
    moderation_queue = await rabbit.declare_queue(
        "comment.moderation.queue",
        dlx_exchange="blog.dlx",
        dlx_routing_key="comment.dead",
        message_ttl=300_000,  # 消息 5 分钟过期 → DLX
    )
    await moderation_queue.bind(events_exchange, "comment.created")

    # ④ 声明通知队列
    notification_queue = await rabbit.declare_queue("comment.notification.queue")
    await notification_queue.bind(events_exchange, "comment.approved")

    await logger.ainfo("rabbitmq.comment_pipeline.setup_complete")
    return events_exchange, moderation_queue


# ── 评论审核消费者 ─────────────────────────────────────


async def comment_moderation_handler(message: dict) -> None:
    """
    审核单条评论——被 RabbitMQ consume 调用

    面试点：这是 RabbitMQ 消费者——和 Celery Worker 是同一套"生产者-消费者"理念。
    区别：Celery 用 Redis 做 Broker，这里用 RabbitMQ 的 Exchange+Queue+DLX。
    选型依据：消息不能丢 → RabbitMQ。任务可以重试、允许少量丢失 → Celery+Redis 更轻量。
    """
    comment_id = message.get("comment_id")
    content = message.get("content", "")

    logger.info("rabbitmq.moderation.received", comment_id=comment_id)

    # 敏感词过滤（不调 AI，省成本）
    spam_keywords = ["赌博", "色情", "广告推广"]
    for keyword in spam_keywords:
        if keyword in content:
            logger.info("rabbitmq.moderation.rejected", comment_id=comment_id, reason=keyword)
            # 拒绝 → ack（不进 DLX，拒绝是正常结果非异常）
            # 如果拒绝后需要通知用户 → publish("blog.events", "comment.rejected")
            return

    # 模拟 AI 审核（生产换 LLM API）
    await asyncio.sleep(0.5)

    # 通过 → ack + 发布 "comment.approved" 事件
    logger.info("rabbitmq.moderation.approved", comment_id=comment_id)
