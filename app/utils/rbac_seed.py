"""
RBAC 默认数据初始化 — 创建默认角色和权限，首次启动时自动执行

=== 面试重点 ===
Q: 为什么要写 seed 而不是手动 INSERT？
A: 1. 可重复执行（幂等）：重复启动不会报错，已存在的跳过
   2. 代码即文档：看这个文件就知道系统有哪些角色和权限
   3. CI/测试友好：新环境一键初始化，不需要手动导 SQL

Q: 默认角色和权限怎么设计？
A: admin(管理员) — 所有权限，含管理用户/角色
   editor(编辑者) — 管理文章和评论，不能管理用户
   user(普通用户) — 创建文章、评论、点赞收藏
   遵循最小权限原则：每个角色只给需要的权限
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logger import logger
from app.models.rbac import Permission, Role, role_permission, user_role
from app.models.user import User

# ── 权限清单（面试点：权限命名规范 {资源}:{操作}） ──
PERMISSIONS = [
    # 文章
    ("article:create", "创建文章"),
    ("article:update", "编辑自己文章"),
    ("article:update:any", "编辑任何文章"),
    ("article:delete", "删除自己文章"),
    ("article:delete:any", "删除任何文章"),
    # 评论
    ("comment:create", "发表评论"),
    ("comment:delete", "删除自己评论"),
    ("comment:delete:any", "删除任何评论"),
    # 分类
    ("category:create", "创建分类"),
    ("category:update", "编辑分类"),
    ("category:delete", "删除分类"),
    # 标签
    ("tag:create", "创建标签"),
    ("tag:delete", "删除标签"),
    # 用户管理
    ("user:view", "查看用户信息"),
    ("user:disable", "禁用/启用用户"),
    # 角色管理
    ("role:manage", "管理角色和权限分配"),
]

# ── 角色-权限分配 ──
ROLE_PERMISSIONS = {
    "admin": [
        "article:create", "article:update", "article:update:any",
        "article:delete", "article:delete:any",
        "comment:create", "comment:delete", "comment:delete:any",
        "category:create", "category:update", "category:delete",
        "tag:create", "tag:delete",
        "user:view", "user:disable",
        "role:manage",
    ],
    "editor": [
        "article:create", "article:update", "article:update:any",
        "article:delete", "article:delete:any",
        "comment:create", "comment:delete", "comment:delete:any",
    ],
    "user": [
        "article:create", "article:update", "article:delete",
        "comment:create", "comment:delete",
    ],
}


async def seed_rbac(db: AsyncSession) -> None:
    """初始化默认角色和权限（幂等，可重复执行）"""

    # ── 1. 创建权限 ──
    perm_map: dict[str, Permission] = {}
    for name, desc in PERMISSIONS:
        result = await db.execute(select(Permission).where(Permission.name == name))
        existing = result.scalar_one_or_none()
        if existing:
            perm_map[name] = existing
        else:
            perm = Permission(name=name, description=desc)
            db.add(perm)
            await db.flush()
            perm_map[name] = perm

    # ── 2. 创建角色 + 分配权限 ──
    for role_name, perm_names in ROLE_PERMISSIONS.items():
        result = await db.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(name=role_name, description=f"系统默认{role_name}角色")
            db.add(role)
            await db.flush()

        # 给角色关联权限（幂等：先清旧关联再重建，或检查后添加）
        for pname in perm_names:
            perm = perm_map[pname]
            # 检查关联是否已存在
            from sqlalchemy import and_
            result = await db.execute(
                select(role_permission).where(
                    and_(
                        role_permission.c.role_id == role.id,
                        role_permission.c.permission_id == perm.id,
                    )
                )
            )
            if result.first() is None:
                await db.execute(
                    role_permission.insert().values(role_id=role.id, permission_id=perm.id)
                )

    await db.flush()

    # ── 3. 给已有用户分配默认角色 ──
    # 面试点：根据旧 role 字段迁移到新 RBAC 角色
    result = await db.execute(select(Role))
    role_map = {r.name: r for r in result.scalars().all()}

    result = await db.execute(select(User))
    for user in result.scalars().all():
        # 检查是否已分配角色（已有则不重复）
        if user.roles:
            continue

        # 根据旧 role 枚举字段分配对应的 RBAC 角色
        if user.role == "admin":
            if "admin" in role_map:
                await db.execute(
                    user_role.insert().values(user_id=user.id, role_id=role_map["admin"].id)
                )
        else:
            # 所有旧 user 角色的用户 → 分配新 user 角色
            if "user" in role_map:
                await db.execute(
                    user_role.insert().values(user_id=user.id, role_id=role_map["user"].id)
                )

    await db.flush()
    await logger.ainfo("rbac.seed.done", permissions=len(perm_map), roles=len(role_map))
