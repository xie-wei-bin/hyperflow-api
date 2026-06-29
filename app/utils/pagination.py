"""
通用分页辅助

=== 面试重点 ===
Q: 为什么不用 OFFSET 分页？
A: OFFSET 深翻页有性能问题：LIMIT 100000, 20 需要扫描 100020 行再丢弃前 10 万行。
   更好的方案：游标分页（WHERE id > last_id LIMIT 20），但 URL 不友好。
   当前 OFFSET 适合中小数据量（< 10 万条）。
   面试时要能说出两种方案的优劣。
"""

import math
from typing import Any


def paginate(
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """构建统一的分页响应数据"""
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total / page_size)),  # 至少 1 页，防除以 0
    }
