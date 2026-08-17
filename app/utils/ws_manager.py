"""
WebSocket 连接管理器 — 管理用户实时通知连接

=== 面试重点 ===
Q: 为什么用 dict 存连接而不是 Redis Pub/Sub？
A: WebSocket 对象不可序列化，必须存在应用进程内存中。
   当前单实例部署用内存 dict；多实例扩展时通过 Redis Pub/Sub 做消息广播——
   每个实例订阅通知频道，收到消息推给本地连接的用户。
   接口不变（manager 对外暴露 send_to_user），换实现只改这个文件。

Q: 多个用户同时在线怎么管理？
A: user_id → {connection_id: WebSocket} 一对多映射。
   支持多标签页/手机+PC 同时在线，每个连接有独立 connection_id，
   disconnect 精确删除指定连接，不会误删同用户的其他连接。

Q: 2025.08 从单连接升级到多连接解决了哪些问题？
A: 8 个缺陷全部修复：
   1. contextvars bug：disconnect 不再解绑日志上下文（协程局部变量不应跨协程操作）
   2. 多标签页支持：dict[int, WebSocket] → dict[int, dict[str, WebSocket]]
   3. 心跳保活：服务端主动检测超时连接，剔除僵尸 WebSocket
   4. 连接限流：全局最大连接数 + 单用户最大连接数
   5. send_to_user 健壮性：逐连接异常捕获 + 失败自动清理
   6. disconnect 精确删除：传 connection_id 而非 user_id
   7. 优雅关闭：shutdown 遍历所有连接主动 close
   8. Redis Pub/Sub 多实例广播：lifespan 注入 Redis + 本机优先推送 + 自动重连
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.logger import logger


# ── 配置常量 ────────────────────────────────────────────
MAX_GLOBAL_CONNECTIONS = 10_000     # 全局最大 WebSocket 连接数
MAX_PER_USER_CONNECTIONS = 5        # 单用户最大连接数（多标签页/多设备）
HEARTBEAT_INTERVAL = 30             # 服务端心跳检测间隔（秒）
HEARTBEAT_TIMEOUT = 90              # 客户端超时未响应视为僵尸连接（秒）


@dataclass
class WSConnection:
    """WebSocket 连接元数据"""
    ws: WebSocket
    connection_id: str
    user_id: int
    connected_at: float = field(default_factory=time.monotonic)
    last_heartbeat: float = field(default_factory=time.monotonic)


class ConnectionManager:
    """
    WebSocket 连接管理器（2025.08 增强版）

    核心改进：
    - 支持多连接（user_id → {connection_id: WSConnection}）
    - 心跳保活，自动剔除僵尸连接
    - 连接数限流保护
    - 修复 contextvars 跨协程污染 bug
    - 可选 Redis Pub/Sub 多实例广播
    """

    def __init__(self, redis=None):
        # user_id → {connection_id: WSConnection}
        self._connections: dict[int, dict[str, WSConnection]] = {}
        # connection_id → user_id（反向索引，O(1) disconnect）
        self._conn_to_user: dict[str, int] = {}
        # 并发锁：保护 _connections / _conn_to_user 读写（多个 ws 协程并发 connect/disconnect）
        self._lock = asyncio.Lock()
        # Redis 客户端（可选，用于多实例广播）
        self._redis = redis
        # 心跳后台任务
        self._heartbeat_task: asyncio.Task[Any] | None = None
        # 推送计数器（监控用）
        self.push_success: int = 0
        self.push_failed: int = 0

    # ── 连接管理 ────────────────────────────────────────

    async def connect(
        self,
        user_id: int,
        ws: WebSocket,
        connection_id: str | None = None,
    ) -> str:
        """
        接受 WebSocket 连接，返回 connection_id

        面试点：
        1. accept 只调一次（WebSocket 协议要求 accept 后才能 close 带状态码）
        2. 只捕获 WebSocketDisconnect（不吞未知异常）
        3. 一次 asyncio.Lock：cid 冲突检查 + 限流检查 + 写入索引 —— 原子执行
           判断条件和写入操作在同一临界区，消灭 TOCTOU 竞态窗口
        4. close 放锁外 + suppress 保护（锁内不做 IO）
        5. 日志数值在锁内读取到局部变量，锁外打印（不在锁外读共享字典）

        Raises:
            ConnectionLimitExceeded: 连接数超限 / cid 冲突
            ConnectionLimitAborted: 客户端握手期间断开
        """
        # ① 先 accept（只一次，WebSocket 协议要求 accept 后才能 close 带 code）
        try:
            await ws.accept()
        except WebSocketDisconnect:
            raise ConnectionLimitAborted("客户端在握手期间断开")

        cid = connection_id or str(uuid.uuid4())

        # ② 一次加锁：cid 冲突检查 + 限流检查 + 写入索引，原子执行
        # 提前初始化所有锁外使用的局部变量，消除 pyright/mypy 类型告警 + 防后续改代码 NameError
        should_close = False
        close_code: int = 1008
        close_reason: str = ""
        _total_user = 0
        _global_total = 0

        async with self._lock:
            # cid 唯一性校验（读共享字典也要在锁内）
            if cid in self._conn_to_user:
                should_close = True
                close_code, close_reason = 1008, "连接 ID 冲突"
            # 全局连接数检查
            elif len(self._conn_to_user) >= MAX_GLOBAL_CONNECTIONS:
                should_close = True
                close_code, close_reason = 1008, "服务器连接数已满"
            # 单用户连接数检查
            elif len(self._connections.get(user_id, {})) >= MAX_PER_USER_CONNECTIONS:
                should_close = True
                close_code, close_reason = 1008, f"单用户最多 {MAX_PER_USER_CONNECTIONS} 个连接"
            else:
                # 原子：限流通过 → 立即写入双索引（与判断在同一临界区，无 TOCTOU 窗口）
                conn = WSConnection(ws=ws, connection_id=cid, user_id=user_id)
                if user_id not in self._connections:
                    self._connections[user_id] = {}
                self._connections[user_id][cid] = conn
                self._conn_to_user[cid] = user_id
                _total_user = len(self._connections[user_id])
                _global_total = len(self._conn_to_user)

        # ③ 锁外：限流拒绝 → close + raise
        if should_close:
            with contextlib.suppress(Exception):
                await ws.close(code=close_code, reason=close_reason)
            raise ConnectionLimitExceeded(close_reason)

        # ④ 锁外：日志（用锁内读取的局部变量，不碰共享字典）
        await logger.ainfo(
            "ws.connected",
            user_id=user_id,
            connection_id=cid,
            total_user_conns=_total_user,
            global_conns=_global_total,
        )
        return cid

    async def disconnect(self, connection_id: str) -> None:
        """
        移除指定连接（精确删除，不影响同用户其他连接）

        面试点：旧版 disconnect(user_id) 会误删用户所有连接。
        新版通过 connection_id 精确定位，只删这一条。
        不操作 contextvars——那是 websocket 协程自己的事。
        asyncio.Lock 保护共享字典并发读写。
        """
        async with self._lock:
            user_id = self._conn_to_user.pop(connection_id, None)
            if user_id is None:
                return  # 已经被清理过了

            user_conns = self._connections.get(user_id)
            if user_conns:
                user_conns.pop(connection_id, None)
                if not user_conns:
                    self._connections.pop(user_id, None)
                remaining = len(user_conns)
            else:
                remaining = 0

        # 日志放锁外
        await logger.ainfo(
            "ws.disconnected",
            user_id=user_id,
            connection_id=connection_id,
            remaining_conns=remaining,
        )

    # ── 消息推送 ────────────────────────────────────────

    async def send_to_user(self, user_id: int, data: dict) -> bool:
        """
        向指定用户的所有连接推送消息

        返回: True=至少一条推送成功, False=用户完全不在线

        面试点：遍历用户所有连接逐一发送，单个失败自动清理僵尸连接。
        旧版一个异常就整条 return False，新版本每条独立处理。
        """
        user_conns = self._connections.get(user_id)
        if not user_conns:
            return False

        any_success = False
        dead_conns: list[str] = []

        for cid, conn in list(user_conns.items()):
            try:
                await conn.ws.send_json(data)
                any_success = True
                self.push_success += 1
            except Exception:
                # 连接已死 → 标记清理
                dead_conns.append(cid)
                self.push_failed += 1

        # 清理死连接
        for cid in dead_conns:
            await self.disconnect(cid)

        if dead_conns:
            await logger.awarning(
                "ws.cleaned_dead_connections",
                user_id=user_id,
                cleaned_count=len(dead_conns),
            )

        # 如果有 Redis，发布到频道让其他实例也推送
        # 面试点：不管本机有没有，一律 publish（简单方案）。
        # 原因：用户可能多标签页分散在多个实例，本机推送成功 ≠ 所有实例都已送达。
        # 每个实例收到广播后各自查本地连接，有则推送——保证多实例多标签页全覆盖。
        # 代价：每次推送都走一次 Redis publish，中小规模完全可接受。
        # 进阶方案见 24-问题修改记录.md 建议②：Redis 维护 user_id→[node_id] 精准定向。
        if self._redis:
            try:
                await self._redis.publish(
                    "ws:broadcast",
                    json.dumps({"user_id": user_id, "data": data}, default=str),
                )
            except Exception:
                pass  # Redis 不可用不影响本地推送

        return any_success

    async def broadcast_to_all(self, data: dict) -> int:
        """
        向所有在线用户广播消息（本机 + 跨实例）

        返回: 本机成功推送的连接数

        面试点：遍历本机用户推送 + publish 全局频道让其他实例也广播。
        send_to_user 只推指定用户；broadcast_to_all 推所有人，
        需要单独发一条 "ws:broadcast_all" 频道，其他实例收到后遍历本机用户。
        """
        count = 0
        for user_id in list(self._connections.keys()):
            if await self.send_to_user(user_id, data):
                count += 1

        # 跨实例：发布到全局广播频道，其他实例收到后遍历本机用户推送
        if self._redis:
            try:
                await self._redis.publish(
                    "ws:broadcast_all",
                    json.dumps(data, default=str),
                )
            except Exception:
                pass

        return count

    # ── 心跳检测 ────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """
        后台心跳检测协程：定期扫描所有连接，关闭超时未响应的僵尸连接

        面试点：不依赖客户端的 ping 消息，服务端主动检测。
        客户端即使不发 ping，服务端也能在 HEARTBEAT_TIMEOUT 秒后
        清理僵尸连接。比"等客户端 ping → 服务端 pong"更可靠。
        """
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = time.monotonic()
            dead_conns: list[str] = []

            for user_conns in self._connections.values():
                for cid, conn in user_conns.items():
                    if now - conn.last_heartbeat > HEARTBEAT_TIMEOUT:
                        dead_conns.append(cid)

            for cid in dead_conns:
                user_id = self._conn_to_user.get(cid)
                try:
                    # 尝试关闭僵尸连接
                    conn = self._connections.get(user_id, {}).get(cid)
                    if conn:
                        with contextlib.suppress(Exception):
                            await conn.ws.close(code=1001, reason="心跳超时")
                except Exception:
                    pass
                await self.disconnect(cid)

            if dead_conns:
                await logger.awarning(
                    "ws.heartbeat_cleaned_zombies",
                    cleaned_count=len(dead_conns),
                    remaining_global=len(self._conn_to_user),
                )

    def start_heartbeat(self) -> None:
        """启动心跳检测后台任务（在 lifespan 中调用）"""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        """停止心跳检测"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None

    async def update_heartbeat(self, connection_id: str) -> None:
        """更新连接的心跳时间（websocket 收到任何消息时调用）"""
        async with self._lock:
            user_id = self._conn_to_user.get(connection_id)
            if user_id is None:
                return
            conn = self._connections.get(user_id, {}).get(connection_id)
            if conn:
                conn.last_heartbeat = time.monotonic()

    # ── 优雅关闭 ────────────────────────────────────────

    async def shutdown(self) -> None:
        """
        优雅关闭：通知所有在线客户端 + 关闭所有连接

        面试点：服务重启时遍历所有连接主动 close，
        客户端收到关闭事件后自动重连，减少感知到的断线时间。

        并发安全（3 个修复）：
        1. 锁内快照连接引用 + 清空字典——避免遍历时 connect/disconnect 修改字典
           导致 "dictionary changed size during iteration"
        2. asyncio.gather 并发关闭——几百上千连接串行 close 会超过 K8s preStop 超时
        3. clear() 放锁内——避免和新连接写入产生数据竞争
        """
        # ① 锁内：快照所有连接 + 清空索引（纯内存操作，不 await）
        async with self._lock:
            snapshot: list[WSConnection] = [
                conn
                for user_conns in self._connections.values()
                for conn in user_conns.values()
            ]
            total_conns = len(self._conn_to_user)
            total_users = len(self._connections)
            self._connections.clear()
            self._conn_to_user.clear()


        await logger.ainfo(
            "ws.shutdown.start",
            total_connections=total_conns,
            total_users=total_users,
        )

        await self.stop_heartbeat()

        # ② 锁外：并发关闭所有连接（不持锁做 IO）
        shutdown_msg = {
            "type": "server_shutdown",
            "message": "服务器正在重启，请稍后重连",
        }

        async def _close_one(conn: WSConnection) -> None:
            with contextlib.suppress(Exception):
                await conn.ws.send_json(shutdown_msg)
                await conn.ws.close(code=1001, reason="服务器重启")

        if snapshot:
            # asyncio.gather 并发执行，串行 N 秒 → 并发 max(单连接耗时)
            await asyncio.gather(*[_close_one(c) for c in snapshot])

        await logger.ainfo("ws.shutdown.done")

    # ── Redis Pub/Sub 多实例广播（可选） ─────────────────

    def set_redis(self, redis) -> None:
        """注入 Redis 客户端（在 lifespan 中调用，避免循环导入）"""
        self._redis = redis

    async def start_redis_listener(self) -> None:
        """
        启动 Redis 消息监听（多实例部署时调用）

        面试点：每个实例订阅 "ws:broadcast" 频道。
        send_to_user 在本机找不到用户时会 publish 求助其他实例。
        只有那个用户连接的实例才会推送。Redis 只做消息路由，不存 WebSocket 对象。

        含自动重连：Redis 断开后等待重试，不会永久丢失监听。
        """
        if self._redis is None:
            await logger.ainfo("ws.redis_listener.skipped", reason="Redis 未配置")
            return

        async def _listen_loop():
            while True:
                try:
                    pubsub = self._redis.pubsub()
                    await pubsub.subscribe("ws:broadcast", "ws:broadcast_all")
                    await logger.ainfo(
                        "ws.redis_listener.started",
                        channels=["ws:broadcast", "ws:broadcast_all"],
                    )

                    async for msg in pubsub.listen():
                        if msg["type"] != "message":
                            continue
                        try:
                            channel = msg["channel"]
                            if channel == "ws:broadcast":
                                # 定向推送：{user_id, data} → 只推指定用户
                                payload = json.loads(msg["data"])
                                uid = payload["user_id"]
                                data = payload["data"]
                                user_conns = self._connections.get(uid)
                                if user_conns:
                                    for conn in list(user_conns.values()):
                                        with contextlib.suppress(Exception):
                                            await conn.ws.send_json(data)
                            elif channel == "ws:broadcast_all":
                                # 全集群广播：遍历本机所有用户推送
                                data = json.loads(msg["data"])
                                for user_conns in self._connections.values():
                                    for conn in list(user_conns.values()):
                                        with contextlib.suppress(Exception):
                                            await conn.ws.send_json(data)
                        except Exception:
                            pass

                except asyncio.CancelledError:
                    break
                except Exception:
                    await logger.aerror("ws.redis_listener.error", exc_info=True)
                    await asyncio.sleep(1)  # 断开后等 1 秒重连

        self._redis_listener_task = asyncio.create_task(_listen_loop())

    # ── 属性 ────────────────────────────────────────────

    @property
    def online_count(self) -> int:
        """当前连接总数（非用户数——一个用户可能有多个连接）"""
        return len(self._conn_to_user)

    @property
    def online_user_count(self) -> int:
        """当前在线用户数"""
        return len(self._connections)

    def get_user_connections(self, user_id: int) -> int:
        """获取指定用户的当前连接数"""
        return len(self._connections.get(user_id, {}))


class ConnectionLimitExceeded(Exception):
    """WebSocket 连接数超限（握手已完成，服务端主动拒绝）"""


class ConnectionLimitAborted(Exception):
    """WebSocket 握手期间客户端断开（accept 失败，无需 close）"""


# 模块级单例
manager = ConnectionManager()
