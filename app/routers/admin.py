"""
RBAC 管理路由 — 角色和权限的 CRUD（仅管理员可访问）

=== 面试重点 ===
Q: 为什么 RBAC 的管理接口放在 admin 路由里而不是混在用户路由里？
A: 1. 权限隔离：管理接口需要 role:manage 权限，普通用户不可见
   2. 职责单一：用户路由管用户自己的事，admin 路由管系统管理的事
   3. 安全审计：所有管理操作集中在一个 prefix 下，日志和监控更容易配置
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.middleware.auth import get_current_user, require_permission
from app.models.rbac import Permission, Role, role_permission, user_role
from app.models.user import User
from app.schemas.common import APIResponse

router = APIRouter(prefix="/api/admin", tags=["RBAC 管理"])


# ── 权限列表 ──

@router.get("/permissions", response_model=APIResponse[list[dict]])
async def list_permissions(
    current_user=Depends(require_permission("role:manage")),
    db: AsyncSession = Depends(get_db),
):
    """查看所有权限"""
    result = await db.execute(select(Permission).order_by(Permission.name))
    perms = result.scalars().all()
    return APIResponse(data=[
        {"id": p.id, "name": p.name, "description": p.description} for p in perms
    ])


# ── 角色 CRUD ──

@router.get("/roles", response_model=APIResponse[list[dict]])
async def list_roles(
    current_user=Depends(require_permission("role:manage")),
    db: AsyncSession = Depends(get_db),
):
    """查看所有角色及其权限"""
    result = await db.execute(select(Role).order_by(Role.name))
    roles = result.scalars().all()
    return APIResponse(data=[
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "permissions": [{"id": p.id, "name": p.name} for p in (r.permissions or [])],
            "user_count": len(r.users) if r.users else 0,
        }
        for r in roles
    ])


@router.post("/roles", status_code=201, response_model=APIResponse[dict])
async def create_role(
    name: str,
    description: str | None = None,
    permission_ids: list[int] | None = None,
    current_user=Depends(require_permission("role:manage")),
    db: AsyncSession = Depends(get_db),
):
    """创建新角色并分配权限"""
    # 检查重复
    result = await db.execute(select(Role).where(Role.name == name))
    if result.scalar_one_or_none():
        raise ConflictException("角色名已存在")

    role = Role(name=name, description=description)
    db.add(role)
    await db.flush()

    # 分配权限
    if permission_ids:
        result = await db.execute(select(Permission).where(Permission.id.in_(permission_ids)))
        for perm in result.scalars().all():
            await db.execute(
                role_permission.insert().values(role_id=role.id, permission_id=perm.id)
            )

    await db.flush()
    await db.refresh(role)

    return APIResponse(code=201, message="角色创建成功", data={
        "id": role.id, "name": role.name, "description": role.description,
    })


@router.put("/roles/{role_id}/permissions", response_model=APIResponse[dict])
async def update_role_permissions(
    role_id: int,
    permission_ids: list[int],
    current_user=Depends(require_permission("role:manage")),
    db: AsyncSession = Depends(get_db),
):
    """更新角色的权限（先删后建，保证最终状态等于输入状态）"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise NotFoundException("角色不存在")

    # 面试点：先删后建，保证最终状态 = 输入状态
    # 防止"旧权限残留 + 新权限追加"的 bug
    await db.execute(
        role_permission.delete().where(role_permission.c.role_id == role_id)
    )
    if permission_ids:
        result = await db.execute(select(Permission).where(Permission.id.in_(permission_ids)))
        for perm in result.scalars().all():
            await db.execute(
                role_permission.insert().values(role_id=role_id, permission_id=perm.id)
            )

    await db.flush()
    return APIResponse(message="角色权限已更新")


@router.delete("/roles/{role_id}", response_model=APIResponse[dict])
async def delete_role(
    role_id: int,
    current_user=Depends(require_permission("role:manage")),
    db: AsyncSession = Depends(get_db),
):
    """删除角色（系统保护：不允许删除 admin 角色）"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise NotFoundException("角色不存在")

    if role.name == "admin":
        raise ForbiddenException("不允许删除系统管理员角色")

    await db.delete(role)
    await db.flush()
    return APIResponse(message="角色已删除")


# ── 用户-角色关联 ──

@router.get("/users/{user_id}/roles", response_model=APIResponse[dict])
async def get_user_roles(
    user_id: int,
    current_user=Depends(require_permission("role:manage")),
    db: AsyncSession = Depends(get_db),
):
    """查看用户的角色"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("用户不存在")

    return APIResponse(data={
        "user_id": user.id,
        "username": user.username,
        "roles": [{"id": r.id, "name": r.name} for r in (user.roles or [])],
    })


@router.put("/users/{user_id}/roles", response_model=APIResponse[dict])
async def update_user_roles(
    user_id: int,
    role_ids: list[int],
    current_user=Depends(require_permission("role:manage")),
    db: AsyncSession = Depends(get_db),
):
    """更新用户的角色（先删后建）"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundException("用户不存在")

    # 面试点：同角色权限更新，先删后建保证最终一致性
    await db.execute(
        user_role.delete().where(user_role.c.user_id == user_id)
    )

    if role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
        for role in result.scalars().all():
            await db.execute(
                user_role.insert().values(user_id=user_id, role_id=role.id)
            )

    await db.flush()
    return APIResponse(message="用户角色已更新")
