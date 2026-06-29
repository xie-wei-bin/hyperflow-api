"""
应用配置 — Pydantic Settings v2 类型安全加载

=== 面试重点 ===
Q: 为什么用 Pydantic Settings 而不是 os.getenv()？
A: 三个原因：
   1. 类型安全：settings.DB_PORT 是 int，os.getenv("DB_PORT") 返回 str，运行时才发现类型错误
   2. 启动校验：extra="forbid" 会在启动时就报错（拼写 DB_HOS 而不是 DB_HOST），
      os.getenv() 静默返回 None，bug 藏到生产环境才爆发
   3. IDE 支持：settings. 后会有自动补全，os.getenv("") 只能靠记忆

Q: extra="forbid" 有什么用？
A: 比如你 .env 里写了 DB_HOS=xxx（拼错了 host），启动直接报：
   "extra fields not permitted: DB_HOS"
   而不会静默忽略，导致数据库连接使用默认值，线上炸了都不知道

Q: 为什么要单例 settings = Settings()？
A: Settings 只读，全局共享一份实例即可，避免反复读取 .env 文件。
   线程安全：Pydantic Settings 实例化后字段不可变
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，禁止使用 os.getenv()，一切从这里取"""

    # ── 数据库 ────────────────────────────
    # 面试点：端口默认 3306 但允许覆盖，这样 CI 环境和开发环境可以不同
    DB_HOST: str
    DB_PORT: int = 3306
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    # ── Redis ─────────────────────────────
    # 面试点：Redis URL 格式 redis://host:port/db，/0 表示第 0 号数据库（共 16 个）
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── JWT ───────────────────────────────
    # 面试点：为什么双 Token？
    # access(15min) 高频使用，短时效减少泄露风险
    # refresh(7天) 低频使用，避免用户频繁登录
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── CORS ──────────────────────────────
    # 面试点：list[str] 类型，Pydantic 会自动解析 JSON 数组格式的环境变量
    # .env 里写 ALLOWED_ORIGINS=["http://localhost:3000"]
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    # ── 日志 ──────────────────────────────
    # 面试点：LEVEL 控制输出多少，ENVIRONMENT 控制怎么输出
    # 两者正交：Staging 可以 DEBUG + JSON（排查不破坏日志平台）
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"  # development / staging / production

    # Docker 用（docker-compose 读 .env，Pydantic 需允许此字段出现）
    REDIS_PASSWORD: str = ""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),  # 绝对路径，去哪执行都行
        env_file_encoding="utf-8",
        extra="forbid",  # 保留，防拼写错误
        # case_sensitive 不加，区分大小写更安全（DB_HOST ≠ db_host 能发现拼错）
    )


# 面试点：模块级单例，Python 的 import 机制天然保证只执行一次
# 即使被多个模块 import，也只会实例化这一个 Settings 对象
settings = Settings()  # type: ignore[call-arg]  # 从 .env 读取，mypy 不感知 pydantic-settings
