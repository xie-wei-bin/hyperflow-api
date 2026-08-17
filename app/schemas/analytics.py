"""
数据分析 Schema — 请求/响应校验

=== 面试重点 ===
Q: Pandas 在后端项目里能做什么？
A: 1. 数据聚合：groupby 按分类/日期汇总，比手写 SQL 循环快 10 倍
   2. Excel 导出：openpyxl + Pandas to_excel，运营/产品自助拉数据
   3. 趋势分析：resample 时间序列，一行代码搞定按天/周/月聚合
   4. 数据清洗：dropna/fillna 处理缺失值，比 if-else 干净

Q: 为什么不全部用 SQL 做聚合？
A: SQL 做简单聚合（COUNT、SUM）没问题，但复杂透视表和多维分析会写很长的
   CASE WHEN + 子查询。Pandas 的 pivot_table + groupby 写法更直观，而且
   数据量不大的情况下（<10万行），Pandas 内存计算比 MySQL 多次查询更快。
"""

from datetime import date


from pydantic import BaseModel, Field


# ── 总览统计 ──────────────────────────────

class OverviewStats(BaseModel):
    """仪表盘总览数据"""
    total_articles: int = Field(description="文章总数")
    total_users: int = Field(description="注册用户总数")
    total_comments: int = Field(description="评论总数")
    total_likes: int = Field(description="点赞总数")
    total_favorites: int = Field(description="收藏总数")
    total_categories: int = Field(description="分类总数")
    total_tags: int = Field(description="标签总数")
    today_articles: int = Field(description="今日新增文章")
    today_users: int = Field(description="今日新增用户")
    today_comments: int = Field(description="今日新增评论")
    draft_count: int = Field(description="草稿数量")
    published_count: int = Field(description="已发布数量")


# ── 趋势数据 ──────────────────────────────

class TrendPoint(BaseModel):
    """单个时间点的数据"""
    date: str = Field(description="日期（YYYY-MM-DD / YYYY-Www / YYYY-MM）")
    count: int = Field(description="该时间段的数值")


class TrendData(BaseModel):
    """趋势数据"""
    period: str = Field(description="聚合粒度：day / week / month")
    points: list[TrendPoint] = Field(default_factory=list, description="趋势数据点")
    total: int = Field(description="该时间段内总计")


# ── 分类/标签分布 ─────────────────────────

class DistributionItem(BaseModel):
    """单个分类/标签的分布数据"""
    name: str = Field(description="分类/标签名称")
    count: int = Field(description="文章数量")
    percentage: float = Field(description="占比百分比（0-100）")
    avg_views: float = Field(default=0, description="平均阅读量")


# ── 作者统计 ──────────────────────────────

class AuthorStats(BaseModel):
    """作者发文统计"""
    user_id: int
    username: str
    article_count: int = Field(description="发文数量")
    total_views: int = Field(description="总阅读量")
    total_likes: int = Field(description="总获赞数")
    total_comments: int = Field(description="总评论数")
    avg_views_per_article: float = Field(description="平均每篇阅读量")


# ── 热门标签统计 ──────────────────────────

class TagStats(BaseModel):
    """标签使用统计"""
    tag_id: int
    tag_name: str
    article_count: int = Field(description="关联文章数")
    total_views: int = Field(description="标签下文章总阅读量")


# ── 导出请求参数 ──────────────────────────

class ExportQuery(BaseModel):
    """Excel 导出请求参数"""
    start_date: date | None = Field(default=None, description="起始日期")
    end_date: date | None = Field(default=None, description="结束日期")
    category_id: int | None = Field(default=None, description="筛选分类")
    status: str | None = Field(default=None, description="文章状态：draft / published")
    fields: list[str] | None = Field(
        default=None,
        description="导出字段列表，默认全部（title,author,views,likes,comments,date）"
    )
