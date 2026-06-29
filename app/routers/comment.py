"""
评论路由 — 树形结构评论 + 回复

面试点：评论路由的 prefix 是 /api 而非 /api/comments，
因为有些端点挂在 /api/articles/{id}/comments 下面（RESTful 嵌套资源风格）
"""

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException, NotFoundException
from app.middleware.auth import get_current_user
from app.models.comment import Comment
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.comment import CommentCreate, CommentReply
from app.schemas.common import APIResponse
from app.services import comment as comment_service

router = APIRouter(prefix="/api", tags=["评论"])


@router.get("/articles/{article_id}/comments", response_model=APIResponse[list[dict]])
async def get_comments(article_id: int, db: AsyncSession = Depends(get_db)):
    """文章评论列表（树形结构）"""
    from app.models.article import Article

    article = await db.get(Article, article_id)
    if not article:
        raise NotFoundException("文章不存在")
    comments = await comment_service.get_article_comments(db, article_id)
    return APIResponse(data=comments)


@router.post("/articles/{article_id}/comments", status_code=201, response_model=APIResponse[dict])
async def create_comment(
    article_id: int,
    data: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """发表评论（需认证）— 同时加分到热门排行（评论权重 ×5）"""
    from app.models.article import Article

    article = await db.get(Article, article_id)
    if not article:
        raise NotFoundException("文章不存在")
    comment = await comment_service.create_comment(db, article_id, current_user.id, data.content)
    await redis.zincrby("blog:article:hot", 5, str(article_id))  # 评论权重最高
    return APIResponse(
        code=201,
        message="评论发表成功",
        data={
            "id": comment.id,
            "content": comment.content,
            "article_id": comment.article_id,
            "user": {
                "id": comment.user.id,
                "username": comment.user.username,
                "avatar": comment.user.avatar,
            }
            if comment.user
            else {},
            "parent_id": None,
            "created_at": comment.created_at.isoformat(),
        },
    )


@router.post("/comments/{comment_id}/reply", status_code=201, response_model=APIResponse[dict])
async def reply_comment(
    comment_id: int,
    data: CommentReply,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """回复评论（需认证）"""
    comment = await comment_service.reply_comment(db, comment_id, current_user.id, data.content)
    return APIResponse(
        code=201,
        message="回复成功",
        data={
            "id": comment.id,
            "content": comment.content,
            "article_id": comment.article_id,
            "parent_id": comment.parent_id,
            "user": {
                "id": comment.user.id,
                "username": comment.user.username,
                "avatar": comment.user.avatar,
            }
            if comment.user
            else {},
            "created_at": comment.created_at.isoformat(),
        },
    )


@router.delete("/comments/{comment_id}", response_model=APIResponse[dict])
async def delete_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除评论 — 软删除（作者本人或管理员）"""
    result = await db.execute(select(Comment).where(Comment.id == comment_id))
    comment = result.scalar_one_or_none()
    if not comment:
        raise NotFoundException("评论不存在")
    if comment.user_id != current_user.id and current_user.role != "admin":
        raise ForbiddenException("无权删除此评论")
    comment.is_deleted = True
    comment.content = "该评论已被删除"
    await db.flush()
    return APIResponse(message="评论已删除")
