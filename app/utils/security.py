"""
安全工具 — JWT 签发/验证 + 密码 bcrypt 哈希

=== 面试重点 ===
Q: 为什么用 bcrypt 而不是 MD5/SHA256？
A: bcrypt 是专为密码设计的哈希算法，有两点 SHA256 做不到：
   1. 内置盐值（Salt）：同样的密码 "123456"，每次 hash 结果不同
      → 攻击者不能拿彩虹表对撞
   2. 计算慢（可调节 rounds）：故意的！用户登录慢 0.1 秒没感觉，
      攻击者暴力破解慢几十万倍。SHA256 太快，一秒能试几十万次

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

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# 面试点：CryptContext 封装了 bcrypt，支持后续升级算法
# schemes=["bcrypt"] 表示当前用 bcrypt，如果未来 bcrypt 被破解
# 可以用 schemes=["bcrypt", "argon2"] 逐步迁移
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """
    对密码进行 bcrypt 哈希

    面试点：为什么不在数据库里存明文？
    1. 数据库泄露 = 所有用户密码泄露（明文能看到所有密码）
    2. 哈希不可逆：拿到哈希值无法反推原始密码
    3. bcrypt 有盐值：两个用户密码相同 → 哈希也不同
    """
    return pwd_context.hash(password)  # type: ignore[no-any-return]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证密码是否匹配
    面试点：不比较 hash(pwd1) == hash(pwd2)，而是用 pwd_context.verify()
    内置恒定时间比较算法，防止时序攻击（攻击者通过测量响应时间猜密码长度）
    """
    return pwd_context.verify(plain_password, hashed_password)  # type: ignore[no-any-return]


def create_access_token(data: dict[str, Any]) -> str:
    """
    签发 access token（短时效 15 分钟）

    面试点：为什么加 "type": "access" 字段？
    防止攻击者拿 refresh token 当 access token 用
    中间件 get_current_user 会检查 payload["type"] == "access"
    两种 Token 用途不同，不能互换
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)  # type: ignore[no-any-return]


def create_refresh_token(data: dict[str, Any]) -> str:
    """
    签发 refresh token（长时效 7 天）

    面试点：refresh token 存在 Redis 而不是 JWT 里
    好处：服务端可控——改密码时删掉 Redis key，用户立刻被踢下线
    如果只靠 JWT 的 exp 字段，改密码后 Token 依然有效直到自然过期
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)  # type: ignore[no-any-return]


def decode_token(token: str) -> dict[str, Any] | None:
    """
    解码 JWT token，验证失败返回 None（不抛异常）

    面试点：为什么不直接 raise？
    调用方需要区分"没传 token"（401 请登录）和"token 过期"（401 请刷新）
    返回 None 让调用方统一处理，避免 try/except 到处飞
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        # 面试点：JWTError 是 jose 提供的基础异常，涵盖了：
        # ExpiredSignatureError（过期）、JWTClaimsError（Payload 不合法）、
        # JWTError（签名无效/篡改）等所有情况
        return None
