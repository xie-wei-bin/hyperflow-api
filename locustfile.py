"""
Locust 压测脚本 — 博客系统 API

=== 面试重点 ===
Q: 你怎么评估系统容量？
A: 用 Locust 模拟真实用户行为——浏览列表(权重3) + 看详情(权重1) + 注册登录(权重1)。
   100 并发 → 200 并发 → 500 并发，观察 QPS/P99/错误率拐点。
   拐点出现的位置就是系统容量上限，拐点之前的 QPS 就是基准容量。

启动: locust -f locustfile.py --host http://localhost:8000
Web UI: http://localhost:8089
"""

from locust import HttpUser, between, task


class BlogUser(HttpUser):
    """模拟真实博客用户行为"""

    wait_time = between(1, 3)  # 用户操作间隔 1-3 秒

    # ── 注册 + 登录（只执行一次） ──
    def on_start(self):
        """每个虚拟用户启动时：注册 + 登录"""
        import random

        suffix = random.randint(10000, 99999)
        self.username = f"loadtest_{suffix}"
        self.email = f"loadtest_{suffix}@test.com"
        self.password = "test123456"

        # 注册
        resp = self.client.post(
            "/api/auth/register",
            json={
                "username": self.username,
                "email": self.email,
                "password": self.password,
            },
        )
        if resp.status_code in (201, 409):  # 409 = 已注册，不报错
            # 登录
            login_resp = self.client.post(
                "/api/auth/login",
                json={"username": self.username, "password": self.password},
            )
            if login_resp.status_code == 200:
                self.token = login_resp.json()["data"]["access_token"]
                self.headers = {"Authorization": f"Bearer {self.token}"}

    # ── 读操作（权重高，模拟真实用户行为） ──

    @task(5)  # 权重 5：50% 流量 → 文章列表
    def list_articles(self):
        self.client.get("/api/articles?page=1&page_size=20", name="/api/articles[list]")

    @task(3)  # 权重 3：30% 流量 → 文章详情
    def get_article_detail(self):
        self.client.get("/api/articles/my-first-post", name="/api/articles[detail]")

    @task(2)  # 权重 2：20% 流量 → 热门排行
    def get_hot_articles(self):
        self.client.get("/api/articles/hot", name="/api/articles[hot]")

    # ── 写操作（权重低） ──

    @task(1)  # 权重 1：10% 流量 → 创建文章
    def create_article(self):
        if hasattr(self, "token"):
            self.client.post(
                "/api/articles",
                json={
                    "title": f"压测文章 {self.username}",
                    "slug": f"loadtest-{self.username}-article",
                    "content": "这是一篇压测生成的文章内容" * 10,
                    "status": "published",
                },
                headers=self.headers,
                name="/api/articles[create]",
            )

    @task(1)
    def search_articles(self):
        self.client.get("/api/search?q=Python", name="/api/search")


# ── 压测报告解读 ─────────────────────────────────────
# 跑完之后打开 Web UI，看 4 个关键指标：
#
# 1. Total Requests per Second (QPS)
#    → 系统吞吐量的天花板
#
# 2. Response Times (P50 / P90 / P99)
#    → P99 是 99% 请求的响应时间，SLA 通常定 P99 < 500ms
#    → 如果 P99 突然跳升到 2s+，说明系统到了瓶颈
#
# 3. Failure Rate
#    → 429（限流触发）→ 限流器正常工作，不是 bug
#    → 500（服务器错误）→ 排查日志
#    → 502/503（连接拒绝）→ 连接池耗尽 / 服务过载
#
# 4. 拐点分析
#    并发 100 → QPS 500, P99 200ms  ✅
#    并发 200 → QPS 800, P99 350ms  ✅
#    并发 500 → QPS 850, P99 2s      ← 拐点！吞吐不再增长，延迟爆炸
#    → 容量上限 ≈ 400 并发
