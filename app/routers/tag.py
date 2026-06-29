"""
标签路由 — 列表 + 按标签筛选文章

面试点：标签是扁平结构（不像评论有父子），无需递归构建树
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.exceptions import NotFoundException
from app.models.article import Article
from app.models.tag import Tag
from app.schemas.common import APIResponse, PaginatedData
from app.utils.pagination import paginate

router = APIRouter(prefix="/api/tags", tags=["标签"])


@router.get("", response_model=APIResponse[list[dict]])
async def list_tags(db: AsyncSession = Depends(get_db)):
    """标签列表 — 所有标签平铺返回"""
    result = await db.execute(select(Tag).order_by(Tag.name))
    tags = result.scalars().all()
    return APIResponse(
        data=[{"id": t.id, "name": t.name, "created_at": t.created_at.isoformat()} for t in tags]
    )


@router.get("/{tag_id}/articles", response_model=APIResponse[PaginatedData[dict]])
async def tag_articles(
    tag_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """标签下文章列表 — 分页"""
    tag = await db.get(Tag, tag_id)
    if not tag:
        raise NotFoundException("标签不存在")

    # COUNT 子查询
    count_result = await db.execute(
        select(func.count())
        .select_from(Article)
        .join(Article.tags)
        .where(Tag.id == tag_id, Article.status == "published")
    )
    total = count_result.scalar() or 0

    # 分页查询文章
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Article)
        .options(selectinload(Article.author), selectinload(Article.category))
        .join(Article.tags)
        .where(Tag.id == tag_id, Article.status == "published")
        .order_by(Article.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    articles = result.scalars().all()

    items = [
        {
            "id": a.id,
            "title": a.title,
            "slug": a.slug,
            "summary": a.summary,
            "cover_image": a.cover_image,
            "view_count": a.view_count,
            "author": {"id": a.author.id, "username": a.author.username} if a.author else {},
            "published_at": a.published_at,
            "created_at": a.created_at,
        }
        for a in articles
    ]

    return APIResponse(data=paginate(items, total, page, page_size))
