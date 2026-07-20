"""文章模型 + 文章-标签中间表"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.comment import Comment
    from app.models.like_favorite import Favorite, Like
    from app.models.tag import Tag
    from app.models.user import User


class Article(Base):
    __tablename__ = "article"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="文章主键ID"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="文章标题")
    slug: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, comment="URL别名，SEO友好"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="文章正文")
    summary: Mapped[str | None] = mapped_column(String(500), default=None, comment="文章摘要")
    cover_image: Mapped[str | None] = mapped_column(String(500), default=None, comment="封面图URL")
    status: Mapped[str] = mapped_column(
        Enum("draft", "published", name="article_status"),
        default="draft",
        comment="文章状态：draft草稿、published已发布",
    )
    view_count: Mapped[int] = mapped_column(Integer, default=0, comment="阅读量计数器")
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", comment="软删除标记"
    )
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, comment="作者用户ID"
    )
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("category.id"), default=None, comment="所属分类ID"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, comment="发布时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="最后更新时间"
    )

    # 表级约束和索引
    #Index(索引名, 字段1, 字段2, ..., 数据库专属参数)
    #func.match() 里面的字段列表，必须和这条 FULLTEXT 索引的字段完全一致、顺序一致
    #MySQL 原生全文中文分词弱，海量内容搜索建议 ES
    __table_args__ = (
        # FULLTEXT 全文索引：MySQL 倒排索引，搜索标题+正文，比 LIKE 快几十倍
        Index("ft_title_content", "title", "content", mysql_prefix="FULLTEXT"),
    )

    # 关联关系
    author: Mapped["User"] = relationship("User", back_populates="articles")
    category: Mapped["Category | None"] = relationship("Category", back_populates="articles")
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="article", lazy="selectin"
    )
    article_tags: Mapped[list["ArticleTag"]] = relationship(
        "ArticleTag", back_populates="article", lazy="selectin", cascade="all, delete-orphan"
    )
    tags: Mapped[list["Tag"]] = relationship(
        "Tag",
        secondary="article_tag",
        lazy="selectin",
        viewonly=True,#只读视图，禁止通过这个字段做增删改操作
    )
    likes: Mapped[list["Like"]] = relationship(
        "Like", back_populates="article", lazy="selectin", cascade="all, delete-orphan"
    )
    favorites: Mapped[list["Favorite"]] = relationship(
        "Favorite", back_populates="article", lazy="selectin", cascade="all, delete-orphan"
    )


class ArticleTag(Base):
    __tablename__ = "article_tag"
#两个 primary_key=True 已经构成了复合主键，自带唯一约束
#UniqueConstraint 是多余的
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("article.id", ondelete="CASCADE"), primary_key=True, comment="文章ID"
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True, comment="标签ID"
    )

    article: Mapped["Article"] = relationship("Article", back_populates="article_tags")
    tag: Mapped["Tag"] = relationship("Tag", back_populates="article_tags")
#文章和标签的配对保证唯一，同一篇文章不能多次打同一个标签。
    __table_args__ = (UniqueConstraint("article_id", "tag_id", name="uq_article_tag"),)
