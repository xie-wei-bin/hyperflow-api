"""
全局异常处理器 — 统一 APIResponse 格式

=== 面试重点 ===
Q: 为什么所有异常都转成 JSONResponse 而不是让 FastAPI 默认处理？
A: 前端需要统一格式：{"code": 200, "message": "success", "data": {...}}
   如果不拦截，不同异常返回不同格式：
   404 → {"detail": "Not Found"}            ← JSON 结构不同
   422 → {"detail": [{"loc":..., "msg":...}]} ← JSON 结构不同
   500 → 纯文本 HTML                      ← 连 JSON 都不是
   前端要写 3 套解析逻辑。统一后，前端只关心 res.data.code

Q: 为什么 ValidationError 要特殊处理，提取字段级错误？
A: Pydantic 的原始错误包含 loc（哪个字段）和 msg（什么原因），
   直接返回前端看不懂，我们要转成：
   {"field": "body.password", "message": "ensure this value has at least 6 characters"}
   这样前端可以在对应输入框下面展示红色提示

Q: IntegrityError → 409 而不是 500？
A: IntegrityError 是"你的操作违反了数据库约束"（如唯一键重复），
   这是客户端的问题（重复注册/重复点赞），应该返回 4xx 让客户端修正
   500 是"服务器内部出 bug 了"，语义不同
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.exceptions import AppException
from app.logger import logger


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """
    处理业务异常（我们自己抛的）

    面试点：exc.message 和 exc.code 直接映射到响应体
    raise NotFoundException("文章不存在") → {"code":404, "message":"文章不存在"}
    """
    await logger.awarning(
        "app.exception",
        exc_type=type(exc).__name__,
        message=exc.message,
        code=exc.code,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.code,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    处理 HTTP 异常（FastAPI 内置，如路由不匹配的 404）

    面试点：HTTPException.detail 可能是 str 或 dict，统一转 str
    """
    await logger.awarning(
        "http.exception",
        status_code=exc.status_code,
        detail=exc.detail,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": None},
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    处理 Pydantic 校验失败 → 422 + 字段级错误

    面试点：把 loc 从 ("body", "password") 转成 "body.password"
    前端可以用 data.errors[0].field 直接定位到表单字段
    """
    errors: list[dict[str, str]] = []
    for error in exc.errors():
        errors.append(
            {
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
            }
        )
    await logger.awarning("validation.error", errors=errors, path=request.url.path)
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": "请求参数校验失败", "data": {"errors": errors}},
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """
    处理数据库约束冲突 → 409

    面试点：前台看到 409 就知道"该用户名已被注册"，不需要解析数据库错误信息
    """
    await logger.aerror("db.integrity_error", detail=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=409,
        content={"code": 409, "message": "数据冲突，资源可能已存在", "data": None},
    )


async def redis_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    处理 Redis 异常 → 503

    面试点：为什么在全局处理器而不是 get_redis() 的 yield 里？
    yield 只包围 yield 这一行，路由里的 await redis.get() 在自己协程中运行，
    yield 的 try/catch 捕获不到 → 必须用全局异常处理器。
    """
    await logger.aerror("redis.error", detail=str(exc), path=request.url.path, exc_info=True)
    return JSONResponse(
        status_code=503,
        content={"code": 503, "message": "服务暂时不可用，请稍后重试", "data": None},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    兜底处理器 — 500 + 完整堆栈

    面试点：exc_info=True 记录完整堆栈到日志，用于排查 Bug
    生产环境返回 "服务器内部错误"，不暴露数据库密码等敏感信息
    开发环境可以从日志中回查
    """
    await logger.aerror(
        "unhandled.exception",
        exc_type=type(exc).__name__,
        detail=str(exc),
        path=request.url.path,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误", "data": None},
    )
