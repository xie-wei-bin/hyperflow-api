"""分类模型"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.article import Article


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="分类主键ID"
    )
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, comment="分类名称")
    description: Mapped[str | None] = mapped_column(String(200), default=None, comment="分类描述")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )

    # 一对多：一个分类下有多篇文章
    articles: Mapped[list["Article"]] = relationship(
        "Article", back_populates="category", lazy="selectin"
    )
