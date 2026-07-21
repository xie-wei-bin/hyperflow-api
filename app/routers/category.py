"""分类路由"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ConflictException, NotFoundException
from app.middleware.auth import get_current_user, require_permission
from app.models.article import Article
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.schemas.common import APIResponse

router = APIRouter(prefix="/api/categories", tags=["分类"])


@router.get("", response_model=APIResponse[list[dict]])
async def list_categories(db: AsyncSession = Depends(get_db)):
    """分类列表（含文章计数）"""
    result = await db.execute(select(Category).order_by(Category.name))
    categories = result.scalars().all()

    data = []
    for cat in categories:
        count_result = await db.execute(
            select(func.count())
            .select_from(Article)
            .where(Article.category_id == cat.id, Article.status == "published")
        )
        article_count = count_result.scalar() or 0
        data.append({
            "id": cat.id, "name": cat.name, "description": cat.description,
            "article_count": article_count, "created_at": cat.created_at.isoformat(),
        })

    return APIResponse(data=data)


@router.post("", status_code=201, response_model=APIResponse[dict])
async def create_category(
    data: CategoryCreate,
    current_user=Depends(require_permission("category:create")),
    db: AsyncSession = Depends(get_db),
):
    """创建分类（需 category:create 权限）"""
    result = await db.execute(select(Category).where(Category.name == data.name))
    if result.scalar_one_or_none():
        raise ConflictException("分类名已存在")

    category = Category(name=data.name, description=data.description)
    db.add(category)
    await db.flush()
    await db.refresh(category)

    return APIResponse(code=201, message="分类创建成功", data={
        "id": category.id, "name": category.name, "description": category.description,
    })


@router.put("/{category_id}", response_model=APIResponse[dict])
async def update_category(
    category_id: int,
    data: CategoryUpdate,
    current_user=Depends(require_permission("category:update")),
    db: AsyncSession = Depends(get_db),
):
    """更新分类（需 category:update 权限）"""
    category = await db.get(Category, category_id)
    if not category:
        raise NotFoundException("分类不存在")

    update_data = data.model_dump(exclude_none=True)
    if "name" in update_data:
        result = await db.execute(
            select(Category).where(Category.name == update_data["name"], Category.id != category_id)
        )
        if result.scalar_one_or_none():
            raise ConflictException("分类名已存在")
    if "name" in update_data:
        category.name = update_data["name"]
    if "description" in update_data:
        category.description = update_data["description"]

    await db.flush()
    await db.refresh(category)

    return APIResponse(message="分类更新成功", data={
        "id": category.id, "name": category.name, "description": category.description,
    })


@router.delete("/{category_id}", response_model=APIResponse[dict])
async def delete_category(
    category_id: int,
    current_user=Depends(require_permission("category:delete")),
    db: AsyncSession = Depends(get_db),
):
    """删除分类（需 category:delete 权限，有关联文章时禁止）"""
    category = await db.get(Category, category_id)
    if not category:
        raise NotFoundException("分类不存在")

    result = await db.execute(
        select(func.count()).select_from(Article).where(Article.category_id == category_id)
    )
    article_count = result.scalar() or 0
    if article_count > 0:
        raise ConflictException(f"该分类下有 {article_count} 篇文章，无法删除")

    await db.delete(category)
    await db.flush()

    return APIResponse(message="分类已删除")


@router.get("/{category_id}/articles", response_model=APIResponse[dict])
async def category_articles(
    category_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """分类下文章列表（分页）"""
    from app.services import article as article_service

    articles, total = await article_service.get_article_list(
        db=db, page=page, page_size=page_size, category_id=category_id, status="published"
    )

    items = [{
        "id": a.id, "title": a.title, "slug": a.slug, "summary": a.summary,
        "cover_image": a.cover_image, "view_count": a.view_count,
        "author": {"id": a.author.id, "username": a.author.username} if a.author else {},
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "created_at": a.created_at.isoformat(),
    } for a in articles]

    from app.utils.pagination import paginate

    return APIResponse(data=paginate(items, total, page, page_size))
