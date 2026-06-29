"""标签模型"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.article import ArticleTag


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="标签主键ID"
    )
    name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, comment="标签名称")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )

    # 多对多：通过中间表 article_tag 关联文章
    article_tags: Mapped[list["ArticleTag"]] = relationship(
        "ArticleTag", back_populates="tag", lazy="selectin"
    )
