"""
认证路由 — 注册、登录、Token 刷新、获取当前用户

=== 面试重点 ===
Q: @limiter.limit("5/minute") 怎么实现的？会不会阻塞其他用户？
A: slowapi 使用令牌桶算法，每个 IP 独立计数：
   Redis Key: blog:rate_limit:192.168.1.1:/api/auth/register
   用户 A 发了 5 次 → 只对 A 限流，用户 B 不受影响
   实现：每次请求 INCR key → 检查值是否超过 5 → 超过返回 429

Q: 注册和登录为什么限流？
A: 防止暴力破解和恶意注册：
   注册 5/min → 防机器批量注册垃圾账号
   登录 10/min → 防字典攻击（试 100 万个密码组合）
   如果没有限流 → 攻击者可以无限尝试 → 数据库压力大 + 安全风险

Q: Refresh Token 交换新 Token 的安全考虑？
A: 每次刷新时签发新的 refresh token，旧的立即失效（覆盖 Redis）
   这叫 Token Rotation：如果攻击者偷了一个 refresh token，
   合法用户下次刷新时生成新的，攻击者手上的旧 token 就废了
"""

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.middleware.auth import get_current_user
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.auth import LoginRequest, LoginResponse, RefreshRequest, RegisterRequest
from app.schemas.common import APIResponse
from app.schemas.user import UserProfile
from app.services import auth as auth_service
from app.utils.security import decode_token

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/register", status_code=201, response_model=APIResponse[dict])
@limiter.limit("5/minute")
async def register(request: Request, data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册 — 限流 5次/分钟/ip"""
    user = await auth_service.register(db, data)
    return APIResponse(
        code=201,
        message="注册成功",
        data={
            "id": user.id,
            "username": user.username,
            "email": user.email,
        },
    )


@router.post("/login", response_model=APIResponse[LoginResponse])
@limiter.limit("10/minute")
async def login(
    request: Request,
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    登录 — 返回双 Token

    面试点：refresh token 存 Redis 原因：
    1. 服务端可控：改密码/封号时删 key → 强制重新登录
    2. 单设备登录：setex 覆盖旧值 → 旧设备 token 失效
    3. 分布式共享：多实例共享 Redis，任一实例都能验证
    """
    tokens = await auth_service.login(db, data.username, data.password)
    refresh_token = tokens["refresh_token"]
    payload = decode_token(refresh_token)
    if not payload:
        from app.exceptions import UnauthorizedException

        raise UnauthorizedException("Token 格式错误")
    user_id = payload["user_id"]
    await redis.setex(f"blog:refresh_token:{user_id}", 60 * 60 * 24 * 7, refresh_token)
    return APIResponse(data=tokens)


@router.post("/refresh", response_model=APIResponse[LoginResponse])
async def refresh_token(data: RefreshRequest, redis: aioredis.Redis = Depends(get_redis)):
    """
    刷新 Token — Token Rotation

    面试点：Refresh Token Rotation 的安全意义
    每次用旧 refresh token 换新的 → 旧 token 失效 → 攻击者偷到的 token 被废
    """
    payload = decode_token(data.refresh_token)
    if not payload or payload.get("type") != "refresh":
        from app.exceptions import UnauthorizedException

        raise UnauthorizedException("Refresh token 无效或已过期")

    user_id = payload["user_id"]
    stored_token = await redis.get(f"blog:refresh_token:{user_id}")
    if not stored_token or stored_token != data.refresh_token:
        from app.exceptions import UnauthorizedException

        raise UnauthorizedException("Refresh token 已失效")

    from app.utils.security import create_access_token, create_refresh_token

    token_data = {"user_id": user_id, "username": payload["username"], "role": payload["role"]}
    new_refresh = create_refresh_token(token_data)
    await redis.setex(f"blog:refresh_token:{user_id}", 60 * 60 * 24 * 7, new_refresh)
    return APIResponse(
        data={
            "access_token": create_access_token(token_data),
            "refresh_token": new_refresh,
            "token_type": "bearer",
        }
    )


@router.get("/me", response_model=APIResponse[UserProfile])
async def get_me(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """获取当前登录用户信息 — Depends 自动完成认证"""
    user = await auth_service.get_current_user_info(db, current_user.id)
    return APIResponse(data=user)
