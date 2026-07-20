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

import uuid#标准库，生成全局唯一随机字符串
#collections.abc：Python 抽象集合类型库，用于类型标注
#Awaitable：可等待对象类型，所有 async 函数返回值都是 Awaitable
#Callable：可调用对象类型（函数、方法），用于标注 call_next 是一个异步回调函数
from collections.abc import Awaitable, Callable

import structlog.contextvars
#BaseHTTPMiddleware，Starlette 官方异步中间件基类，自定义中间件必须继承它
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求注入 X-Request-ID（协程安全）
    异步方法，中间件必须异步
    call_next：回调函数，把请求交给下一层中间件 / 路由处理
Callable[[Request], Awaitable[Response]] 类型注解：接收 Request，返回可等待的 Response
    """
#dispatch 所有 Starlette 中间件固定入口方法，拦截请求必经此函数
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 优先取客户端传的 X-Request-ID（微服务链路上游可能已经生成了）
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        #.get读取头字段，不存在则使用第二个参数默认值，uuid.uuid4()生成随机无顺序 UUID（版本 4）
        # contextvars：route/service 层自动注入，req/res 生命周期精准
        #bind_contextvars：绑定上下文变量
        structlog.contextvars.bind_contextvars(request_id=request_id)
        # request.state：兜底方案，保证 Starlette BaseHTTPMiddleware
        # 的 anyio TaskGroup 切换 context 后 timing 日志依然能拿到
        request.state.request_id = request_id

        response = await call_next(request)#把请求往下传递，执行路由接口、业务代码
        response.headers["X-Request-ID"] = request_id

        structlog.contextvars.unbind_contextvars("request_id")
        #unbind_contextvars：解绑删除绑定的 request_id 上下文

        return response
