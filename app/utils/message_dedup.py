"""
消息幂等性 — 防止重复消费

=== 面试重点 ===
Q: 为什么消息队列需要幂等？
A: Kafka/RabbitMQ 的 at-least-once 语义 + 网络抖动 → 同一条消息可能投递多次。
   消费者必须做幂等——同一消息处理两次，结果不变，不会重复创建订单/发通知。

Q: 怎么实现？
A: ① Redis SET NX：已处理的消息 ID 记录在 Redis，消费前检查
   ② 数据库 UNIQUE 约束兜底（你的点赞系统的两层防护就是幂等）
   ③ 消息序号 seq：消费者只处理 seq > last_seq 的消息

Q: 这和博客项目有什么关系？
A: 博客的点赞系统用 Redis SISMEMBER + DB UNIQUE 实现幂等——
   同一用户对同一文章点两次赞，两次都返回"已点赞"，不重复计数。
   这和秒杀"同一用户不能重复下单"是完全一样的模式。
"""

from __future__ import annotations

import hashlib
import json


class MessageDedup:
    """
    消息去重器 — Redis SET NX 实现

    使用：
        dedup = MessageDedup(redis)
        if await dedup.is_duplicate("order:msg_abc123"):
            return  # 已处理过，跳过
        await process_order(msg)
        await dedup.mark_processed("order:msg_abc123", ttl=86400)
    """

    def __init__(self, redis):
        self._redis = redis

    async def is_duplicate(self, message_id: str) -> bool:
        """检查消息是否已处理（存在 = 重复）"""
        key = f"dedup:{message_id}"
        return bool(await self._redis.exists(key))

    async def mark_processed(self, message_id: str, ttl: int = 86400) -> None:
        """标记消息已处理（TTL 后自动清理，不无限膨胀）"""
        key = f"dedup:{message_id}"
        await self._redis.setex(key, ttl, "1")

    async def try_mark(self, message_id: str, ttl: int = 86400) -> bool:
        """
        原子检查+标记——SET NX 一条命令完成

        返回: True=首次处理 / False=重复消息
        面试点：SET NX 是原子操作，不需要先 GET 再 SET——那中间有窗口
        """
        key = f"dedup:{message_id}"
        result = await self._redis.set(key, "1", nx=True, ex=ttl)
        return bool(result)

    @staticmethod
    def generate_message_id(payload: dict, prefix: str = "msg") -> str:
        """根据消息内容生成唯一 ID（SHA256 哈希）"""
        raw = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"{prefix}:{digest}"
