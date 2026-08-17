"""
熔断器 — 防止级联故障

=== 面试重点 ===
Q: 熔断器干什么的？
A: 当外部服务（AI API/第三方接口）频繁超时或报错时，短时间内直接拒绝请求
   （快速失败），不让故障扩散到整个系统。等一段时间后半开尝试，恢复则关闭。

Q: 三种状态？
A: CLOSED（正常）→ 错误数超阈值 → OPEN（拒绝请求）→ 超时后 → HALF_OPEN（放一个探测请求）
   → 成功 → CLOSED / 失败 → OPEN

Q: 项目里什么场景用？
A: ① AI 审核接口（LLM API 超时/限流时熔断，降级到敏感词过滤）
   ② Redis 分布式锁（Redis 不可用时直接跳过，fail open）
   ③ 邮件发送（SMTP 超时熔断，不阻塞通知主流程）
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable

from app.logger import logger


class CircuitState(Enum):
    CLOSED = "closed"          # 正常通行
    OPEN = "open"              # 熔断中，拒绝请求
    HALF_OPEN = "half_open"    # 半开，放一个探测请求


class CircuitBreaker:
    """
    熔断器

    使用：
        cb = CircuitBreaker("ai_moderation", threshold=5, recovery=30)
        result = await cb.call(ai_moderate, comment_content)
        # AI 连续失败 5 次 → 熔断 30 秒 → 半开探测 → 恢复或继续熔断
    """

    def __init__(
        self,
        name: str,
        threshold: int = 5,       # 连续失败 N 次熔断
        recovery: float = 30.0,   # 熔断后多少秒尝试恢复
        half_open_limit: int = 1, # 半开状态最多放行几个请求
    ):
        self.name = name
        self._threshold = threshold
        self._recovery = recovery
        self._half_open_limit = half_open_limit

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_count = 0
        self._lock = asyncio.Lock()

    async def call(
        self,
        func: Callable[..., Any],
        *args,
        fallback: Callable[..., Any] | None = None,
        **kwargs,
    ) -> Any:
        """
        通过熔断器调用函数

        Args:
            func: 要调用的函数
            fallback: 熔断时的降级函数（如 AI 审核熔断 → 纯敏感词过滤）
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                # 检查是否可以进入半开
                if time.monotonic() - self._last_failure_time >= self._recovery:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_count = 0
                    await logger.ainfo(
                        "circuit_breaker.half_open",
                        name=self.name,
                    )
                else:
                    # 熔断中 → 降级或直接抛异常
                    await logger.awarning(
                        "circuit_breaker.open_rejected",
                        name=self.name,
                    )
                    if fallback:
                        return await fallback(*args, **kwargs)
                    raise CircuitBreakerOpenError(
                        f"熔断器 [{self.name}] 已打开，"
                        f"请在 {self._recovery}s 后重试"
                    )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_count >= self._half_open_limit:
                    if fallback:
                        return await fallback(*args, **kwargs)
                    raise CircuitBreakerOpenError(
                        f"熔断器 [{self.name}] 半开探测请求已达上限"
                    )
                self._half_open_count += 1

        # 执行实际调用（锁外，避免阻塞）
        try:
            result = await func(*args, **kwargs)
        except Exception as e:
            await self._on_failure()
            if fallback:
                return await fallback(*args, **kwargs)
            raise e

        await self._on_success()
        return result

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                await logger.ainfo(
                    "circuit_breaker.closed",
                    name=self.name,
                    message="半开探测成功，熔断器恢复",
                )
            # CLOSED 状态成功 → 重置失败计数
            self._failure_count = 0

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # 半开探测失败 → 重新打开
                self._state = CircuitState.OPEN
                await logger.aerror(
                    "circuit_breaker.reopened",
                    name=self.name,
                    message="半开探测失败，重新熔断",
                )
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self._threshold
            ):
                self._state = CircuitState.OPEN
                await logger.aerror(
                    "circuit_breaker.opened",
                    name=self.name,
                    failure_count=self._failure_count,
                    recovery_seconds=self._recovery,
                )

    @property
    def state(self) -> CircuitState:
        return self._state


class CircuitBreakerOpenError(Exception):
    """熔断器已打开"""


# ── 装饰器版本 ────────────────────────────────────────


def with_circuit_breaker(
    name: str,
    threshold: int = 5,
    recovery: float = 30.0,
):
    """
    熔断器装饰器

    使用：
        cb = CircuitBreaker("ai_call", threshold=5, recovery=30)

        @with_circuit_breaker("ai_call")
        async def call_ai(prompt):
            ...
    """
    cb = CircuitBreaker(name, threshold=threshold, recovery=recovery)

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await cb.call(func, *args, **kwargs)
        wrapper._circuit_breaker = cb  # type: ignore[attr-defined]
        return wrapper
    return decorator


# ── 预置熔断器实例（博客场景） ──────────────────────────

# AI 审核熔断器：LLM API 连续失败 3 次 → 熔断 60 秒
ai_moderation_cb = CircuitBreaker("ai_moderation", threshold=3, recovery=60)

# Redis 操作熔断器：连续失败 5 次 → 熔断 30 秒（fail open）
redis_cb = CircuitBreaker("redis", threshold=5, recovery=30)
