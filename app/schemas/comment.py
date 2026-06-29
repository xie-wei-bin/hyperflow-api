"""评论相关 Schema"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    """创建评论请求"""

    content: str = Field(..., min_length=1, description="评论内容")


class CommentReply(BaseModel):
    """回复评论请求"""

    content: str = Field(..., min_length=1, description="回复内容")


class CommentItem(BaseModel):
    """评论项（支持树形结构嵌套）"""

    id: int
    content: str
    article_id: int
    user: dict[str, Any]
    parent_id: int | None = None
    is_deleted: bool = False
    created_at: datetime
    replies: list["CommentItem"] = []

    model_config = {"from_attributes": True}
