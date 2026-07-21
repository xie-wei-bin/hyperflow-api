"""
RBAC 权限模型 — 角色、权限及其多对多关联

=== 面试重点 ===
Q: 为什么用 RBAC 而不是简单的 user/admin 枚举？
A: 1. 可扩展：新增"编辑者"角色无需改代码，配权限即可
   2. 细粒度：权限精确到操作（article:delete），不是粗粒度的"管理员能干啥"
   3. 灵活组合：一个用户可以有多角色，权限取其并集
   4. 审计友好：每个权限有明确标识，排查权限问题时一目了然

Q: 用户-角色和角色-权限为什么都用多对多？
A: 用户-角色多对多：一个用户可以是"编辑者"+"审核员"
   角色-权限多对多：一个角色包含多个权限，一个权限可被多个角色共用
   如果用一对多，用户只能有一个角色，权限也只能属于一个角色→不灵活

Q: Role 和 Permission 为什么用 ORM 模型而不是纯关联表？
A: Permission 需要 description 字段（权限说明），Role 也需要 description 和 created_at。
   如果只是中间关联表（user_role, role_permission）不需要额外字段，用 Table 对象就够了。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# ── 角色-权限关联表（多对多，无额外字段，用 Table 对象） ──
role_permission = Table(
    "role_permission",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True,
           comment="角色ID"),
    Column("permission_id", Integer, ForeignKey("permission.id", ondelete="CASCADE"),
           primary_key=True, comment="权限ID"),
)

# ── 用户-角色关联表（多对多） ──
user_role = Table(
    "user_role",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True,
           comment="用户ID"),
    Column("role_id", Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True,
           comment="角色ID"),
)


class Permission(Base):
    """权限 — 定义系统中所有可控制的操作"""

    __tablename__ = "permission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True,
                                     comment="权限主键ID")
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False,
                                       comment="权限标识，如 article:delete")
    description: Mapped[str | None] = mapped_column(String(255), default=None,
                                                      comment="权限说明")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                  comment="创建时间")

    def __repr__(self) -> str:
        return f"<Permission {self.name}>"


class Role(Base):
    """角色 — 权限的集合，分配给用户"""

    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True,
                                     comment="角色主键ID")
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False,
                                       comment="角色名称：admin / editor / user")
    description: Mapped[str | None] = mapped_column(String(255), default=None,
                                                      comment="角色描述")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                  comment="创建时间")

    # 角色拥有的权限（多对多）
    permissions: Mapped[list[Permission]] = relationship(
        "Permission", secondary=role_permission, lazy="selectin",
    )
    # 拥有此角色的用户（多对多，反向引用）
    users: Mapped[list["User"]] = relationship(
        "User", secondary=user_role, back_populates="roles", lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Role {self.name}>"
