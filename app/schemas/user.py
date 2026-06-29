"""
用户相关 Schema — 区分公开信息和私有信息

=== 面试重点 ===
Q: 为什么分 UserPublic 和 UserProfile？
A: 安全设计——公开接口不返回敏感字段（is_active、邮箱）。
   任何人看其他用户信息 → UserPublic（id、username、avatar）
   自己看自己的信息 → UserProfile（含邮箱、是否禁用）
   如果所有接口用同一个 Schema → /api/users/1 会泄露用户邮箱
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserPublic(BaseModel):
    """用户公开信息 — 查别人时返回这个"""

    id: int
    username: str
    email: str
    avatar: str | None = None
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}  # 允许从 ORM 对象直接转换


class UserProfile(BaseModel):
    """当前用户完整信息 — 查自己时返回这个"""

    id: int
    username: str
    email: str
    avatar: str | None = None
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    """更新个人资料 — 所有字段可选"""

    username: str | None = Field(None, min_length=3, max_length=50)
    email: EmailStr | None = None
    avatar: str | None = Field(None, max_length=500)


class ChangePasswordRequest(BaseModel):
    """修改密码 — 需要旧密码验证"""

    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码")
