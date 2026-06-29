"""
统一响应格式 — APIResponse + 分页

=== 面试重点 ===
Q: 为什么所有接口返回统一格式？
A: 前端不用写三套解析逻辑：
   成功: {"code": 200, "message": "success", "data": {...}}
   校验失败: {"code": 422, "message": "参数校验失败", "data": {"errors": [...]}}
   服务器错误: {"code": 500, "message": "服务器内部错误", "data": null}
   前端只判断 code 字段，不看 HTTP 状态码。

Q: Generic[T] 是什么？
A: Python 泛型 — APIResponse[UserProfile] 告诉 Pydantic "data 字段是 UserProfile 类型"。
   Swagger 文档会自动生成 data 的结构，前端也知道 data 里有什么字段。
   不用泛型 → data 是 Any → Swagger 不知道返回什么 → 前端猜
"""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")  # 泛型占位符：可以是任何类型


class APIResponse(BaseModel, Generic[T]):
    """所有 API 统一返回此结构"""

    code: int = 200
    message: str = "success"
    data: T | None = None


class PaginatedData(BaseModel, Generic[T]):
    """分页数据结构"""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int
