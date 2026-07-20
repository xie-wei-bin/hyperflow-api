"""
搜索服务 — MySQL FULLTEXT 全文搜索

=== 面试重点 ===
Q: FULLTEXT 和 LIKE '%keyword%' 的区别？
A: LIKE 全表扫描 O(n)，数据量过万就明显变慢。
   FULLTEXT 倒排索引：事先把每个词对应哪些文章存好，搜索直接查索引 → O(log n)
   代价：写操作需要更新索引（INSERT/UPDATE 慢一点点）

Q: 为什么用 MySQL FULLTEXT 不用 Elasticsearch？
A: ES 适合百万级以上全文搜索 + 复杂聚合。
   当前项目数据量（万级）MySQL FULLTEXT 完全够用。
   Service 层接口不变，未来迁移 ES 只换这个文件，router 一行不动。

Q: 停用词和短关键词怎么处理？
A: MySQL 默认忽略 innodb_ft_min_token_size=3（小于 3 字符不分词）和内置停用词。
   需要单字搜索时改 MySQL 配置；生产环境推荐迁移 ES 获得更好中文分词。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.article import Article


async def search_articles(
    db: AsyncSession,
    keyword: str,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Article], int]:
    """全文搜索文章（标题 + 正文），按相关度降序 + 发布时间降序"""
    # MATCH...AGAINST 走 FULLTEXT 倒排索引
    # 计算相关度得分（MATCH 返回的浮点数，越高越匹配）
    relevance = func.match(Article.title, Article.content).against(keyword)

    # 构造排序：先按匹配度，相同匹配度再按发布时间
    order_by = [relevance.desc(), Article.published_at.desc()]

    query = (
        select(Article)

        .options(
            selectinload(Article.author), selectinload(Article.category), selectinload(Article.tags)
        )
        .where(
            Article.status == "published",
            Article.is_deleted == False,  # noqa: E712
            relevance > 0,               # 相关度为 0 的直接排除
        )
        .order_by(*order_by)
    )

    # COUNT 子查询
    count_query = (
        select(func.count())
        .select_from(Article)
        .where(
            Article.status == "published",
            Article.is_deleted == False,  # noqa: E712
            func.match(Article.title, Article.content).against(keyword),
        )
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    articles = result.scalars().all()

    return articles, total
