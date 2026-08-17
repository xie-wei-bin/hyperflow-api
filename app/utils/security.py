"""
安全工具 — JWT 签发/验证 + 密码 bcrypt 哈希

=== 面试重点 ===
Q: 为什么用 bcrypt 而不是 MD5/SHA256？
A: bcrypt 是专为密码设计的哈希算法，有两点 SHA256 做不到：
   1. 内置盐值（Salt）：同样的密码 "123456"，每次 hash 结果不同
      → 攻击者不能拿彩虹表对撞
   2. 计算慢（可调节 rounds）：故意的！用户登录慢 0.1 秒没感觉，
      攻击者暴力破解慢几十万倍。SHA256 太快，一秒能试几十万次

Q: 为什么用 PyJWT 而不是 python-jose？
A: python-jose 已停止维护（2022 年最后更新），PyJWT 是活跃维护的替代品。
   两者 API 几乎一致，迁移成本极低，但 PyJWT 的异常层级更清晰：
   PyJWTError → InvalidTokenError / ExpiredSignatureError / ...

Q: JWT 的构成？三部分有什么作用？
A: Header.Payload.Signature
   Header:   {"alg": "HS256", "typ": "JWT"}  — 声明用什么算法
   Payload:  {"user_id": 1, "exp": ...}      — 存数据，不加密只签名
   Signature: HMAC-SHA256(Header.Payload, secret) — 防篡改
   关键理解：Payload 是 base64 编码不是加密，任何人都能解码看到内容
   所以不要把密码/敏感信息放 Payload！

Q: access token 和 refresh token 为什么分两个?
A: access(15min) → 每次请求都带，放在 Authorization header
   refresh(7天)  → 只存在 Redis + 客户端，很少传输
   即使 access token 被截获，15 分钟后就失效
   refresh token 通过 Redis 可控：改密码 → delete refresh → 强制重新登录
   这是 OAuth 2.0 的最佳实践

Q: 为什么不直接把 user 对象序列化到 JWT?
A: JWT Payload 每多一个字段，每次请求的 Header 就大一点。
   只存 user_id，需要用户信息时查 Redis 缓存或 DB 比扩大 JWT 更优
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from jwt import PyJWTError

from app.config import settings


def hash_password(password: str) -> str:
    """
    对密码进行 bcrypt 哈希

    面试点：为什么不在数据库里存明文？
    1. 数据库泄露 = 所有用户密码泄露（明文能看到所有密码）
    2. 哈希不可逆：拿到哈希值无法反推原始密码
    3. bcrypt 有盐值：两个用户密码相同 → 哈希也不同
    4. bcrypt.hashpw(明文字节，盐) 自动生成随机盐值，不必手动管理
    用 .encode() 转 UTF-8 字节
    .decode() 将字节转回普通字符串，方便入库存储
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证密码是否匹配

    面试点：bcrypt.checkpw 内置恒定时间比较，可防止时序攻击
    （攻击者通过测量服务器响应时间差异来猜测密码长度/内容）
    plain_password 是用户登录输入的原始明文
    hashed_password 是数据库存好的加密字符串
    checkpw 读取哈希里自带的盐，重新加密用户输入密码并比对，专门用来登录校验密码
    """
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def create_access_token(data: dict[str, Any]) -> str:
    """
    JWT 由三部分组成：Header（头部）、Payload（载荷）和 Signature（签名）。
    其中 Payload 必须是一个 JSON 对象（包含一组键值对）。
    在 Python 中，dict 是 JSON 的“原生镜像”，将 dict 传入 create_access_token，
    函数内部才能直接将其加密签名，生成最终的 Token 字符串。
    签发 access token（短时效 15 分钟）
    加密库强制要求传入 dict
    面试点：为什么加 "type": "access" 字段？
    防止攻击者拿 refresh token 当 access token 用
    中间件 get_current_user 会检查 payload["type"] == "access"
    两种 Token 用途不同，不能互换
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    """
    签发 refresh token（长时效 7 天）

    面试点：refresh token 存在 Redis 而不是 JWT 里
    好处：服务端可控——改密码时删掉 Redis key，用户立刻被踢下线
    如果只靠 JWT 的 exp 字段，改密码后 Token 依然有效直到自然过期
    """
    to_encode = data.copy()#复制字典，不修改原传入字典，避免污染外部变量
    #必须用 UTC 标准时间
    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    #追加两个关键字段
    #"exp"：JWT 标准过期声明，解码时库自动判断令牌是否超时，超时直接抛异常
    to_encode.update({"exp": expire, "type": "refresh"})
    #用项目统一密钥、加密算法，把完整载荷加密为一段字符串，返回给前端存本地
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


# ── JWT 解码异常层级 ──────────────────────────────────
# 面试点：为什么自定义三种异常而不是返回 None？
# 旧版 decode_token 返回 None，调用方无法区分：
#   "Token 过期了，请刷新" vs "Token 被篡改，可能是攻击"
# 新版通过异常类型区分，调用方可以：
#   - TokenExpiredError → 提示用户刷新
#   - TokenInvalidError → 记录安全日志 + 拒绝
#   - TokenTypeMismatchError → 记录安全日志（有人拿 refresh 当 access 用）


class TokenExpiredError(ValueError):
    """Token 已过期 — 客户端应刷新"""


class TokenInvalidError(ValueError):
    """Token 无效 — 签名错误/格式错误/缺少必要字段/被篡改"""


class TokenTypeMismatchError(ValueError):
    """Token type 字段不匹配 — 如拿 refresh token 当 access token 用"""


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    """
    解码并校验 JWT token

    Args:
        token: JWT token 字符串
        expected_type: 期望的 token 类型（"access" 或 "refresh"）。
                       为 None 则不校验 type 值（仅校验存在性）。

    Returns:
        解析后的 payload 字典

    Raises:
        TokenExpiredError:      token 已过期
        TokenInvalidError:      token 签名无效/格式错误/缺少必要字段
        TokenTypeMismatchError: type 字段与期望值不匹配

    === 面试重点 ===
    Q: 为什么用异常而不是返回 None？
    A: 三种失败原因需要不同处理：
       过期 → 客户端刷新 token（正常流程）
       篡改 → 记录安全告警，拒绝请求（可能是攻击）
       类型不匹配 → 记录安全告警（有人拿 refresh 当 access 用）
       返回 None 把这三种全混在一起，调用方无法区分。

    Q: leeway 是干什么的？
    A: 服务器时钟偏差容错。如果签发 Token 的服务器比验证服务器快 10 秒，
       没有 leeway 会导致刚刚签发的 Token 被误判过期。
       30 秒是业界常用值，覆盖 NTP 同步延迟和跨机房时钟偏差。

    Q: 为什么 type 校验放在 decode_token 而不是交给调用方？
    A: 防御内聚——之前的代码 type 校验散落在 4 个调用点，auth.py:79 甚至漏了。
       校验逻辑和校验对象应该放在一起，不依赖调用方的自觉。
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "type"]},
            leeway=settings.JWT_LEEWAY,  # 30 秒时钟容错
        )
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token 已过期，请刷新")
    except jwt.MissingRequiredClaimError as e:
        raise TokenInvalidError(f"Token 缺少必要字段: {e}")
    except jwt.InvalidTokenError:
        raise TokenInvalidError("Token 无效或已被篡改")

    # 面试点：不仅校验 type 存在（options 已做），还校验 type 值
    if expected_type is not None and payload.get("type") != expected_type:
        raise TokenTypeMismatchError(
            f"Token 类型不匹配，期望 {expected_type}，"
            f"实际 {payload.get('type', '缺失')}"
        )

    return payload
