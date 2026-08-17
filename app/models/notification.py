"""
通知模型 — 存储用户收到的通知（点赞/评论/回复/收藏）

=== 面试重点 ===
Q: 通知为什么要持久化到 MySQL 而不是只靠 WebSocket 推送？
A: WebSocket 只能推给在线用户。用户离线时收到的通知必须存下来，
   下次登录后从数据库加载未读通知。这就是"推送+拉取"双通道。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True,
                                     comment="通知主键ID")
    recipient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, comment="接收通知的用户ID"
    )
    sender_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, comment="触发通知的用户ID"
    )
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="通知类型：like/comment/reply/favorite"
    )
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="通知内容")
    article_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("article.id", ondelete="CASCADE"), default=None, comment="关联文章ID"
    )
    comment_id: Mapped[int | None] = mapped_column(
        Integer, default=None, comment="关联评论ID（回复通知用）"
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已读")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="通知时间"
    )

    # 关联
    recipient = relationship("User", foreign_keys=[recipient_id], lazy="selectin")
    sender = relationship("User", foreign_keys=[sender_id], lazy="selectin")
