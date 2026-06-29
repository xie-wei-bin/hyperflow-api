"""
搜索路由 — 全文搜索

面试点：搜索参数 q 是必填的（...），没有关键词不搜，避免空查询扫描全表
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import APIResponse, PaginatedData
from app.services import search as search_service
from app.utils.pagination import paginate

router = APIRouter(prefix="/api", tags=["搜索"])


@router.get("/search", response_model=APIResponse[PaginatedData[dict]])
async def search_articles(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """全文搜索（MySQL FULLTEXT INDEX）"""
    articles, total = await search_service.search_articles(
        db, keyword=q, page=page, page_size=page_size
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
    return APIResponse(data=paginate(items, total, page, page_size))
