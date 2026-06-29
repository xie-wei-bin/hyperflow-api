"""
请求耗时中间件 — 记录每个请求的处理时间

=== 面试重点 ===
Q: 为什么用中间件而不是在每个路由里手动计时？
A: 1. DRY：100 个路由不用写 100 次 time.time()
   2. 统一格式：所有请求的耗时日志格式一致，日志平台好检索
   3. 洋葱模型：包在最外层，记录的是完整耗时（含中间件链 + 路由 + 异常处理）

Q: 中间件的执行顺序？
A: CORS → RequestID → Timing → 路由
   Timing 在外层但 RequestID 先注入，所以 timing 日志里能带 request_id
"""

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logger import logger


class TimingMiddleware(BaseHTTPMiddleware):
    """记录每个请求的处理耗时 + 状态码"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start_time = time.time()
        response = await call_next(request)  # ← 所有中间件 + 路由都在这一行里执行
        duration_ms = (time.time() - start_time) * 1000
        await logger.ainfo(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            request_id=getattr(request.state, "request_id", None),
        )
        return response
