"""
通用分页辅助

=== 面试重点 ===
Q: 为什么用 OFFSET 分页而不是游标分页？
A: OFFSET 支持跳页（URL 里 ?page=5），对博客场景更友好。
   缺点是深翻页慢：LIMIT 100000, 20 需要扫描 100020 行再丢弃前 10 万行。
   游标分页（WHERE id > last_id LIMIT 20）每次只扫固定行数，但无法跳到指定页。
   当前数据量小（< 10 万条），OFFSET 够用；数据量大后改用游标分页。
   paginate统一封装后端分页返回格式，所有列表接口（文章、评论、用户列表）共用这个函数，
   前端拿到固定结构，不用每个接口自己组装分页字典。
"""

import math
from typing import Any


def paginate(
    items: list[Any],#当前页的数据列表（已经 offset+limit 查询出来的 ORM 数据 / 转好的 Pydantic 对象）
    total: int,#符合筛选条件的全部数据总条数，需要单独 select(func.count()) 查询
    page: int,#当前请求页码
    page_size: int,#每页条数
) -> dict[str, Any]:
    """构建统一的分页响应数据"""
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,#math.ceil()：向上取整，max(1, ...) 边界防护
        "total_pages": max(1, math.ceil(total / page_size)),  # 至少 1 页，防除以 0
    }
