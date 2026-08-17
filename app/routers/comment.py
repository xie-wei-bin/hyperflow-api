"""
评论路由 — 树形结构评论 + 回复

面试点：评论路由的 prefix 是 /api 而非 /api/comments，
因为有些端点挂在 /api/articles/{id}/comments 下面（RESTful 嵌套资源风格）
"""

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.exceptions import ForbiddenException, NotFoundException
from app.middleware.auth import get_current_user, require_permission
from app.models.comment import Comment
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.comment import CommentCreate, CommentReply
from app.schemas.common import APIResponse
from app.services import comment as comment_service

router = APIRouter(prefix="/api", tags=["评论"])


@router.get("/articles/{article_id}/comments", response_model=APIResponse[list[dict]])
async def get_comments(
    article_id: int,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """文章评论列表（树形结构）— Redis 缓存 5 分钟"""
    import json

    from app.models.article import Article

    article = await db.get(Article, article_id)
    if not article:
        raise NotFoundException("文章不存在")

    # Cache-Aside：先查 Redis → miss → 查 DB → 写缓存
    cache_key = f"blog:comments:{article_id}"
    cached = await redis.get(cache_key)
    if cached:
        return APIResponse(data=json.loads(cached))

    comments = await comment_service.get_article_comments(db, article_id)
    await redis.setex(cache_key, 300, json.dumps(comments, default=str))
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
    await redis.zincrby("blog:article:hot", settings.HOT_RANK_COMMENT_WEIGHT, str(article_id))
    await redis.delete(f"blog:comments:{article_id}")  # 新评论 → 清缓存

    # ── WebSocket 实时通知：评论文章 → 通知文章作者 ──
    from app.routers.ws_notify import push_notification

    await push_notification(
        db, article.author_id, current_user.id, "comment",
        f"{current_user.username} 评论了你的文章《{article.title}》",
        article_id=article.id,
    )

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
    """回复评论（需认证）— 并推送通知给被回复者"""
    comment = await comment_service.reply_comment(db, comment_id, current_user.id, data.content)

    # ── WebSocket 实时通知：回复评论 → 通知被回复的人 ──
    from app.routers.ws_notify import push_notification

    if comment.parent_id:
        # 获取父评论的作者
        from sqlalchemy import select as _sel

        parent_result = await db.execute(_sel(Comment).where(Comment.id == comment.parent_id))
        parent = parent_result.scalar_one_or_none()
        if parent:
            await push_notification(
                db, parent.user_id, current_user.id, "reply",
                f"{current_user.username} 回复了你的评论",
                article_id=comment.article_id,
                comment_id=comment.id,
            )

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
    redis: aioredis.Redis = Depends(get_redis),
):
    """删除评论 — 软删除（作者本人或有 comment:delete:any 权限的管理员）"""
    result = await db.execute(select(Comment).where(Comment.id == comment_id))
    comment = result.scalar_one_or_none()
    if not comment:
        raise NotFoundException("评论不存在")

    # 面试点：RBAC 权限判断 — 自己是作者 OR 有 comment:delete:any 权限
    is_owner = comment.user_id == current_user.id
    if not is_owner:
        # 不是作者 → 检查是否有"删除任何评论"权限
        from sqlalchemy import select as _sel

        from app.models.rbac import Permission, Role

        has_perm = await db.scalar(
            _sel(Permission)
            .join(Role.permissions)
            .join(Role.users)
            .where(User.id == current_user.id, Permission.name == "comment:delete:any")
            .limit(1)
        )
        if not has_perm:
            raise ForbiddenException("无权删除此评论")

    comment.is_deleted = True
    comment.content = "该评论已被删除"
    await db.flush()
    await redis.delete(f"blog:comments:{comment.article_id}")  # 删评论 → 清缓存
    return APIResponse(message="评论已删除")


@router.get("/comments/{comment_id}/replies", response_model=APIResponse[list[dict]])
async def get_replies(
    comment_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """加载更多子回复 — 分页加载指定评论的直接回复"""
    # 验证父评论存在
    parent = await db.get(Comment, comment_id)
    if not parent:
        raise NotFoundException("评论不存在")

    # 分页查子回复
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Comment)
        .options(selectinload(Comment.user))
        .where(Comment.parent_id == comment_id)
        .order_by(Comment.created_at.asc())
        .offset(offset)
        .limit(page_size)
    )
    replies = result.scalars().all()
    return APIResponse(data=[_reply_to_dict(r) for r in replies])


def _reply_to_dict(comment: Comment) -> dict:
    """子回复转为字典（不含嵌套 replies）"""
    return {
        "id": comment.id,
        "content": comment.content if not comment.is_deleted else "该评论已被删除",
        "article_id": comment.article_id,
        "parent_id": comment.parent_id,
        "is_deleted": comment.is_deleted,
        "user": {
            "id": comment.user.id, "username": comment.user.username, "avatar": comment.user.avatar,
        } if comment.user else {},
        "created_at": comment.created_at.isoformat(),
    }
