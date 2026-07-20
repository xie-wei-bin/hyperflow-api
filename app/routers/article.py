"""
文章路由 — CRUD + 热门排行 + 点赞收藏

=== 面试重点 ===
Q: Cache-Aside 策略在这个文件里怎么体现？
A: GET /{slug} 详情接口完整展示了四步：
   1. await redis.get(cache_key)           ← 先查 Redis
   2. 命中 → return 缓存数据（跳过数据库）
   3. 未命中 → 查 DB → redis.setex(cache_key, 600, data) ← 写回
   4. PUT 更新 → await redis.delete(cache_key) ← 主动失效
   下次 GET 时缓存已失效 → 重新查 DB → 写新缓存

Q: 点赞/收藏的幂等性怎么实现？
A: 两层保障：
   1. Redis Set 快速判断：sismember → 已点赞直接返回 200
   2. 数据库 UNIQUE(article_id, user_id) 兜底：并发情况下 Redis 可能来不及
      更新，DB 约束作为最终防线，重复插入会抛 IntegrityError → 409 → catch 返回 200
   这样用户点两次点赞，两次都返回成功，不会报错也不会重复计数

Q: 为什么阅读量增加和缓存操作不放在 service 层？
A: 职责分离：
   service 层：纯业务逻辑，可被定时任务/CLI 脚本复用
   router 层：HTTP 特有的处理（缓存、限流、权限），和协议绑定
   阅读量增加依赖 Redis，而 Redis 是基础设施细节
   如果 service 里操作 Redis，换了其他存储就要改 service
"""

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.exceptions import ForbiddenException
from app.middleware.auth import get_current_user
from app.models.like_favorite import Favorite, Like
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.article import ArticleCreate, ArticleDetail, ArticleListItem, ArticleUpdate
from app.schemas.common import APIResponse, PaginatedData
from app.services import article as article_service
from app.utils.pagination import paginate

router = APIRouter(prefix="/api/articles", tags=["文章"])


@router.get("", response_model=APIResponse[PaginatedData[ArticleListItem]])
async def list_articles(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category_id: int | None = None,
    tag_id: int | None = None,
    status: str | None = "published",
    search: str | None = None,
    sort_by: str = Query(default="created_at", pattern="^(created_at|view_count|published_at)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    文章列表 — 分页+筛选+搜索+排序

    面试点：Query 的参数校验比手写 if 更安全：
    pattern="^(asc|desc)$" 防止 SQL 注入（排序列）
    ge=1 防止负数页码；le=100 防止一次拉取过多
    """
    articles, total = await article_service.get_article_list(
        db,
        page=page,
        page_size=page_size,
        category_id=category_id,
        tag_id=tag_id,
        status=status,
        search=search,
        sort_by=sort_by,
        order=order,
    )
    items = [
        {
            "id": a.id,
            "title": a.title,
            "slug": a.slug,
            "summary": a.summary,
            "cover_image": a.cover_image,
            "status": a.status,
            "view_count": a.view_count,
            "like_count": len(a.likes) if a.likes else 0,
            "comment_count": len(a.comments) if a.comments else 0,
            "author": {"id": a.author.id, "username": a.author.username, "avatar": a.author.avatar}
            if a.author
            else {},
            "category": {"id": a.category.id, "name": a.category.name} if a.category else None,
            "tags": [{"id": t.id, "name": t.name} for t in (a.tags or [])],
            "published_at": a.published_at,
            "created_at": a.created_at,
        }
        for a in articles
    ]
    return APIResponse(data=paginate(items, total, page, page_size))


@router.get("/hot", response_model=APIResponse[list[dict]])
async def hot_articles(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """热门排行 — 优先 ZSet 分页，Redis 挂了降级 MySQL"""
    offset = (page - 1) * page_size
    return APIResponse(
        data=await article_service.get_hot_articles(db, page_size, offset=offset, redis=redis)
    )


@router.get("/{slug}", response_model=APIResponse[ArticleDetail])
async def get_article(
    slug: str, db: AsyncSession = Depends(get_db), redis: aioredis.Redis = Depends(get_redis)
):
    """
    文章详情 — Cache-Aside 缓存策略

    面试点：这是 Cache-Aside 模式的完整演示：
    Read: Redis → miss → DB → write Redis
    Hot score: ZINCRBY 实时更新热门排行
    """
    # 步骤 1：查缓存
    cached = await redis.get(f"blog:article:cache:{slug}")
    if cached:
        import json

        return APIResponse(data=json.loads(cached))

    # 步骤 2：查数据库
    article = await article_service.get_article_by_slug(db, slug)

    # 步骤 3：阅读量 + 热门排行（Redis 原子操作）
    await redis.incr(f"blog:article:view:{article.id}")
    await redis.zincrby("blog:article:hot", settings.HOT_RANK_VIEW_WEIGHT, str(article.id))

    detail = {
        "id": article.id,
        "title": article.title,
        "slug": article.slug,
        "content": article.content,
        "summary": article.summary,
        "cover_image": article.cover_image,
        "status": article.status,
        "view_count": article.view_count,
        "like_count": 0,
        "favorite_count": 0,
        "author": {
            "id": article.author.id,
            "username": article.author.username,
            "avatar": article.author.avatar,
        }
        if article.author
        else {},
        "category": {"id": article.category.id, "name": article.category.name}
        if article.category
        else None,
        "tags": [{"id": t.id, "name": t.name} for t in (article.tags or [])],
        "published_at": article.published_at,
        "created_at": article.created_at,
        "updated_at": article.updated_at,
    }

    # 步骤 4：写回缓存（10 分钟过期）
    import json

    await redis.setex(f"blog:article:cache:{slug}", 600, json.dumps(detail, default=str))
    return APIResponse(data=detail)


@router.post("", status_code=201, response_model=APIResponse[ArticleDetail])
async def create_article(
    data: ArticleCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建文章（需认证）"""
    article = await article_service.create_article(db, data.model_dump(), current_user.id)
    return APIResponse(
        code=201,
        message="文章创建成功",
        data={
            "id": article.id,
            "title": article.title,
            "slug": article.slug,
            "content": article.content,
            "summary": article.summary,
            "cover_image": article.cover_image,
            "status": article.status,
            "view_count": article.view_count,
            "like_count": 0,
            "favorite_count": 0,
            "author": {
                "id": current_user.id,
                "username": current_user.username,
                "avatar": current_user.avatar,
            },
            "category": None,
            "tags": [],
            "published_at": article.published_at,
            "created_at": article.created_at,
            "updated_at": article.updated_at,
        },
    )


@router.put("/{article_id}", response_model=APIResponse[ArticleDetail])
async def update_article(
    article_id: int,
    data: ArticleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """编辑文章（作者本人或管理员）— 更新后主动失效缓存"""
    article = await article_service.get_article_by_id(db, article_id)
    if article.author_id != current_user.id and current_user.role != "admin":
        raise ForbiddenException("无权编辑此文章")
    article = await article_service.update_article(db, article, data.model_dump(exclude_none=True))
    # 面试点：主动失效缓存（Cache Invalidation）
    await redis.delete(f"blog:article:cache:{article.slug}")
    return APIResponse(
        message="文章更新成功",
        data={
            "id": article.id,
            "title": article.title,
            "slug": article.slug,
            "content": article.content,
            "summary": article.summary,
            "cover_image": article.cover_image,
            "status": article.status,
            "view_count": article.view_count,
            "like_count": 0,
            "favorite_count": 0,
            "author": {
                "id": article.author.id,
                "username": article.author.username,
                "avatar": article.author.avatar,
            }
            if article.author
            else {},
            "category": {"id": article.category.id, "name": article.category.name}
            if article.category
            else None,
            "tags": [{"id": t.id, "name": t.name} for t in (article.tags or [])],
            "published_at": article.published_at,
            "created_at": article.created_at,
            "updated_at": article.updated_at,
        },
    )


@router.delete("/{article_id}", response_model=APIResponse[dict])
async def delete_article(
    article_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    删除文章 — 软删除

    面试点：为什么要软删除？
    1. 数据恢复：误删可以恢复（改 is_deleted=False 即可）
    2. 关联保护：文章下的评论/点赞/收藏还在，硬删会导致孤儿数据
    3. 审计合规：用户发布过的内容需要保留痕迹
    """
    article = await article_service.get_article_by_id(db, article_id)
    if article.author_id != current_user.id and current_user.role != "admin":
        raise ForbiddenException("无权删除此文章")
    article.is_deleted = True
    await db.flush()
    await redis.delete(f"blog:article:cache:{article.slug}")
    return APIResponse(message="文章已删除")


@router.post("/{article_id}/like", response_model=APIResponse[dict])
async def like_article(
    article_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    点赞 — 幂等设计

    面试点：两步去重保障幂等：
    1. Redis sismember 快速判断（毫秒级）
    2. DB UNIQUE(article_id, user_id) 最终防线（并发安全）
    """
    await article_service.get_article_by_id(db, article_id)
    if await redis.sismember(f"blog:user:likes:{current_user.id}", str(article_id)):
        return APIResponse(message="已点赞")
    like = Like(article_id=article_id, user_id=current_user.id)
    db.add(like)
    try:
        # begin_nested 创建 savepoint：回滚只影响 INSERT，不影响请求内之前的写操作
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        return APIResponse(message="已点赞")  # 并发冲突 → savepoint 自动回滚
    await redis.sadd(f"blog:user:likes:{current_user.id}", str(article_id))
    await redis.incr(f"blog:article:likes:{article_id}")
    await redis.zincrby("blog:article:hot", settings.HOT_RANK_LIKE_WEIGHT, str(article_id))  # 点赞权重 ×3
    return APIResponse(message="点赞成功")


@router.delete("/{article_id}/like", response_model=APIResponse[dict])
async def unlike_article(
    article_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """取消点赞"""
    if not await redis.sismember(f"blog:user:likes:{current_user.id}", str(article_id)):
        return APIResponse(message="未点赞")
    result = await db.execute(
        select(Like).where(Like.article_id == article_id, Like.user_id == current_user.id)
    )
    like = result.scalar_one_or_none()
    if like:
        await db.delete(like)
        await db.flush()
    await redis.srem(f"blog:user:likes:{current_user.id}", str(article_id))
    await redis.decr(f"blog:article:likes:{article_id}")
    await redis.zincrby("blog:article:hot", -settings.HOT_RANK_LIKE_WEIGHT, str(article_id))  # 回退点赞权重
    return APIResponse(message="已取消点赞")


@router.post("/{article_id}/favorite", response_model=APIResponse[dict])
async def favorite_article(
    article_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """收藏 — 同点赞的幂等去重机制"""
    await article_service.get_article_by_id(db, article_id)
    if await redis.sismember(f"blog:user:favorites:{current_user.id}", str(article_id)):
        return APIResponse(message="已收藏")
    favorite = Favorite(article_id=article_id, user_id=current_user.id)
    db.add(favorite)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        return APIResponse(message="已收藏")
    await redis.sadd(f"blog:user:favorites:{current_user.id}", str(article_id))
    await redis.zincrby("blog:article:hot", settings.HOT_RANK_FAVORITE_WEIGHT, str(article_id))
    return APIResponse(message="收藏成功")


@router.delete("/{article_id}/favorite", response_model=APIResponse[dict])
async def unfavorite_article(
    article_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """取消收藏"""
    if not await redis.sismember(f"blog:user:favorites:{current_user.id}", str(article_id)):
        return APIResponse(message="未收藏")
    result = await db.execute(
        select(Favorite).where(
            Favorite.article_id == article_id, Favorite.user_id == current_user.id
        )
    )
    favorite = result.scalar_one_or_none()
    if favorite:
        await db.delete(favorite)
        await db.flush()
    await redis.srem(f"blog:user:favorites:{current_user.id}", str(article_id))
    await redis.zincrby("blog:article:hot", -settings.HOT_RANK_FAVORITE_WEIGHT, str(article_id))  # 回退收藏权重
    return APIResponse(message="已取消收藏")
