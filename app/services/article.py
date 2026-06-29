"""
文章服务 — CRUD + 热门排行 + 缓存

=== 面试重点 ===
Q: Cache-Aside 模式具体怎么实现？
A: 四步走：
   1. 读请求 → 先查 Redis
   2. 命中 → 直接返回，不碰 DB
   3. 未命中 → 查 DB → 写回 Redis（设 10 分钟过期）
   4. 更新 → 主动删除 Redis 缓存（下次读自动重建）
   核心思想：Redis 是易失缓存，DB 是唯一真相来源

Q: 热门排行为什么用 ZSet 而不是每次 COUNT？
A: ZSet 是有序集合，member 是文章 ID，score 是热度分数。
   ZINCRBY blog:article:hot 3 article_123 → O(log N)
   ZREVRANGE blog:article:hot 0 19 → 取 top 20，O(log N + M)
   如果每次 COUNT(likes) + COUNT(comments) 计算：
   → 多表 JOIN + 子查询 → 每篇文章 3 次 DB 查询 → 20 篇 = 60 次查询
   ZSet 一次搞定，适合实时排行榜

Q: 阅读量为什么用 Redis INCR 而不是直接 UPDATE article SET view_count=view_count+1？
A: 热门文章每秒可能有上千次阅读，MySQL 行锁会成为瓶颈。
   Redis INCR 是内存原子操作，单线程不会冲突。
   然后 5 分钟批量回写一次 MySQL：1000 次 INCR → 1 次 UPDATE
   性能差距：1000 次 MySQL UPDATE ≈ 500ms，1000 次 Redis INCR ≈ 5ms

Q: 标签关联为什么用 ArticleTag 而不是 article.tags.append()?
A: article.tags 是 viewonly=True 的只读关系，不能直接写入。
   必须显式创建 ArticleTag 对象保证 ORM 行为清晰：
   db.add(ArticleTag(article_id=1, tag_id=3))
   如果允许自动创建，ORM 可能会产生意外的 INSERT/UPDATE/DELETE 组合
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundException
from app.models.article import Article
from app.models.like_favorite import Like
from app.models.tag import Tag


async def get_article_list(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    category_id: int | None = None,
    tag_id: int | None = None,
    status: str | None = "published",
    search: str | None = None,
    sort_by: str = "created_at",
    order: str = "desc",
) -> tuple[list[Article], int]:
    """
    文章分页列表 + 多条件筛选

    面试点：selectinload 预加载关联数据，避免 N+1
    没有 selectinload：查 20 篇文章 → 每篇查 author(×20) + category(×20) + tags(×20)
    有了 selectinload：3 次 IN 查询 → 批量查出所有 author/category/tags
    """
    query = (
        select(Article)
        .options(
            selectinload(Article.author),
            selectinload(Article.category),
            selectinload(Article.tags),
        )
        .where(Article.is_deleted == False)  # noqa: E712
    )  # 软删除过滤

    if status:
        query = query.where(Article.status == status)
    if category_id:
        query = query.where(Article.category_id == category_id)
    if tag_id:
        query = query.where(Article.tags.any(Tag.id == tag_id))
    if search:
        # 面试点：MySQL FULLTEXT 搜索，比 LIKE '%keyword%' 快几十倍
        # LIKE 全表扫描，FULLTEXT 走倒排索引
        query = query.where(func.match(Article.title, Article.content).against(search))

    # 排序
    sort_column = getattr(Article, sort_by, Article.created_at)
    if order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # 面试点：COUNT 用子查询，避免和主查询的 offset/limit/sort 冲突
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    articles = list(result.scalars().all())

    return articles, total


async def get_article_by_slug(db: AsyncSession, slug: str) -> Article:
    """根据 slug 获取文章（SEO 友好的 URL）"""
    result = await db.execute(
        select(Article)
        .options(
            selectinload(Article.author), selectinload(Article.category), selectinload(Article.tags)
        )
        .where(Article.slug == slug)
    )
    article = result.scalar_one_or_none()
    if not article:
        raise NotFoundException("文章不存在")
    return article


async def get_article_by_id(db: AsyncSession, article_id: int) -> Article:
    """根据 ID 获取文章（内部使用，如权限检查）"""
    result = await db.execute(
        select(Article)
        .options(
            selectinload(Article.author), selectinload(Article.category), selectinload(Article.tags)
        )
        .where(Article.id == article_id)
    )
    article = result.scalar_one_or_none()
    if not article:
        raise NotFoundException("文章不存在")
    return article


async def create_article(db: AsyncSession, data: dict[str, Any], author_id: int) -> Article:
    """创建文章 + 关联标签"""
    tag_ids = data.pop("tag_ids", [])

    article = Article(**data, author_id=author_id)
    db.add(article)
    await db.flush()  # flush 获取 article.id，但不 commit

    if tag_ids:
        from app.models.article import ArticleTag

        for tag_id in tag_ids:
            tag = await db.get(Tag, tag_id)
            if tag:
                db.add(ArticleTag(article_id=article.id, tag_id=tag.id))

    await db.flush()
    await db.refresh(article)  # 重新加载，获取预加载的关联数据
    return article


async def update_article(db: AsyncSession, article: Article, data: dict[str, Any]) -> Article:
    """更新文章 + 重建标签关联"""
    tag_ids = data.pop("tag_ids", None)

    for key, value in data.items():
        if value is not None:
            setattr(article, key, value)

    if tag_ids is not None:
        from app.models.article import ArticleTag

        # 面试点：先删后建，保证最终状态 = 输入状态
        for at in list(article.article_tags):
            await db.delete(at)
        await db.flush()
        for tag_id in tag_ids:
            tag = await db.get(Tag, tag_id)
            if tag:
                db.add(ArticleTag(article_id=article.id, tag_id=tag.id))

    await db.flush()
    await db.refresh(article)
    return article


async def get_hot_articles(db: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    """
    热门文章排行 — 按阅读量排序

    面试点：生产环境应该用 Redis ZSet（ZREVRANGE）而非 MySQL ORDER BY
    这里保留 MySQL 版本作为 Redis 不可用时的降级方案
    """
    result = await db.execute(
        select(Article)
        .options(selectinload(Article.author), selectinload(Article.category))
        .where(Article.status == "published", Article.is_deleted == False)  # noqa: E712
        .order_by(Article.view_count.desc())
        .limit(limit)
    )
    articles = result.scalars().all()

    # 批量查询 like_count：一次 IN 查询替代 N 次独立 COUNT
    article_ids = [a.id for a in articles]
    like_counts: dict[int, int] = {}
    if article_ids:
        like_result = await db.execute(
            select(Like.article_id, func.count().label("cnt"))
            .where(Like.article_id.in_(article_ids))
            .group_by(Like.article_id)
        )
        like_counts = {row.article_id: row.cnt for row in like_result.all()}

    hot_list: list[dict[str, Any]] = []
    for article in articles:
        like_count = like_counts.get(article.id, 0)

        hot_list.append(
            {
                "id": article.id,
                "title": article.title,
                "slug": article.slug,
                "summary": article.summary,
                "view_count": article.view_count,
                "like_count": like_count,
                "author": {
                    "id": article.author.id,
                    "username": article.author.username,
                    "avatar": article.author.avatar,
                },
                "category": {"id": article.category.id, "name": article.category.name}
                if article.category
                else None,
                "published_at": article.published_at.isoformat() if article.published_at else None,
            }
        )

    return hot_list
