"""
搜索路由 — 全文搜索

面试点：搜索参数 q 是必填的，没有关键词不搜，避免空查询扫描全表
"""

import json
import re

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.schemas.common import APIResponse, PaginatedData
from app.services import search as search_service
from app.utils.pagination import paginate

router = APIRouter(prefix="/api", tags=["搜索"])

# 过滤纯符号/空白关键词，避免无效 FULLTEXT 查询
_EMPTY_KEYWORD_RE = re.compile(r"^[\s\W_]+$")


@router.get("/search", response_model=APIResponse[PaginatedData[dict]])
async def search_articles(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """全文搜索（MySQL FULLTEXT INDEX）— 相关度排序 + Redis 缓存"""
    keyword = q.strip()

    # 拦截纯符号/空白关键词
    if _EMPTY_KEYWORD_RE.match(keyword):
        return APIResponse(data=paginate([], 0, page, page_size))

    # 缓存命中 → 直接返回
    cache_key = f"blog:search:{keyword}:{page}:{page_size}"
    cached = await redis.get(cache_key)
    if cached:
        return APIResponse(data=json.loads(cached))

    articles, total = await search_service.search_articles(
        db, keyword=keyword, page=page, page_size=page_size
    )

    items = [
        {
            "id": a.id,
            "title": a.title,
            "slug": a.slug,
            "summary": a.summary,
            "cover_image": a.cover_image,
            "view_count": a.view_count,
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
    data = paginate(items, total, page, page_size)

    # 缓存 5 分钟
    await redis.setex(cache_key, 300, json.dumps(data, default=str))
    return APIResponse(data=data)
