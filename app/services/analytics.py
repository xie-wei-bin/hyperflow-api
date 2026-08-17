"""
数据分析服务 — Pandas 聚合 + Excel 导出

=== 面试重点 ===
Q: 为什么在 service 层用 Pandas 而不是在 router 层？
A: 职责分离 — service 层做数据加工，router 层只负责 HTTP 协议。
   如果以后要支持 CLI 导出（python -m app.cli export --format excel），
   service 层代码可以原封不动复用。

Q: Pandas DataFrame 在后端里怎么用？
A: 1. 从 SQLAlchemy 查询结果提取 dict 列表 → pd.DataFrame(rows)
   2. groupby / resample / pivot_table 做聚合
   3. to_dict("records") 转回 Python 对象 → Pydantic 序列化 → JSON 响应
   4. to_excel() 配合 openpyxl 生成 Excel → StreamingResponse 返回文件

Q: 数据量大了怎么办？
A: 当前设计适合万级数据。数据量超过 10 万行时：
   1. 用 SQL 子查询先聚合再交给 Pandas 做二次处理
   2. Excel 导出改用 openpyxl 流式写入（write_only=True），逐行写不占内存
   3. 大数据量导出考虑异步任务 + 文件链接下载

Q: Pandas vs 纯 SQL 的选择原则？
A: - 简单聚合（COUNT/SUM/AVG）→ SQL，数据库原生能力最快
   - 复杂透视/多维度交叉分析 → Pandas，代码比长 SQL 好维护
   - 数据导出/格式转换 → Pandas，to_excel/to_csv 一行搞定
   - 本项目场景：MySQL 做初步过滤，Pandas 做最终聚合和格式化
"""

from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.article import Article
from app.models.category import Category
from app.models.comment import Comment
from app.models.like_favorite import Favorite, Like
from app.models.tag import Tag
from app.models.user import User


# ── 总览统计（纯 SQL 聚合，Pandas 做格式化） ──

async def get_overview_stats(db: AsyncSession) -> dict[str, Any]:
    """
    仪表盘总览 — SQL 聚合 + Pandas 数据清洗

    面试点：SQL 做 COUNT/SUM（数据库原生能力），Pandas 做数据组装和缺失值处理。
    不把聚合逻辑全扔给 Pandas，因为 MySQL 的 COUNT(*) 走索引只要几毫秒，
    拉回 Python 再 count 反而多一次网络传输。
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 并行查询（各 SELECT 互不依赖，数据库可以并行执行）
    total_articles = await db.scalar(select(func.count()).select_from(Article))
    total_users = await db.scalar(select(func.count()).select_from(User))
    total_comments = await db.scalar(select(func.count()).select_from(Comment))
    total_likes = await db.scalar(select(func.count()).select_from(Like))
    total_favorites = await db.scalar(select(func.count()).select_from(Favorite))
    total_categories = await db.scalar(select(func.count()).select_from(Category))
    total_tags = await db.scalar(select(func.count()).select_from(Tag))

    today_articles = await db.scalar(
        select(func.count()).select_from(Article).where(Article.created_at >= today)
    )
    today_users = await db.scalar(
        select(func.count()).select_from(User).where(User.created_at >= today)
    )
    today_comments = await db.scalar(
        select(func.count()).select_from(Comment).where(Comment.created_at >= today)
    )
    draft_count = await db.scalar(
        select(func.count()).select_from(Article).where(Article.status == "draft")
    )
    published_count = await db.scalar(
        select(func.count()).select_from(Article).where(Article.status == "published")
    )

    # 面试点：用 Pandas 做数据组装 — 虽然这里简单，但展示了 DataFrame 构造方式
    # 复杂场景下，可以把多次查询结果合并成一个报表 DataFrame
    df = pd.DataFrame([{
        "total_articles": total_articles or 0,
        "total_users": total_users or 0,
        "total_comments": total_comments or 0,
        "total_likes": total_likes or 0,
        "total_favorites": total_favorites or 0,
        "total_categories": total_categories or 0,
        "total_tags": total_tags or 0,
        "today_articles": today_articles or 0,
        "today_users": today_users or 0,
        "today_comments": today_comments or 0,
        "draft_count": draft_count or 0,
        "published_count": published_count or 0,
    }])

    # fillna(0)：防止 None 值导致 JSON 序列化异常
    return df.fillna(0).to_dict("records")[0]


# ── 文章发布趋势（Pandas 时间序列） ──

async def get_article_trend(
    db: AsyncSession,
    days: int = 30,
    period: str = "day",
) -> dict[str, Any]:
    """
    文章发布趋势 — Pandas resample 时间序列聚合

    面试点：resample 是 Pandas 的核心能力之一，按天/周/月重采样时间序列。
    如果纯 SQL 实现：
      day: GROUP BY DATE(created_at)   ← 还行
      week: GROUP BY YEARWEEK(created_at) ← 开始复杂
      month: GROUP BY DATE_FORMAT(created_at, '%Y-%m') ← 更复杂
    Pandas resample 一行代码搞定三种粒度，代码可读性远胜 SQL。
    """
    start_date = datetime.now() - timedelta(days=days)

    # 只取需要的列，减少数据传输量
    result = await db.execute(
        select(Article.created_at)
        .where(Article.created_at >= start_date, Article.is_deleted == False)  # noqa: E712
        .order_by(Article.created_at)
    )
    rows = [{"created_at": r[0]} for r in result.all()]

    # 面试点：空数据保护 — 没有文章时返回空趋势而非报错
    if not rows:
        return {"period": period, "points": [], "total": 0}

    # ── Pandas 时间序列处理 ──
    df = pd.DataFrame(rows)
    # 设置时间索引，resample 的前提条件
    df["created_at"] = pd.to_datetime(df["created_at"])
    df.set_index("created_at", inplace=True)

    # 面试点：resample 一行搞定按天/周/月聚合
    # 对比纯 SQL 需要拼不同的 GROUP BY + DATE_FORMAT，清晰太多
    freq_map = {"day": "D", "week": "W", "month": "ME"}
    freq = freq_map.get(period, "D")

    # resample + size 统计每个时间桶内的文章数
    trend = df.resample(freq).size()

    # 面试点：fillna(0) — resample 会自动补全缺失的时间桶（如某天没发文），
    # 值为 NaN，填充为 0 保证前端折线图不中断
    trend = trend.fillna(0)

    points = [
        {"date": str(idx.date()) if period == "day" else str(idx),
         "count": int(val)}
        for idx, val in trend.items()
    ]

    return {
        "period": period,
        "points": points,
        "total": int(trend.sum()),
    }


# ── 分类分布（Pandas groupby + 百分比） ──

async def get_category_distribution(db: AsyncSession) -> list[dict[str, Any]]:
    """
    分类文章分布 — Pandas groupby 聚合 + 百分比计算

    面试点：groupby 后 agg 做多指标聚合（count + mean），比 SQL 的子查询清晰。
    """
    result = await db.execute(
        select(
            Category.name,
            func.count(Article.id).label("count"),
            func.coalesce(func.avg(Article.view_count), 0).label("avg_views"),
        )
        .outerjoin(Article, Category.id == Article.category_id)
        .group_by(Category.id)
        .order_by(func.count(Article.id).desc())
    )
    rows = [{"name": r.name, "count": r.count, "avg_views": float(r.avg_views)} for r in result.all()]

    if not rows:
        return []

    # ── Pandas 处理 ──
    df = pd.DataFrame(rows)

    # 计算每个分类的百分比
    total = df["count"].sum()
    # 面试点：Pandas 向量化计算 — 整列一次计算，比 for 循环快几十倍
    # 向量化操作是在 C 层面一次性对所有元素执行同一运算，没有 Python 的 for 循环开销
    df["percentage"] = (df["count"] / total * 100).round(1) if total > 0 else 0

    return df.to_dict("records")


# ── 作者统计（Pandas 排序 + Top N） ──

async def get_author_stats(
    db: AsyncSession,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """
    作者发文统计 — Pandas 排序 + Top N 筛选

    面试点：SQL 取聚合数据 → Pandas 排序/过滤/计算派生指标
    派生指标（如 avg_views_per_article）在 Pandas 里一行计算，SQL 需要子查询。
    """
    # SQL 做基础聚合（数据库擅长）
    result = await db.execute(
        select(
            User.id.label("user_id"),
            User.username,
            func.count(Article.id).label("article_count"),
            func.coalesce(func.sum(Article.view_count), 0).label("total_views"),
        )
        .outerjoin(Article, User.id == Article.author_id)
        .group_by(User.id)
        .having(func.count(Article.id) > 0)  # 只统计有文章的作者
    )
    rows = [
        {
            "user_id": r.user_id,
            "username": r.username,
            "article_count": r.article_count,
            "total_views": r.total_views,
        }
        for r in result.all()
    ]

    if not rows:
        return []

    df = pd.DataFrame(rows)

    # ── Pandas 派生指标 + 排序 ──
    # 面试点：向量化计算 avg_views_per_article，避免 for 循环
    df["avg_views_per_article"] = (df["total_views"] / df["article_count"]).round(1)
    # 按发文数降序排，取 Top N
    df = df.sort_values("article_count", ascending=False).head(top_n)

    # 补充点赞和评论数据（需要额外查询）
    for i, row in df.iterrows():
        likes_count = await db.scalar(
            select(func.count())
            .select_from(Like)
            .join(Article, Like.article_id == Article.id)
            .where(Article.author_id == row["user_id"])
        )
        comments_count = await db.scalar(
            select(func.count())
            .select_from(Comment)
            .join(Article, Comment.article_id == Article.id)
            .where(Article.author_id == row["user_id"])
        )
        df.at[i, "total_likes"] = likes_count or 0
        df.at[i, "total_comments"] = comments_count or 0

    return df.to_dict("records")


# ── 标签统计 ──

async def get_tag_stats(db: AsyncSession, top_n: int = 20) -> list[dict[str, Any]]:
    """
    标签使用统计 — 哪个标签最热门

    面试点：多对多关系聚合在 SQL 里 JOIN 三层（tag → article_tag → article），
    Pandas 拿到结果后可以快速透视分析。
    """
    result = await db.execute(
        select(
            Tag.id.label("tag_id"),
            Tag.name.label("tag_name"),
            func.count(Article.id).label("article_count"),
            func.coalesce(func.sum(Article.view_count), 0).label("total_views"),
        )
        .outerjoin(Tag.article_tags)
        .outerjoin(Article)
        .group_by(Tag.id)
        .order_by(func.count(Article.id).desc())
        .limit(top_n)
    )
    rows = [
        {
            "tag_id": r.tag_id,
            "tag_name": r.tag_name,
            "article_count": r.article_count,
            "total_views": r.total_views,
        }
        for r in result.all()
    ]

    if not rows:
        return []

    # Pandas 格式化：按 article_count 排序
    df = pd.DataFrame(rows)
    df = df.sort_values("article_count", ascending=False)

    return df.to_dict("records")


# ── Excel 导出（Pandas + openpyxl） ──

async def export_articles_excel(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
    category_id: int | None = None,
    status: str | None = None,
    fields: list[str] | None = None,
) -> BytesIO:
    """
    文章数据导出为 Excel — Pandas to_excel + BytesIO

    面试点：为什么用 BytesIO 而不是写临时文件？
    1. 无磁盘 I/O：直接内存操作，适合云环境（容器磁盘可能是只读的）
    2. 无清理负担：临时文件需要定时清理，BytesIO 用完自动 GC
    3. 并发友好：每个请求独立的 BytesIO，不会互相覆盖文件

    面试点：为什么不用 Pandas 默认的 xlsxwriter 而用 openpyxl？
    openpyxl 支持样式设置 + 列宽调整 + 单元格格式化，xlsxwriter 也支持
    但 openpyxl 是纯 Python 实现，跨平台无编译依赖，Docker 里更好装。
    """
    # 构建查询
    query = select(
        Article.id,
        Article.title,
        Article.slug,
        Article.status,
        Article.view_count,
        Article.created_at,
        Article.published_at,
        User.username.label("author_name"),
        Category.name.label("category_name"),
    ).outerjoin(User, Article.author_id == User.id) \
     .outerjoin(Category, Article.category_id == Category.id) \
     .where(Article.is_deleted == False)  # noqa: E712

    if start_date:
        query = query.where(Article.created_at >= start_date)
    if end_date:
        query = query.where(Article.created_at <= end_date)
    if category_id:
        query = query.where(Article.category_id == category_id)
    if status:
        query = query.where(Article.status == status)

    query = query.order_by(Article.created_at.desc())

    result = await db.execute(query)
    rows = [dict(r._mapping) for r in result.all()]

    # ── Pandas DataFrame 构建 ──
    df = pd.DataFrame(rows)

    # 重命名列（数据库列名 → 中文表头）
    column_map = {
        "id": "ID",
        "title": "标题",
        "slug": "URL别名",
        "status": "状态",
        "view_count": "阅读量",
        "created_at": "创建时间",
        "published_at": "发布时间",
        "author_name": "作者",
        "category_name": "分类",
    }

    # 如果指定了导出字段，只保留需要的列
    if fields:
        # 字段映射：前端传"title" → 数据库列"title"
        available_columns = [col for col in fields if col in df.columns or col in column_map.values()]
        if available_columns:
            # 按用户指定的顺序排列
            df = df[[c for c in available_columns if c in df.columns]]

    # 面试点：inplace=True 原地修改避免拷贝，但链式调用时可能出问题
    # 这里用显式赋值方式更安全
    df = df.rename(columns=column_map)

    # 面试点：fillna("") — Pandas NaN 写入 Excel 会变成空单元格，比显示 "NaN" 好
    df = df.fillna("")

    # ── Excel 写入 ──
    output = BytesIO()
    # 面试点：openpyxl 引擎支持样式设置
    with pd.ExcelWriter(output, engine="openpyxl") as writer:  # type: ignore[attr-defined]
        df.to_excel(writer, sheet_name="文章数据", index=False)

        # 调整列宽（Pandas 默认列宽可能太窄）
        worksheet = writer.sheets["文章数据"]
        for col_idx, col_name in enumerate(df.columns, start=1):
            # 计算合适的列宽：中文≈2字符宽度，英文≈1字符宽度
            max_len = len(str(col_name)) * 2  # 中文表头
            for row_val in df[col_name].head(100):  # 只取前 100 行计算，避免 O(n)
                # 中文字符占两个宽度
                str_val = str(row_val)
                char_len = sum(2 if ord(c) > 127 else 1 for c in str_val)
                max_len = max(max_len, char_len)
            # 限制最大宽度 50，防止单列太宽
            adjusted_width = min(max_len + 4, 50)
            worksheet.column_dimensions[worksheet.cell(row=1, column=col_idx).column_letter].width = adjusted_width

    output.seek(0)  # 重置指针，让 StreamingResponse 从头读取
    return output
