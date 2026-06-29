"""
结构化日志 — structlog 配置

=== 面试重点 ===
Q: 三个易忽略的生产级问题怎么处理？
1. 异常堆栈格式化：JSONRenderer 不会自动格式化异常，需要 format_exc_info 或 ExceptionRenderer
2. 敏感字段脱敏：密码/token 不能明文打在日志里，需要自定义 processor 替换
3. 异步上下文传递：request_id 在 await 跨越不同协程时会丢失，
   需要 merge_contextvars 把上下文绑定到 contextvars 而非线程本地
"""

import re
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.config import settings

# ── 敏感字段脱敏处理器 ──────────────────────────────────
# 面试点：生产环境日志可能被 ELK/Loki 收集并展示给运维
# 日志里出现明文密码 → 安全隐患，需要自动替换
_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "phone",
    "mobile",
}
_SENSITIVE_PATTERNS = [
    # 手机号：1 开头 10 位数字 → 1**********8
    (re.compile(r"\b(1[3-9]\d)\d{4}(\d{4})\b"), r"\1****\2"),
    # 邮箱：保留首字母和域名
    (re.compile(r"\b(\w)[\w.-]*(@[\w.]+\w)\b"), r"\1***\2"),
]


def _mask_sensitive(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """
    敏感字段脱敏：遍历日志事件字典，发现敏感 key 就替换值

    面试点：purge 而不是 filter——不删字段只打码，保留日志结构
    运维看到 "password": "***" 就知道有人传了密码，只是看不到明文
    """
    for key in list(event_dict.keys()):
        key_lower = key.lower()
        # any()：迭代器中任意一个条件为 True，整体返回 True
        if any(sk in key_lower for sk in _SENSITIVE_KEYS):
            event_dict[key] = "***"
        elif isinstance(event_dict[key], str):
            val = event_dict[key]
            for pattern, repl in _SENSITIVE_PATTERNS:
                if pattern.search(val):
                    event_dict[key] = pattern.sub(repl, val)
                    break
    return event_dict


# ── structlog 配置 ────────────────────────────────────
structlog.configure(
    processors=[
        # 1. 合并 contextvars 上下文（异步协程安全）
        # 面试点：FastAPI 异步路由在 await 时可能切换协程
        # 普通线程本地存储会导致 request_id 丢失
        # merge_contextvars 用 contextvars 而非 threading.local，确保异步安全
        structlog.contextvars.merge_contextvars,
        # 2. 按日志级别过滤
        structlog.stdlib.filter_by_level,
        # 3. 自动添加 level 字段（info/warning/error）
        structlog.stdlib.add_log_level,
        # 4. 敏感字段脱敏（在渲染之前处理）
        # 面试点：必须在加时间戳和渲染之前处理
        # 否则 password 已经被 JSONRenderer 写死了
        _mask_sensitive,  # 自定义敏感字段脱敏
        # 5. ISO 8601 时间戳
        structlog.processors.TimeStamper(fmt="iso"),
        # 6. 堆栈信息格式化
        # 面试点：logger.aerror("xxx", exc_info=True) 时
        # 自动把 sys.exc_info() 转成可检索的结构化字段
        # 输出：{"exception": "ZeroDivisionError: division by zero\n  File ..."}
        # 而不是一个不可检索的纯字符串
        structlog.processors.format_exc_info,
        # 7. 渲染输出
        # 面试点：按环境选格式，而非按日志级别
        # development → ConsoleRenderer（彩色人类可读）
        # staging/production → JSONRenderer（机器可解析，对接 ELK/Loki）
        # 这样 Staging 临时开 DEBUG 排查时，输出仍是 JSON，不破坏日志平台
        # A if 条件 else B
        structlog.dev.ConsoleRenderer()
        if settings.ENVIRONMENT == "development"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# 使用示例：
# 异步安全绑定：
#   import structlog.contextvars
#   structlog.contextvars.bind_contextvars(request_id="abc123")
#   → 同一个 request 的所有协程都能自动带上 request_id
#
# 异常日志：
#   await logger.aerror("db.query_failed", exc_info=True)
#   → 自动格式化堆栈成结构化字段
#
# 敏感字段自动脱敏：
#   logger.info("user.login", password="123456")
#   → 输出 {"password": "***", ...}
