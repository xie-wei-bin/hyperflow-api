"""
认证中间件 — JWT 依赖注入 + RBAC 权限校验

=== 面试重点 ===
Q: Depends 的原理？和 Django 中间件有什么区别？
A: Depends 是 FastAPI 的依赖注入系统，声明在函数参数上即可：
   current_user: User = Depends(get_current_user)
   FastAPI 自动调用 get_current_user，把返回值注入参数。

Q: RBAC 权限检查 vs 简单的 admin 检查有什么区别？（新增）
A: 旧版：if current_user.role == "admin" → 只能区分"管理员"和"非管理员"
   新版 RBAC：require_permission("article:delete:any") →
   1. 查用户的所有角色 → 2. 查这些角色的所有权限 → 3. 判断目标权限
   好处：新增角色不需要改代码，数据库配权限即可
"""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException, UnauthorizedException
from app.models.rbac import Permission, Role
from app.models.user import User
from app.utils.security import (
    TokenExpiredError,
    TokenInvalidError,
    TokenTypeMismatchError,
    decode_token,
)

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    验证 JWT access token → 返回当前用户

    面试点：每一步检查的意义
    1. credentials is None → 没传 Authorization header → 401
    2. decode_token 返回 None → token 过期/伪造/格式错误 → 401
    3. payload["type"] != "access" → 拿了 refresh token 来糊弄 → 401
    4. user is None → token 里的 user_id 对应的人被删了 → 401
    5. not user.is_active → 账号被管理员禁用 → 403
    """
    if credentials is None:
        raise UnauthorizedException("请先登录")

    # 面试点：通过异常类型区分三种失败，不再混杂在 None 里
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenExpiredError:
        raise UnauthorizedException("Token 已过期，请刷新")
    except TokenTypeMismatchError:
        raise UnauthorizedException("请使用 access token")
    except TokenInvalidError:
        raise UnauthorizedException("Token 无效或已被篡改")

    user_id = payload.get("user_id")
    if user_id is None:
        raise UnauthorizedException("Token 格式错误")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise UnauthorizedException("用户不存在")

    if not user.is_active:
        raise ForbiddenException("账号已被禁用")

    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    验证当前用户是否为管理员（向后兼容）

    Depends 链式调用：
    get_current_admin → get_current_user → get_db
    """
    if current_user.role != "admin":
        raise ForbiddenException("需要管理员权限")
    return current_user


# ── RBAC 权限检查工厂 ──
# 面试点：闭包工厂——外层接收权限名，返回一个新的 Depends
# 用法：Depends(require_permission("article:delete"))
def require_permission(permission: str):
    """
    权限检查依赖工厂

    面试点：通过 用户→角色→权限 多对多链路检查权限
    一次 JOIN 查询完成：user_role + role_permission + permission
    """

    async def check_permission(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        # 一次 JOIN 查用户是否有指定权限
        has_perm = await db.scalar(
            select(Permission)
            .join(Role.permissions)
            .join(Role.users)
            .where(User.id == current_user.id, Permission.name == permission)
            .limit(1)
        )
        if not has_perm:
            raise ForbiddenException(f"需要权限：{permission}")
        return current_user

    return check_permission
