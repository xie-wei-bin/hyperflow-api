"""
博客系统 API — FastAPI 应用入口

=== 面试重点 ===
Q: FastAPI 相比 Flask/Django 有什么优势？
A: 1. 原生异步（Flask 需要额外插件）
   2. 自动生成 OpenAPI 文档（Swagger），Django 需要 drf-spectacular
   3. Pydantic 自动校验请求体，Django DRF 需要手写 Serializer
   4. Depends 依赖注入系统，比 Django 的中间件更灵活
   5. 性能：Starlette 底层，接近 Node.js 吞吐

Q: 中间件和异常处理器的执行顺序？
A: 请求 → RequestID → Timing → 路由 → 异常处理器 → 响应
   RequestID 在最外层：先把 request_id 绑定好，内层 Timing 记录日志时才能拿到
   Timing 在内层：记录完整耗时中间件链，但 request_id 已由外层注入

Q: lifespan 和 @app.on_event("startup") 的区别？
A: lifespan 是新的推荐方式，用一个 async with 管理启动和关闭。
   好处：可以在 yield 之前启动资源，在 yield 之后清理资源。
         on_event 旧写法做不到"先启 A，再启 B，关时反序"的顺序控制
"""

import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import RedisError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.config import settings
from app.exception_handlers import (
    app_exception_handler,
    http_exception_handler,
    integrity_error_handler,
    redis_error_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.exceptions import AppException
from app.limiter import limiter
from app.logger import logger
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.timing import TimingMiddleware
from app.routers import (
    admin,
    article,
    auth,
    category,
    comment,
    health,
    search,
    tag,
    user,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    面试点：yield 前 = 启动时运行，yield 后 = 关闭时运行
    用 create_task 启动后台任务，shutdown 时 cancel 保证优雅退出
    """
    import asyncio as _asyncio

    await logger.ainfo("app.startup", message="博客系统 API 启动中...")

    # ── RBAC 初始化 ──
    # 面试点：启动时自动 seed 默认角色和权限（幂等，可重复执行）
    from app.database import async_session

    from app.utils.rbac_seed import seed_rbac

    async with async_session() as seed_db:
        await seed_rbac(seed_db)
        # seed_rbac 内部已 commit，这里不需要额外操作

    # 面试点：用 asyncio.create_task 而不是 threading.Thread
    from app.utils.sync_views import run_sync_loop

    sync_task = _asyncio.create_task(run_sync_loop(300))

    yield  # ← 这里开始处理请求

    await logger.ainfo("app.shutdown", message="博客系统 API 正在关闭...")
    sync_task.cancel()
    with contextlib.suppress(_asyncio.CancelledError):
        await sync_task

    # 优雅释放连接池资源，防止 Redis/MySQL 端产生 CLOSE_WAIT
    from app.database import engine as _db_engine
    from app.redis_client import _raw_redis as _redis_raw

    await _redis_raw.close()
    await _db_engine.dispose()
    await logger.ainfo("app.shutdown", message="连接池已释放")


# 面试点：FastAPI() 的参数会被渲染到 Swagger 页面
app = FastAPI(
    title="博客系统 API",
    description="企业级博客后端系统，支持用户认证、文章管理、评论互动、收藏点赞、全文搜索、热门排行",
    version="0.1.0",
    lifespan=lifespan,
)

# ── 接口限流 ──────────────────────────────────
# 面试点：为什么选 slowapi 而不是自己实现？
# slowapi 内置令牌桶算法，支持 Redis 分布式限流（多实例共享计数器）
# 自己实现需要处理滑动窗口、并发安全、Redis 原子操作等复杂问题
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# ── CORS 中间件 ────────────────────────────────
# 面试点：CORS 为什么不能简化成 allow_origins=["*"]？
# 安全原因：allow_origins=["*"] 配合 allow_credentials=True 是致命组合
# 任何网站都能带着你浏览器的 Cookie 调你的 API
# 正确做法：明确列出允许的域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# ── 自定义中间件 ────────────────────────────────
# 面试点：add_middleware 后加的在外层（洋葱模型）压栈
# RequestID 必须在外层：先绑定 ID → Timing 内层记录日志时才能拿到
app.add_middleware(TimingMiddleware)# 先注册 Timing → 内层
app.add_middleware(RequestIDMiddleware)# 后注册 RequestID → 外层，最先拦截请求

# ── 全局异常处理器 ──────────────────────────────
# 面试点：为什么注册 5 个异常处理器？
# 异常类型越具体，错误信息越精确：
#   AppException     → 我们自己抛的（如"用户不存在"）
#   HTTPException    → FastAPI 内置（如 404）
#   ValidationError  → Pydantic 校验失败 → 422 带字段级详情
#   IntegrityError   → 数据库约束冲突 → 409（唯一键重复）
#   Exception        → 兜底，500 + 完整堆栈日志
# 面试官可能追问：顺序重要吗？→ Exception 放最后，否则前面全部被吞
app.add_exception_handler(RedisError, redis_error_handler)
app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(IntegrityError, integrity_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_exception_handler)  # 兜底必须最后

# ── 注册路由 ───────────────────────────────────
# 面试点：每个 router 是一个 APIRouter，有独立 prefix
# 好处：模块内聚，人员分工不冲突，方便拆微服务
app.include_router(health.router)
app.include_router(auth.router)  # prefix=/api/auth
app.include_router(user.router)  # prefix=/api/users
app.include_router(article.router)  # prefix=/api/articles
app.include_router(category.router)  # prefix=/api/categories
app.include_router(tag.router)  # prefix=/api/tags
app.include_router(comment.router)  # prefix=/api（评论路由前缀特殊）
app.include_router(search.router)  # prefix=/api
app.include_router(admin.router)  # prefix=/api/admin — RBAC 管理接口
