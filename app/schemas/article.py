"""文章相关 Schema"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ArticleCreate(BaseModel):
    """创建文章请求"""

    title: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    summary: str | None = Field(None, max_length=500)
    cover_image: str | None = Field(None, max_length=500)
    status: str = Field(default="draft", pattern="^(draft|published)$")
    category_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list)


class ArticleUpdate(BaseModel):
    """更新文章请求"""

    title: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=200)
    content: str | None = None
    summary: str | None = Field(None, max_length=500)
    cover_image: str | None = Field(None, max_length=500)
    status: str | None = Field(None, pattern="^(draft|published)$")
    category_id: int | None = None
    tag_ids: list[int] | None = None


class ArticleListItem(BaseModel):
    """文章列表项（不含正文）"""

    id: int
    title: str
    slug: str
    summary: str | None = None
    cover_image: str | None = None
    status: str
    view_count: int
    like_count: int = 0
    comment_count: int = 0
    author: dict[str, Any]
    category: dict[str, Any] | None = None
    tags: list[dict[str, Any]] = []
    published_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ArticleDetail(BaseModel):
    """文章详情（含正文）"""

    id: int
    title: str
    slug: str
    content: str
    summary: str | None = None
    cover_image: str | None = None
    status: str
    view_count: int
    like_count: int = 0
    favorite_count: int = 0
    author: dict[str, Any]
    category: dict[str, Any] | None = None
    tags: list[dict[str, Any]] = []
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ArticleListQuery(BaseModel):
    """文章列表查询参数"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    category_id: int | None = None
    tag_id: int | None = None
    status: str | None = Field(None, pattern="^(draft|published)$")
    search: str | None = None
    sort_by: str = Field(default="created_at", pattern="^(created_at|view_count|published_at)$")
    order: str = Field(default="desc", pattern="^(asc|desc)$")
