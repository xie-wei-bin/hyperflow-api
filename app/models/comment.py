"""评论模型 — 支持树形结构（parent_id自关联）"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.user import User


class Comment(Base):
    __tablename__ = "comment"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="评论主键ID"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="评论内容")
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("article.id", ondelete="CASCADE"), nullable=False, comment="所属文章ID"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, comment="评论用户ID"
    )
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("comment.id"), default=None, comment="父评论ID，NULL表示顶级评论"
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, comment="软删除标记")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="评论时间"
    )

    # 关联关系
    article: Mapped["Article"] = relationship("Article", back_populates="comments")
    user: Mapped["User"] = relationship("User", back_populates="comments")
    parent: Mapped["Comment | None"] = relationship(
        "Comment", remote_side=[id], back_populates="replies"
    )
    replies: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="parent", lazy="selectin"
    )
