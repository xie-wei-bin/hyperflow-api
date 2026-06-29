"""
自定义异常类

=== 面试重点 ===
Q: 为什么定义自己的异常类而不是直接用 HTTPException？
A: 1. 语义清晰：raise NotFoundException("文章不存在") 比
      raise HTTPException(404, "文章不存在") 更可读
   2. 分层隔离：业务层不应该 import HTTP 相关的东西
      service 层只抛 AppException，路由层不需要 try/catch
   3. 统一处理：所有 AppException 子类被一个 handler 捕获，不会遗漏

Q: 异常处理流程是怎样的？
A: service 抛出 NotFoundException
   → exception_handlers.py 的 app_exception_handler 捕获
   → 转成 JSONResponse({"code": 404, "message": "文章不存在"})
   → 前端收到的永远是统一格式 {"code": xxx, "message": "xxx", "data": null}
   前端不需要：
   fetch().then(res => { if(res.status===404)... else if(res.status===409)... })
   直接看 res.data.code 即可
"""


class AppException(Exception):  # noqa: N818
    """
    应用基础异常，所有业务异常都继承这个

    面试点：相比 return {"error": "xxx"} 的方式
    异常的好处：
    1. 调用栈清晰：不需要层层 check if result.has_error
    2. 事务回滚：Exception 触发 get_db 里的 rollback
    3. 日志集中：handler 里统一打日志，不需要到处写
    """

    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code
        super().__init__(self.message)


class NotFoundException(AppException):
    """资源不存在 — 404"""

    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, code=404)


class UnauthorizedException(AppException):
    """未认证 — 401（没登录或 token 过期）"""

    def __init__(self, message: str = "请先登录"):
        super().__init__(message, code=401)


class ForbiddenException(AppException):
    """无权限 — 403（登录了但不是管理员/不是作者）"""

    def __init__(self, message: str = "无权执行此操作"):
        super().__init__(message, code=403)


class ConflictException(AppException):
    """资源冲突 — 409（重复注册、重复点赞等）"""

    def __init__(self, message: str = "资源已存在"):
        super().__init__(message, code=409)


class BadRequestException(AppException):
    """请求参数错误 — 400"""

    def __init__(self, message: str = "请求参数有误"):
        super().__init__(message, code=400)
