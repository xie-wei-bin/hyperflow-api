"""
请求耗时中间件 — 记录每个请求的处理时间

=== 面试重点 ===
Q: 为什么用中间件而不是在每个路由里手动计时？
A: 1. DRY：100 个路由不用写 100 次 time.time()
   2. 统一格式：所有请求的耗时日志格式一致，日志平台好检索
   3. 洋葱模型：包在最外层，记录的是完整耗时（含中间件链 + 路由 + 异常处理）

Q: 中间件的执行顺序？
A请求下行执行顺序：CORS（外层） → RequestID → Timing → 路由
RequestID 在 Timing 外层，请求进入 Timing 前已完成 request_id 注入，因此 Timing 日志能稳定读取到追踪 ID，无缺失风险。
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
        response = await call_next(request)
        duration_ms = (time.time() - start_time) * 1000
        await logger.ainfo(
            "request.completed",#日志标识名，用来区分日志类型，过滤所有请求完成日志
            method=request.method,#请求方式：GET / POST / PUT / DELETE
            path=request.url.path,#请求接口路由路径，如 /article/list，不含域名、查询参数
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            # contextvars 在 BaseHTTPMiddleware 中可能丢失 context，
            # request.state.request_id 作为保底，确保 timing 日志一定带 ID
            request_id=getattr(request.state, "request_id", None),
            #getattr(对象, 属性名, 默认值)：安全读取对象属性
        )
        return response
