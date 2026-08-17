"""
秒杀模型 — 商品 + 订单

=== 面试重点 ===
Q: 为什么订单状态用枚举而不是字符串？
A: 枚举在数据库层面是整数，比字符串快；Python 层面有类型检查。
   状态流转：pending → paid → timeout_cancel / completed。

Q: 为什么建联合索引 (product_id, user_id)？
A: 秒杀的核心查询——"这个用户对这个商品是否已下单"。
   联合索引 + UNIQUE 约束 = 数据库层面保证幂等。
   加上 Redis SISMEMBER 快速判断 → 两层防护，和点赞系统一模一样。
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderStatus(str, Enum):
    PENDING = "pending"              # 待支付
    PAID = "paid"                    # 已支付
    TIMEOUT_CANCEL = "timeout_cancel"  # 超时取消
    COMPLETED = "completed"          # 已完成


class Product(Base):
    """秒杀商品"""

    __tablename__ = "seckill_product"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="商品名称")
    total_stock: Mapped[int] = mapped_column(Integer, nullable=False, comment="总库存")
    # 剩余库存由 Redis 维护（seckill:stock:{id}），DB 字段仅做对账基准
    price: Mapped[int] = mapped_column(Integer, nullable=False, comment="价格（分）")
    start_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="秒杀开始时间"
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="秒杀结束时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )


class SeckillOrder(Base):
    """秒杀订单"""

    __tablename__ = "seckill_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("seckill_product.id"), nullable=False, comment="商品ID"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, comment="用户ID"
    )
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus), default=OrderStatus.PENDING, comment="订单状态"
    )
    amount: Mapped[int] = mapped_column(Integer, default=1, comment="购买数量")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, comment="支付时间"
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, comment="取消时间"
    )

    __table_args__ = (
        # 面试点：UNIQUE(product_id, user_id) 数据库层面防重——
        # 同一用户对同一商品不能重复下单。和点赞系统完全一样的模式。
        UniqueConstraint("product_id", "user_id", name="uq_seckill_user_product"),
        Index("ix_seckill_status_created", "status", "created_at"),
    )
