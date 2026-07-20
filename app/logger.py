"""
结构化日志 — structlog 配置（终端 + 文件双输出）

面试点：
1. 终端始终输出（开发=彩色，生产=JSON）
2. 配置 LOG_FILE 后额外写一份 JSON 日志文件（对接 ELK/Loki）
3. 敏感字段自动脱敏
"""

import logging
import re
from collections.abc import MutableMapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from app.config import settings

# ── 敏感字段脱敏 ──────────────────────────────────
_SENSITIVE_KEYS = {
    "password", "passwd", "secret", "token", "access_token",
    "refresh_token", "authorization", "cookie", "phone", "mobile",
}
# 手机号：lookaround 边界替代 \b，兼容 138-1234-5678 等带分隔符写法
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)[\s-]?(\d{4})[\s-]?(\d{4})(?!\d)")
# 邮箱：域名部分支持横线 user@my-mail.com
_EMAIL_RE = re.compile(r"(?<!\w)(\w)[\w.-]*(@[\w.-]+\w)(?!\w)")
# 缺陷①：LLM API Key 脱敏，匹配 sk- 前缀 + 至少 8 位（含横线下划线）
_SK_RE = re.compile(r"sk-[a-zA-Z0-9\-_]{8,}")


def _mask_value(val: Any) -> Any:
    """递归脱敏，逐层检查 key 是否为敏感字段"""
    if isinstance(val, dict):
        result: dict[str, Any] = {}
        for k, v in val.items():
            if any(sk in k.lower() for sk in _SENSITIVE_KEYS):
                result[k] = "***"
            else:
                result[k] = _mask_value(v)
        return result

    if isinstance(val, list):
        return [_mask_value(item) for item in val]

    # 缺陷②：float/int 统一转字符串走正则，不做长度判断
    if isinstance(val, (int, float)):#括号代表多类型匹配
        return _mask_text(str(val))#正则只能处理字符串

    if isinstance(val, str):
        return _mask_text(val)

    return val


def _mask_text(val: str) -> str:
    """对纯文本值应用全部敏感正则
    \1：引用第 1 个捕获分组的内容，手机号前三位原样保留
    ****：固定 4 个星号，直接替换掉第 2 组中间 4 位数字（隐藏隐私）
    \3：引用第 3 个捕获分组的内容，手机号最后四位原样保留"""
    #
    val = _PHONE_RE.sub(r"\1****\3", val)#.sub(替换模板, 原字符串)
    val = _EMAIL_RE.sub(r"\1***\2", val)
    val = _SK_RE.sub("sk-***", val)    # 缺陷①
    return val


def _mask_sensitive(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):#只存 key，丢掉所有 value
        #只要循环内存在任何修改字典的逻辑（赋值、删除、新增），工程规范必须转 list 拷贝键
        key_lower = key.lower()
        #any只要命中一次，立马结束，到外循环for，没有命中久内循环结束再到外循环
        if any(sk in key_lower for sk in _SENSITIVE_KEYS):
            event_dict[key] = "***"
        else:
            event_dict[key] = _mask_value(event_dict[key])
    return event_dict


# ── 公共处理器链 ──────────────────────────────────
_shared_processors = [
    structlog.contextvars.merge_contextvars,#上下文绑定，注入 request_id、以及 contextvars 绑定的所有字段
    structlog.stdlib.filter_by_level,#级别不够就丢弃（省了后面脱敏的开销）
    structlog.stdlib.add_log_level,#加个 level 字段
    _mask_sensitive,
    structlog.processors.TimeStamper(fmt="iso"),#加时间戳
    structlog.processors.format_exc_info,#格式化异常堆栈
]

# ── 预配置标准库 logging ──────────────────────────
# structlog 底层用标准库 logging 输出，所以给 root logger 加 handler
#getattr(对象, 属性名, 兜底默认值) 作用：取出对象的指定属性
_log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

# 1. 终端 handler（始终输出）
_console = logging.StreamHandler()#输出流处理器
_console.setLevel(_log_level)
_console.setFormatter(logging.Formatter("%(message)s"))

_root_logger = logging.getLogger()
_root_logger.setLevel(_log_level)
_root_logger.handlers.clear()
_root_logger.addHandler(_console)

# 2. 文件 handler — LOG_FILE_ENABLED + LOG_FILE 双重校验
if settings.LOG_FILE_ENABLED and settings.LOG_FILE:
    _log_path = Path(settings.LOG_FILE)
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    #创建滚动文件处理器
    _file = RotatingFileHandler(
        str(_log_path),#日志主文件完整路径，正常日志都输出到这个文件
        encoding="utf-8",
        maxBytes=settings.LOG_FILE_MAX_BYTES,#单个日志文件最大字节阈值
        backupCount=settings.LOG_FILE_BACKUP_COUNT,#最多保留多少个历史备份日志文件
    )
    _file.setLevel(_log_level)
    _file.setFormatter(logging.Formatter("%(message)s"))
    _root_logger.addHandler(_file)

# ── structlog 配置 ─────────────────────────────────
# 始终用 JSONRenderer：ConsoleRenderer 的 ANSI 颜色码写进文件后
# ELK/Loki 无法解析，且线上排查时纯 JSON 比带颜色终端输出更易 grep
structlog.configure(
    processors=_shared_processors + [
        structlog.processors.JSONRenderer(ensure_ascii=False, default=str)
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# 抑制第三方库裸日志：各自打自己的 stderr，不混入 structlog JSON 文件
logging.getLogger("aiomysql").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("uvicorn").propagate = False
#阻止日志向上传递到 root logger
logging.getLogger("asyncio").propagate = False
