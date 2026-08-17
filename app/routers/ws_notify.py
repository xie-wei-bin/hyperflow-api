"""
WebSocket 实时通知路由 — 在线推送 + 离线拉取

=== 面试重点 ===
Q: 为什么 WebSocket 通知需要 Token 认证？
A: 不能让未登录的人随便连 WebSocket 收通知。
   前端 WebSocket 不支持自定义 Header，所以 token 通过 query string 传递：
   ws://localhost:8000/ws/notifications?token=xxx

Q: 实时推送 + 离线拉取的双通道设计？
A: 在线 → WebSocket 实时推送到浏览器
   离线 → 通知存 MySQL，下次打开页面时 GET /api/notifications 拉取
   每条通知都持久化到 MySQL（不管用户在不在线），保证不丢失
"""

import asyncio
import contextlib
import uuid
from datetime import datetime

import structlog.contextvars
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import NotFoundException
from app.logger import logger
from app.middleware.auth import get_current_user
from app.models.notification import Notification
from app.models.user import User
from app.schemas.common import APIResponse
from app.utils.security import (
    TokenExpiredError,
    TokenInvalidError,
    TokenTypeMismatchError,
    decode_token,
)
from app.utils.ws_manager import (
    HEARTBEAT_INTERVAL,
    ConnectionLimitAborted,
    ConnectionLimitExceeded,
    manager,
)

router = APIRouter(tags=["实时通知"])


@router.websocket("/ws/notifications")
async def websocket_notifications(ws: WebSocket, token: str = Query(...)):
    """
    WebSocket 通知连接 — 建立后服务端可主动推送

    面试点：从 query string 取 token 做认证
    WebSocket 握手是 HTTP GET，query string 是唯一定制位置
    """
    # 验证 JWT token（expected_type 内置 type 校验，不再分散在调用方）
    try:
        payload = decode_token(token, expected_type="access")
    except (TokenExpiredError, TokenInvalidError, TokenTypeMismatchError):
        await ws.close(code=4001, reason="Token 无效或已过期")
        return

    user_id = payload.get("user_id")
    if user_id is None:
        await ws.close(code=4001, reason="Token 格式错误")
        return

    # ── 绑定日志上下文（协程局部，仅本连接可见） ──
    connection_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(
        request_id=connection_id, user_id=user_id, connection_id=connection_id,
    )

    # ── 接受连接 ──
    # 面试点：connect 返回唯一 connection_id，支持多连接管理（多标签页/多设备）
    try:
        cid = await manager.connect(user_id, ws, connection_id=connection_id)
    except (ConnectionLimitExceeded, ConnectionLimitAborted):
        # 限流拒绝/握手断开 → connect 内已 close，直接清理日志上下文
        structlog.contextvars.unbind_contextvars("request_id", "user_id", "connection_id")
        return

    try:
        # ── 消息循环（含心跳保活） ──
        # 面试点：任意消息（包括 "ping"）都会延长心跳超时。
        # 服务端后台 HeartbeatLoop 独立检测僵尸连接，不依赖客户端主动 ping。
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=HEARTBEAT_INTERVAL + 10)
            except asyncio.TimeoutError:
                # 客户端长时间无消息 → 主动发 ping 探测
                try:
                    await ws.send_json({"type": "ping"})
                    # 等待 pong 响应
                    pong = await asyncio.wait_for(ws.receive_text(), timeout=10)
                    if pong == "pong":
                        await manager.update_heartbeat(cid)
                        continue
                except Exception:
                    pass
                # ping/pong 失败 → 连接已死 → 退出循环
                break

            if msg == "ping":
                await ws.send_json({"type": "pong"})
            # 任何消息都刷新心跳（包括 ping 和其他自定义消息）
            await manager.update_heartbeat(cid)

    except WebSocketDisconnect:
        await logger.ainfo("ws.disconnected", user_id=user_id, connection_id=cid)
    except asyncio.CancelledError:
        await logger.ainfo("ws.cancelled", user_id=user_id, connection_id=cid)
    except Exception:
        await logger.aerror("ws.error", exc_info=True, user_id=user_id, connection_id=cid)
    finally:
        # ── 精确清理当前连接（不影响同用户其他连接） ──
        await manager.disconnect(cid)
        # 日志上下文解绑（协程局部，不影响其他 ws 连接）
        structlog.contextvars.unbind_contextvars("request_id", "user_id", "connection_id")


# ── RESTful 通知历史 ──

@router.get("/api/notifications", response_model=APIResponse[dict])
async def get_notifications(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False, description="只看未读"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取通知历史（分页）— 离线用户通过此接口拉取未收到的通知"""
    # COUNT
    count_query = select(func.count()).select_from(Notification).where(
        Notification.recipient_id == current_user.id
    )
    if unread_only:
        count_query = count_query.where(Notification.is_read == False)  # noqa: E712
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页查询
    query = (
        select(Notification)
        .where(Notification.recipient_id == current_user.id)
    )
    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712
    query = query.order_by(Notification.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    notifications = result.scalars().all()

    items = [
        {
            "id": n.id,
            "type": n.type,
            "message": n.message,
            "article_id": n.article_id,
            "comment_id": n.comment_id,
            "is_read": n.is_read,
            "sender": {"id": n.sender.id, "username": n.sender.username,
                       "avatar": n.sender.avatar} if n.sender else {},
            "created_at": n.created_at.isoformat(),
        }
        for n in notifications
    ]

    # 统计未读数
    unread_result = await db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.recipient_id == current_user.id,
            Notification.is_read == False,  # noqa: E712
        )
    )
    unread_count = unread_result.scalar() or 0

    return APIResponse(data={
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "unread_count": unread_count,
    })


@router.put("/api/notifications/{notification_id}/read", response_model=APIResponse[dict])
async def mark_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """标记单条通知为已读"""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_id == current_user.id,
        )
    )
    n = result.scalar_one_or_none()
    if not n:
        raise NotFoundException("通知不存在")
    n.is_read = True
    await db.flush()
    return APIResponse(message="已标记为已读")


@router.put("/api/notifications/read-all", response_model=APIResponse[dict])
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """一键标记所有通知为已读"""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.recipient_id == current_user.id,
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
    )
    await db.flush()
    return APIResponse(message=f"已标记 {result.rowcount} 条通知为已读")


# ── 辅助函数（供其他路由调用） ──

async def push_notification(
    db: AsyncSession,
    recipient_id: int,
    sender_id: int,
    notif_type: str,
    message: str,
    article_id: int | None = None,
    comment_id: int | None = None,
) -> None:
    """
    创建通知 + 尝试 WebSocket 推送

    面试点：不管用户在不在线，先存 MySQL 再尝试推送。
    推送失败（用户离线）→ 通知已持久化，下次拉取时能看到。
    """
    if recipient_id == sender_id:
        return  # 不给自己发通知

    # 1. 持久化到 MySQL
    notification = Notification(
        recipient_id=recipient_id,
        sender_id=sender_id,
        type=notif_type,
        message=message,
        article_id=article_id,
        comment_id=comment_id,
    )
    db.add(notification)
    await db.flush()

    # 2. 加载 sender 信息（用于 WebSocket 推送的 JSON）
    from sqlalchemy import select as _sel
    sender_result = await db.execute(_sel(User).where(User.id == sender_id))
    sender = sender_result.scalar_one_or_none()

    # 3. 尝试实时推送
    pushed = await manager.send_to_user(recipient_id, {
        "type": "notification",
        "data": {
            "id": notification.id,
            "type": notif_type,
            "message": message,
            "article_id": article_id,
            "comment_id": comment_id,
            "sender": {"id": sender.id, "username": sender.username,
                       "avatar": sender.avatar} if sender else {},
            "created_at": notification.created_at.isoformat(),
        },
    })
    if pushed:
        # 推送成功 → 标记为已读（用户已经看到了）
        notification.is_read = True
        await db.flush()

    # 4. 异步发送邮件通知（Celery — 不阻塞 API 响应）
    # 面试点：邮件发送通过 Celery Worker 异步执行，API 立即返回。
    # 即使邮件发送失败（Celery 自动重试 3 次），也不影响 WebSocket 推送。
    try:
        recipient_result = await db.execute(_sel(User).where(User.id == recipient_id))
        recipient = recipient_result.scalar_one_or_none()
        if recipient and recipient.email:
            from app.tasks.email_tasks import send_email_notification

            send_email_notification.delay(
                to_email=recipient.email,
                subject=f"博客系统通知 — {message[:30]}...",
                body=f"用户 {sender.username if sender else '某人'} {message}\n\n"
                     f"请登录博客系统查看详情。",
            )
    except Exception:
        pass  # Celery 投递失败不影响主流程（WebSocket 推送已成功）
