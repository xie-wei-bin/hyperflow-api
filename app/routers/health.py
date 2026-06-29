"""
健康检查路由

=== 面试重点 ===
Q: 为什么需要 /health 接口？
A: 1. K8s/Docker 的 liveness probe：定期 GET /health → 200 OK 表示容器存活
   2. 负载均衡器的健康检查：如果连续失败自动踢掉这个节点
   3. 运维监控告警：监控系统每分钟调一次 → DB 或 Redis 挂了立即告警

Q: 为什么单独检查 DB 和 Redis？
A: 容器可能存活（FastAPI 进程没挂），但依赖服务挂了（MySQL 被 OOM、Redis 网络不通）。
   /health 返回 {"database": "unavailable"} 比 500 更精准。
   监控系统可以根据具体字段决定告警级别。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.schemas.common import APIResponse

router = APIRouter(tags=["系统"])


@router.get("/health", response_model=APIResponse[dict])
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """健康检查：分别验证数据库和 Redis 连通性"""
    health_data = {"database": "ok", "redis": "ok"}

    # 数据库连通性：发一个轻量查询
    try:
        await db.execute(text("SELECT 1"))  # 不用查具体表，SELECT 1 是最轻的
    except Exception:
        health_data["database"] = "unavailable"

    # Redis 连通性：PING → PONG
    try:
        await redis.ping()
    except Exception:
        health_data["redis"] = "unavailable"

    return APIResponse(data=health_data)
