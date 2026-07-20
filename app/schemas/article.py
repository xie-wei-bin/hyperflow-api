"""文章相关 Schema"""

from datetime import datetime

from pydantic import BaseModel, Field


# ── 内嵌模型：替代 dict[str, Any]，IDE 可自动补全，Swagger 生成完整文档 ──

class AuthorBrief(BaseModel):
    """文章内嵌的作者公开信息"""
    id: int
    username: str
    avatar: str | None = None

    model_config = {"from_attributes": True}


class CategoryBrief(BaseModel):
    """文章内嵌的分类信息"""
    id: int
    name: str

    model_config = {"from_attributes": True}


class TagBrief(BaseModel):
    """文章内嵌的标签信息"""
    id: int
    name: str

    model_config = {"from_attributes": True}


class ArticleCreate(BaseModel):
    """创建文章请求"""

    title: str = Field(..., min_length=1, max_length=200)#标题
    slug: str = Field(..., min_length=1, max_length=200)#链接别名
    content: str = Field(..., min_length=1)#正文
    summary: str | None = Field(None, max_length=500)#摘要
    cover_image: str | None = Field(None, max_length=500)#封面图地址
    status: str = Field(default="draft", pattern="^(draft|published)$")#文章状态，pattern 正则校验：只能传 draft / published，其他字符直接报参数错误
    category_id: int | None = None#分类 ID
    tag_ids: list[int] = Field(default_factory=list)#每次生成独立空列表，解决可变默认值共享污染问题，专门用于列表、字典这类可变类型。


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
    author: AuthorBrief | None = None
    category: CategoryBrief | None = None
    tags: list[TagBrief] = []
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
    author: AuthorBrief | None = None
    category: CategoryBrief | None = None
    tags: list[TagBrief] = []
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
