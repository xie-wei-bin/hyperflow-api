"""评论模型 — 支持树形结构（parent_id自关联）
自关联 = 一张表搞定无限层级嵌套"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, func
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
    )#parent_id 指向本表主键 comment.id，顶层一级评论此字段为空。
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("comment.id"), default=None, comment="父评论ID，NULL表示顶级评论"
    )
    depth: Mapped[int] = mapped_column(
        Integer, default=0, comment="评论层级：0=顶级，1=一级回复，2=二级回复"
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, comment="软删除标记")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="评论时间"
    )

    # 联合索引：覆盖"某篇文章的所有评论"和"某条评论的所有子回复"两个高频查询
    __table_args__ = (Index("ix_comment_article_parent", "article_id", "parent_id"),)

    # 关联关系
    article: Mapped["Article"] = relationship("Article", back_populates="comments")
    user: Mapped["User"] = relationship("User", back_populates="comments")
    parent: Mapped["Comment | None"] = relationship(
        "Comment", remote_side=[id], back_populates="replies"
    )#relationship 会自动找：本表指向自身的外键字段
    #自动把 parent_id 定为本次关联的 local 本地字段
    #同一张表自己关联自己，SQLAlchemy 分不清谁是本地、谁是远端，必须用 remote_side 区分
    #remote_side=[id] 指定「远端匹配字段 remote」多对一才需要写
    replies: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="parent", lazy="selectin"
    )
