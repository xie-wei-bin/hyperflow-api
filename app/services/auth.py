"""
认证服务 — 注册、登录、Token 刷新

=== 面试重点 ===
Q: 登录时为什么支持用户名和邮箱两种方式？
A: UX 友好——用户可能只记住邮箱忘了用户名。
   实现：WHERE username = :input OR email = :input

Q: flush() 和 commit() 的区别？
A: flush() → 把内存变更发到数据库（可获自增 ID），但事务未提交，其他连接看不到
   commit() → 提交事务，其他连接可见
   注册流程：db.add(user) → flush()（拿 user.id，检查唯一约束）→ get_db() 自动 commit
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictException, UnauthorizedException
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)


async def register(db: AsyncSession, data: RegisterRequest) -> User:
    """用户注册 — 检查用户名和邮箱唯一性"""
    # 检查用户名唯一性
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise ConflictException("用户名已被注册")

    # 检查邮箱唯一性
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise ConflictException("邮箱已被注册")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    await db.flush()  # 发 INSERT 但不提交，获取 user.id
    await db.refresh(user)
    return user


async def login(db: AsyncSession, username: str, password: str) -> dict[str, str]:
    """登录 — 返回 access + refresh token"""
    # | 是 SQLAlchemy 的 OR 操作符：用户名或邮箱匹配
    result = await db.execute(
        select(User).where((User.username == username) | (User.email == username))
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        raise UnauthorizedException("用户名或密码错误")

    if not user.is_active:
        raise UnauthorizedException("账号已被禁用")

    token_data = {"user_id": user.id, "username": user.username, "role": user.role}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


async def get_current_user_info(db: AsyncSession, user_id: int) -> User:
    """获取当前用户完整信息（含邮箱等私有字段）"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise UnauthorizedException("用户不存在")
    return user
