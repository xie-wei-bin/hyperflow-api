"""
认证相关 Schema

=== 面试重点 ===
Q: Pydantic Field 有什么用？
A: EmailStr：自动校验邮箱格式（xx@xx.com），不含 @ → 422
   min_length/max_length：字段长度校验，前端提交 1 个字的密码 → 直接拒绝
   这些校验在路由执行之前就跑完了，不合格的请求根本到不了业务代码
"""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """用户注册请求"""

    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: EmailStr = Field(..., description="邮箱")
    password: str = Field(..., min_length=6, max_length=128, description="密码")


class LoginRequest(BaseModel):
    """登录请求 — 支持用户名或邮箱登录"""

    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")


class LoginResponse(BaseModel):
    """登录响应 — 返回双 Token"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """刷新 Token 请求"""

    refresh_token: str = Field(..., description="刷新令牌")


class TokenData(BaseModel):
    """JWT Payload 数据结构 — 签发 Token 时把用户信息打包进 Payload"""

    user_id: int
    username: str
    role: str
