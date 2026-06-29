"""分类相关 Schema"""

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    """创建分类请求"""
    name: str = Field(..., min_length=1, max_length=50, description="分类名称")
    description: str | None = Field(None, max_length=200, description="分类描述")


class CategoryUpdate(BaseModel):
    """更新分类请求"""
    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=200)
