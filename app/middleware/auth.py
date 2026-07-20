"""
认证中间件 — JWT 依赖注入

=== 面试重点 ===
Q: Depends 的原理？和 Django 中间件有什么区别？
A: Depends 是 FastAPI 的依赖注入系统，声明在函数参数上即可：
   current_user: User = Depends(get_current_user)
   FastAPI 自动调用 get_current_user，把返回值注入参数。
   对比 Django 中间件：Django 中间件是全局的，每个请求都跑。
   Depends 是声明式的——只在声明的路由上执行，更精细。

Q: 为什么不把认证逻辑写在路由函数里，而是用 Depends？
A: 1. DRY：100 个接口要认证，不用复制粘贴 100 次
   2. 测试：可以 override get_current_user，测试时跳过真实认证
   3. 解耦：路由不关心"怎么认证的"，只关心拿到了 user
   4. 可替换：未来换成 OAuth2.0/OIDC，路由代码一行不改

Q: HTTPBearer 和手动取 Header 的区别？
A: HTTPBearer 自动从 Authorization: Bearer <token> 中提取 token
   还会在 Swagger 文档里加上 🔒 图标和 Authorize 按钮
   手动取 Header：request.headers.get("Authorization") 然后 split(" ")[-1]
   容易出错，如 Bearer 大小写、空格数量等边界情况

Q: 怎么防止攻击者拿 refresh token 当 access token 用？
A: 检查 payload["type"] == "access"
   refresh token 的 type 是 "refresh"，无法通过此校验
"""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ForbiddenException, UnauthorizedException
from app.models.user import User
from app.utils.security import decode_token

# 面试点：HTTPBearer(auto_error=False)
# auto_error=False → token 缺失时不自动报 403，返回 None
# 这样未登录访问公开接口不会报错，需要认证的接口再检查
#FastAPI 内置的提取 Authorization: Bearer xxx 请求头工具，专门拿 JWT
security = HTTPBearer(auto_error=False)
"""
credentials: HTTPAuthorizationCredentials | None = Depends(security)
依赖 security 自动解析请求头 Bearer token；没传则为 None
db: AsyncSession = Depends(get_db)
自动注入异步数据库会话，用来根据 token 里的 user_id 查询真实用户
"""

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

    用法: current_user: User = Depends(get_current_user)
    """
    if credentials is None:
        raise UnauthorizedException("请先登录")

    payload = decode_token(credentials.credentials)#credentials.credentials是这个对象内部存储 JWT 字符串的字段
    if payload is None:
        raise UnauthorizedException("Token 无效或已过期")

    if payload.get("type") != "access":
        raise UnauthorizedException("请使用 access token")

    user_id = payload.get("user_id")
    if user_id is None:
        raise UnauthorizedException("Token 格式错误")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    """
    scalar_one_or_none() 是异步 SQLAlchemy 专用取值方法，含义：
如果查到一条匹配数据：返回 User ORM 实体对象；
如果一条都没查到：返回 None；
如果查出多条（主键唯一不可能出现）：直接抛异常
"""

    if user is None:
        raise UnauthorizedException("用户不存在")

    if not user.is_active:#用户类中的布尔值is_active
        raise ForbiddenException("账号已被禁用")

    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    验证当前用户是否为管理员

    面试点：Depends 链式调用
    get_current_admin 依赖 get_current_user → get_current_user 依赖 get_db
    FastAPI 自动解析依赖树，先调用 get_db → get_current_user → get_current_admin
    这就是 Depends 的强大之处：像搭积木一样组合依赖

    用法: admin: User = Depends(get_current_admin)
    """
    if current_user.role != "admin":
        raise ForbiddenException("需要管理员权限")
    return current_user
