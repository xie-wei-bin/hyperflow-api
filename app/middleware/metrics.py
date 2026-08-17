"""
Prometheus 监控指标 — prometheus-fastapi-instrumentator 接入

=== 面试重点 ===
Q: 为什么接入 Prometheus？
A: 生产环境三大可观测性支柱：指标（Prometheus）+ 日志（structlog）+ 链路追踪（待接入）。
   没有指标 → 不知道 QPS 多少、P99 延迟多高、错误率多大。
   出了问题只能靠日志排查，但日志量大了根本翻不完。

Q: prometheus-fastapi-instrumentator 跟踪了哪些指标？
A: 开箱即用：
   - http_requests_total（按 method/path/status 分组） → QPS、错误率
   - http_request_duration_seconds（Histogram） → P50/P90/P99 延迟
   - http_request_size_bytes / http_response_size_bytes → 流量大小
   这些都是标准 RED 指标（Rate/Errors/Duration）。

Q: 为什么 /metrics 不需要鉴权？
A: Prometheus 是内部基础设施，不暴露在公网。生产环境通过 k8s Service
   annotations 让 Prometheus 自动发现，或 Nginx 限制 IP 段访问。

Q: Instrumentator 和手写 prometheus_client 的区别？
A: Instrumentator 自动拦截所有 HTTP 请求，一行代码搞定。
   手写需要每个路由函数里手动 inc/count，100 个接口改 100 处。
   自定义业务指标（如 article_views_total）才需要手写 prometheus_client。
"""

from prometheus_client import Gauge
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from prometheus_fastapi_instrumentator.metrics import Info


# ── 业务自定义指标 ─────────────────────────
# 面试点：Gauge 是可增可减的瞬时值（文章数/用户数），Counter 是只增不减的累计值（请求数）
# 这些指标不通过 HTTP 请求自动采集，而是通过 /metrics 端点暴露当前值
# 真正的采集由外部定时任务或 startup 事件更新

article_total_gauge = Gauge(
    "blog_articles_total",
    "已发布文章总数",
    ["status"],  # 按 draft/published 分组
)

user_total_gauge = Gauge(
    "blog_users_total",
    "注册用户总数",
)

ws_connections_gauge = Gauge(
    "blog_ws_connections",
    "活跃 WebSocket 连接数",
)


def setup_metrics(app) -> Instrumentator:
    """
    初始化 Prometheus 指标采集

    面试点：为什么在 lifespan 之前调用？
    Instrumentator 是纯 ASGI 中间件 + /metrics 路由，
    独立于 lifespan 生命周期，互不影响。
    """
    instrumentator = Instrumentator(
        # 面试点：should_group_status_codes=False 保留每个状态码的独立统计
        # True 会把 2xx 合并为 200，丢失"201 创建 vs 200 成功"的区分
        should_group_status_codes=False,
        # should_ignore_untemplated=True 忽略未注册的动态路由（如 /admin/xxx）
        # 防止爬虫请求污染指标
        should_ignore_untemplated=True,
        # 排除 /metrics 和 /health 端点，避免监控自身被监控（噪声）
        excluded_handlers=[".*metrics.*", ".*health.*"],
    )

    # 面试点：Instrumentator 的 metrics() 函数式组合，按需添加指标维度
    instrumentator.add(
        metrics.request_size(
            should_include_handler=True,
            should_include_method=True,
        )
    ).add(
        metrics.response_size(
            should_include_handler=True,
            should_include_method=True,
        )
    ).add(
        metrics.latency(
            metric_name="http_request_duration_seconds",
            should_include_handler=True,
            should_include_method=True,
            should_include_status=True,
        )
    ).add(
        metrics.requests(
            metric_name="http_requests_total",
            should_include_handler=True,
            should_include_method=True,
            should_include_status=True,
        )
    )

    # 注册到 FastAPI app：添加 /metrics 端点 + 请求拦截中间件
    instrumentator.instrument(app).expose(app, include_in_schema=True)

    return instrumentator
