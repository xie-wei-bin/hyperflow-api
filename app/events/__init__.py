"""
事件总线 — 生产者/消费者抽象层

=== 面试重点 ===
Q: 为什么抽象一层而不是直接调 Redis Pub/Sub？
A: 面向接口编程。当前用 Redis 实现，未来换 Kafka 只改这一个文件——
   publish() 变成 kafka_producer.send()，subscribe() 变成 kafka_consumer.poll()。
   所有业务代码不感知底层是 Redis 还是 Kafka。

Q: 这和秒杀系统有什么关系？
A: 秒杀系统用 Kafka 做异步削峰：下单请求 → Kafka → 消费者慢慢处理。
   这个事件总线就是同一套模式——文章发布 → publish event → 多个消费者（通知/搜索/审核）各自处理。
   面试时讲"我用了生产者-消费者模式解耦"，Redis 和 Kafka 只是不同的 Transport。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from abc import ABC, abstractmethod
from typing import Any, Callable

from app.logger import logger


# ── 事件定义 ──────────────────────────────────────────


class Event:
    """事件基类"""

    def __init__(self, event_type: str, data: dict[str, Any]):
        self.event_type = event_type
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "data": self.data}


# ── 预定义事件类型 ──
class EventType:
    ARTICLE_PUBLISHED = "article.published"       # 文章发布 → 通知粉丝 + 同步 ES
    ARTICLE_UPDATED = "article.updated"           # 文章更新 → 清理缓存 + 重新索引
    COMMENT_CREATED = "comment.created"           # 评论创建 → AI 审核 + 通知作者
    COMMENT_APPROVED = "comment.approved"         # 评论审核通过 → 展示
    USER_REGISTERED = "user.registered"           # 用户注册 → 发欢迎邮件
    CACHE_INVALIDATED = "cache.invalidated"       # 缓存失效 → 重新预热


# ── 传输层接口 ────────────────────────────────────────


class EventTransport(ABC):
    """事件传输层抽象接口——Redis 和 Kafka 实现同一套接口"""

    @abstractmethod
    async def publish(self, topic: str, event: Event) -> None:
        """发布事件到指定主题"""

    @abstractmethod
    async def subscribe(
        self, topic: str, handler: Callable[[Event], Any]
    ) -> asyncio.Task[Any]:
        """订阅主题，收到事件后调用 handler"""

    @abstractmethod
    async def shutdown(self) -> None:
        """关闭传输层"""


# ── Redis 实现（当前） ─────────────────────────────────


class RedisEventTransport(EventTransport):
    """
    Redis Pub/Sub 事件传输

    面试点：Redis Pub/Sub 做事件总线——
    优点：低延迟、项目已有 Redis、零额外依赖
    缺点：不持久化、无消费者组、无消息回溯
    升级路径：替换为 KafkaEventTransport，接口不变
    """

    def __init__(self, redis):
        self._redis = redis
        self._subscribers: list[asyncio.Task[Any]] = []

    async def publish(self, topic: str, event: Event) -> None:
        """发布事件到 Redis Pub/Sub 频道"""
        try:
            channel = f"events:{topic}"
            await self._redis.publish(
                channel,
                json.dumps(event.to_dict(), default=str),
            )
            await logger.ainfo(
                "event.published",
                topic=topic,
                event_type=event.event_type,
            )
        except Exception:
            await logger.aerror(
                "event.publish_failed",
                topic=topic,
                event_type=event.event_type,
            )

    async def subscribe(
        self, topic: str, handler: Callable[[Event], Any]
    ) -> asyncio.Task[Any]:
        """订阅事件主题"""
        channel = f"events:{topic}"

        async def _listen():
            # 面试点：自动重连——Redis 断开后等待重试
            while True:
                try:
                    pubsub = self._redis.pubsub()
                    await pubsub.subscribe(channel)
                    await logger.ainfo("event.subscribed", topic=topic)

                    async for msg in pubsub.listen():
                        if msg["type"] != "message":
                            continue
                        try:
                            payload = json.loads(msg["data"])
                            event = Event(
                                event_type=payload["event_type"],
                                data=payload["data"],
                            )
                            # 面试点：handler 异常不中断监听循环
                            with contextlib.suppress(Exception):
                                await handler(event)
                        except Exception:
                            pass

                except asyncio.CancelledError:
                    break
                except Exception:
                    await logger.aerror("event.subscriber.error", topic=topic)
                    await asyncio.sleep(1)

        task = asyncio.create_task(_listen())
        self._subscribers.append(task)
        return task

    async def shutdown(self) -> None:
        for task in self._subscribers:
            task.cancel()
        self._subscribers.clear()
        await logger.ainfo("event.transport.shutdown")


# ── Kafka 接口预留 ─────────────────────────────────────


class KafkaEventTransport(EventTransport):
    """
    Kafka 事件传输（接口预留，待实施）

    面试点：当前用 Redis，未来切 Kafka 只需实现这个类。
    业务代码只依赖 EventTransport 接口，不感知底层实现。
    """

    def __init__(self, bootstrap_servers: str):
        self._servers = bootstrap_servers
        # 待实施：from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
        self._producer = None
        self._consumers: list[Any] = []

    async def publish(self, topic: str, event: Event) -> None:
        """发布到 Kafka Topic（待实施）"""
        raise NotImplementedError("Kafka transport 待实施")

    async def subscribe(
        self, topic: str, handler: Callable[[Event], Any]
    ) -> asyncio.Task[Any]:
        """订阅 Kafka Consumer Group（待实施）"""
        raise NotImplementedError("Kafka transport 待实施")

    async def shutdown(self) -> None:
        pass


# ── 事件总线门面 ───────────────────────────────────────


class EventBus:
    """
    事件总线门面——业务代码唯一入口

    使用：
        bus = EventBus(RedisEventTransport(redis))

        # 发布
        await bus.publish(EventType.ARTICLE_PUBLISHED, {"article_id": 1})

        # 订阅
        await bus.subscribe(EventType.ARTICLE_PUBLISHED, handler)
    """

    def __init__(self, transport: EventTransport):
        self._transport = transport

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        event = Event(event_type=event_type, data=data)
        await self._transport.publish(event_type, event)

    async def subscribe(
        self, event_type: str, handler: Callable[[Event], Any]
    ) -> asyncio.Task[Any]:
        return await self._transport.subscribe(event_type, handler)

    async def shutdown(self) -> None:
        await self._transport.shutdown()


# 模块级单例（lifespan 中初始化）
event_bus: EventBus | None = None


def get_event_bus() -> EventBus | None:
    return event_bus


async def init_event_bus(redis) -> EventBus:
    """在 lifespan 中调用，初始化事件总线"""
    global event_bus
    transport = RedisEventTransport(redis)
    event_bus = EventBus(transport)
    await logger.ainfo("event_bus.initialized", transport="Redis Pub/Sub")
    return event_bus
