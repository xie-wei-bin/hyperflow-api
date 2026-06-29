"""用户模型"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.comment import Comment
    from app.models.like_favorite import Favorite, Like


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="用户主键ID"
    )
    username: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="登录用户名"
    )
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, comment="邮箱地址")
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="bcrypt密码哈希值"
    )
    avatar: Mapped[str | None] = mapped_column(String(500), default=None, comment="头像URL")
    role: Mapped[str] = mapped_column(
        Enum("user", "admin", name="user_role"),
        default="user",
        comment="角色：user普通用户、admin管理员",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="账号是否激活")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="注册时间"
    )

    # 关联关系
    articles: Mapped[list["Article"]] = relationship(
        "Article", back_populates="author", lazy="selectin"
    )
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="user", lazy="selectin"
    )
    likes: Mapped[list["Like"]] = relationship("Like", back_populates="user", lazy="selectin")
    favorites: Mapped[list["Favorite"]] = relationship(
        "Favorite", back_populates="user", lazy="selectin"
    )
