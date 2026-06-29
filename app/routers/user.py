"""用户路由"""

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import BadRequestException, NotFoundException
from app.middleware.auth import get_current_user
from app.models.article import Article
from app.models.like_favorite import Favorite
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.common import APIResponse, PaginatedData
from app.schemas.user import (
    ChangePasswordRequest,
    UpdateProfileRequest,
    UserProfile,
    UserPublic,
)
from app.utils.security import hash_password, verify_password

router = APIRouter(prefix="/api/users", tags=["用户"])


@router.get("/{user_id}", response_model=APIResponse[UserPublic])
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """获取用户公开信息"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("用户不存在")
    return APIResponse(data=user)


@router.put("/me", response_model=APIResponse[UserProfile])
async def update_profile(
    data: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新个人资料（需认证）"""
    if data.username is not None:
        # 检查用户名唯一性
        result = await db.execute(
            select(User).where(User.username == data.username, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise BadRequestException("用户名已被使用")
        current_user.username = data.username

    if data.email is not None:
        result = await db.execute(
            select(User).where(User.email == data.email, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise BadRequestException("邮箱已被使用")
        current_user.email = data.email

    if data.avatar is not None:
        current_user.avatar = data.avatar

    await db.flush()
    await db.refresh(current_user)
    return APIResponse(data=current_user)


@router.put("/me/password", response_model=APIResponse[dict])
async def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """修改密码（需认证，需旧密码验证，改后失效所有 refresh token）"""
    if not verify_password(data.old_password, current_user.password_hash):
        raise BadRequestException("旧密码错误")

    current_user.password_hash = hash_password(data.new_password)
    await db.flush()

    # 密码修改后失效所有 refresh token（强制重新登录）
    await redis.delete(f"blog:refresh_token:{current_user.id}")

    return APIResponse(message="密码修改成功，请重新登录")


@router.get("/me/favorites", response_model=APIResponse[PaginatedData[dict]])
async def my_favorites(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我的收藏列表（分页，需认证）"""
    # 统计总数
    count_result = await db.execute(
        select(func.count()).select_from(Favorite).where(Favorite.user_id == current_user.id)
    )
    total = count_result.scalar() or 0

    # 分页查询收藏
    result = await db.execute(
        select(Favorite)
        .where(Favorite.user_id == current_user.id)
        .order_by(Favorite.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    favorites = result.scalars().all()

    # 批量加载文章：一次 IN 查询替代 N 次独立 SELECT
    fav_article_ids = [fav.article_id for fav in favorites]
    article_map: dict[int, Article] = {}
    if fav_article_ids:
        art_result = await db.execute(select(Article).where(Article.id.in_(fav_article_ids)))
        article_map = {a.id: a for a in art_result.scalars().all()}

    items = []
    for fav in favorites:
        article = article_map.get(fav.article_id)
        if article:
            items.append(
                {
                    "id": article.id,
                    "title": article.title,
                    "slug": article.slug,
                    "summary": article.summary,
                    "cover_image": article.cover_image,
                    "status": article.status,
                    "view_count": article.view_count,
                    "like_count": 0,
                    "comment_count": 0,
                    "author": {},
                    "category": None,
                    "tags": [],
                    "published_at": article.published_at,
                    "created_at": article.created_at,
                }
            )

    from app.utils.pagination import paginate

    return APIResponse(data=paginate(items, total, page, page_size))
