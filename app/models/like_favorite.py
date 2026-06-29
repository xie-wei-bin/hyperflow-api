"""点赞 + 收藏模型"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.user import User


class Like(Base):
    __tablename__ = "like"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="点赞主键ID"
    )
    article_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("article.id", ondelete="CASCADE"),
        nullable=False,
        comment="被点赞文章ID",
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, comment="点赞用户ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="点赞时间"
    )

    article: Mapped["Article"] = relationship("Article", back_populates="likes")
    user: Mapped["User"] = relationship("User", back_populates="likes")

    __table_args__ = (UniqueConstraint("article_id", "user_id", name="uq_article_user_like"),)


class Favorite(Base):
    __tablename__ = "favorite"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="收藏主键ID"
    )
    article_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("article.id", ondelete="CASCADE"),
        nullable=False,
        comment="被收藏文章ID",
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, comment="收藏用户ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="收藏时间"
    )

    article: Mapped["Article"] = relationship("Article", back_populates="favorites")
    user: Mapped["User"] = relationship("User", back_populates="favorites")

    __table_args__ = (UniqueConstraint("article_id", "user_id", name="uq_article_user_favorite"),)
