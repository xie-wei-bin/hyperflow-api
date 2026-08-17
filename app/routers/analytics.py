"""
数据分析路由 — 仪表盘统计 + 趋势分析 + Excel 导出

=== 面试重点 ===
Q: 数据分析接口为什么需要鉴权？
A: 统计数据和 Excel 导出可能包含敏感信息（用户邮箱、阅读行为），
   只允许管理员或特定角色访问。

Q: Excel 导出为什么用 StreamingResponse？
A: StreamingResponse 流式返回，不会把整个文件加载到服务器内存。
   BytesIO 在 memory 里，对于小文件（<10MB）没问题，
   大文件需要改用临时文件 + FileResponse 异步删除。

Q: 相比直接查 MySQL 出报表，Pandas 这个方案的优缺点？
A: 优点：代码简洁（groupby/resample 一行顶 SQL 十行）、
         导出方便（to_excel 带格式）、
         数据清洗方便（fillna/dropna/类型转换）。
   缺点：需要把数据从 MySQL 拉到 Python 内存，万级以上建议 SQL 先聚合再 Pandas 二次处理。
"""

from datetime import date
from io import BytesIO

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.analytics import (
    AuthorStats,
    DistributionItem,
    ExportQuery,
    OverviewStats,
    TagStats,
    TrendData,
    TrendPoint,
)
from app.schemas.common import APIResponse
from app.services import analytics as analytics_service

router = APIRouter(prefix="/api/analytics", tags=["数据分析"])


def _check_admin(current_user: User):
    """
    权限校验 — 只允许管理员访问数据分析接口

    面试点：数据分析接口包含敏感统计数据，需要鉴权。
    提取为独立函数，方便未来改为 require_permission("analytics:read")。
    """
    # 先检查 RBAC 角色
    if current_user.roles:
        for role in current_user.roles:
            if role.name == "admin":
                return
    # 兼容旧 role 枚举
    if current_user.role == "admin":
        return
    raise ForbiddenException("仅管理员可访问数据分析接口")


# ── 总览统计 ──────────────────────────────

@router.get("/overview", response_model=APIResponse[OverviewStats])
async def get_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    仪表盘总览 — 文章/用户/评论/点赞/收藏总数 + 今日数据

    面试点：这个接口的数据可以直接对接前端仪表盘（Dashboard）页面。
    前端通常做成卡片布局：4 个统计卡片 + 趋势折线图 + 分类饼图。
    """
    _check_admin(current_user)
    data = await analytics_service.get_overview_stats(db)
    return APIResponse(data=data)


# ── 文章发布趋势 ──────────────────────────

@router.get("/articles/trend", response_model=APIResponse[TrendData])
async def get_article_trend(
    days: int = Query(default=30, ge=1, le=365, description="统计天数（1-365）"),
    period: str = Query(default="day", pattern="^(day|week|month)$", description="聚合粒度"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    文章发布趋势 — 按天/周/月聚合

    面试点：period 参数用正则校验，防止传入非法值导致 Pandas resample 抛异常。
    resample('D') 支持，resample('X') 会直接报错 → 参数校验在入口处拦截。
    """
    _check_admin(current_user)
    data = await analytics_service.get_article_trend(db, days=days, period=period)
    return APIResponse(data=data)


# ── 分类分布 ──────────────────────────────

@router.get("/articles/category-distribution", response_model=APIResponse[list[DistributionItem]])
async def get_category_distribution(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    分类文章分布 — 饼图/柱状图数据源

    面试点：返回 percentage 字段，前端不需要自己做百分比计算。
    后端一次算好，前端直接绑图表组件，减少前端逻辑。
    """
    _check_admin(current_user)
    data = await analytics_service.get_category_distribution(db)
    return APIResponse(data=data)


# ── 作者统计 ──────────────────────────────

@router.get("/articles/author-stats", response_model=APIResponse[list[AuthorStats]])
async def get_author_stats(
    top_n: int = Query(default=10, ge=1, le=50, description="Top N 作者"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    作者发文统计 — Top N 排行

    面试点：派生指标（avg_views_per_article）在 Pandas 里算好再返回，
    比前端拿到原始数据自己算更安全（前端可能算错或展示不一致）。
    """
    _check_admin(current_user)
    data = await analytics_service.get_author_stats(db, top_n=top_n)
    return APIResponse(data=data)


# ── 标签统计 ──────────────────────────────

@router.get("/articles/tag-stats", response_model=APIResponse[list[TagStats]])
async def get_tag_stats(
    top_n: int = Query(default=20, ge=1, le=100, description="Top N 标签"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """标签使用统计 — 哪个标签最热门"""
    _check_admin(current_user)
    data = await analytics_service.get_tag_stats(db, top_n=top_n)
    return APIResponse(data=data)


# ── Excel 导出 ────────────────────────────

@router.get("/export/articles")
async def export_articles(
    start_date: date | None = Query(default=None, description="起始日期"),
    end_date: date | None = Query(default=None, description="结束日期"),
    category_id: int | None = Query(default=None, description="筛选分类"),
    status: str | None = Query(default=None, description="文章状态"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    导出文章数据为 Excel 文件

    面试点：StreamingResponse + BytesIO 实现内存中生成 Excel 直接返回。
    优点：
    1. 不需要磁盘 I/O，适合容器环境
    2. media_type 设置正确，浏览器自动识别为 Excel 文件
    3. headers 里的 filename 支持中文（URL 编码）

    面试点：为什么不返回 APIResponse？
    Excel 是二进制文件，不能包在 JSON 里。StreamingResponse 直接返回文件流。
    这是 RESTful API 的常见"特例"— 文件下载接口不走统一响应格式。
    """
    _check_admin(current_user)

    excel_bytes = await analytics_service.export_articles_excel(
        db,
        start_date=start_date,
        end_date=end_date,
        category_id=category_id,
        status=status,
    )

    # 面试点：文件名用 URL 编码处理中文，避免浏览器下载时乱码
    from urllib.parse import quote

    filename = quote("文章数据导出.xlsx")

    return StreamingResponse(
        excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
    )
