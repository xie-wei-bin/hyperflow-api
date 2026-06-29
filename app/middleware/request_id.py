"""
请求 ID 中间件 — 为每个请求生成唯一 X-Request-ID

=== 面试重点 ===
Q: request.state 和 contextvars 有什么区别？
A: request.state 绑定在 Starlette Request 对象上，同一个请求内 OK，但不能跨协程。
   contextvars 是 Python 3.7+ 内置的协程安全变量：
   - 每个协程有自己独立的"上下文副本"
   - await 切换协程时自动保存/恢复，不需要手动传递
   - structlog 的 merge_contextvars 会自动把绑定的变量注入每条日志
   最终效果：同一个 request_id 出现在该请求的所有日志中，中间件层、路由层、service 层全链路串联
"""

import uuid
from collections.abc import Awaitable, Callable

import structlog.contextvars
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求注入 X-Request-ID（协程安全）"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 优先取客户端传的 X-Request-ID（微服务链路上游可能已经生成了）
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # 面试点：用 structlog.contextvars.bind_contextvars 而不是 request.state
        # contextvars 在 await 切换协程时自动传递，整个请求链路都能取到
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        # 面试点：响应结束后清理上下文，防止泄露到下一个无关请求
        structlog.contextvars.unbind_contextvars("request_id")

        return response
