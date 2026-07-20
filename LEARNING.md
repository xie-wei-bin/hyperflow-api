# 博客系统 API — 7 天学习计划

> 每个文件打开先看 `=== 面试重点 ===`，再看代码。每学完一个模块必须动手实操。

---

## 第 1 天：基础层 — 理解"项目怎么启动的"

| 顺序 | 文件 | 重点 | 实操 |
|------|------|------|------|
| 1 | `pyproject.toml` | 装了哪些包、为什么选它们 | |
| 2 | `.env` → `.env.example` | 环境变量管理 | |
| 3 | `app/config.py` | Pydantic 加载 .env → 打断点看 settings.XXX | 改 LOG_LEVEL 看日志变化 |
| 4 | `app/logger.py` | structlog 配置 | 启动项目，看日志长什么样 |
| 5 | `app/database.py` | 异步引擎、连接池 | 打断点看 pool_size 怎么生效 |
| 6 | `app/redis_client.py` | MonitoredRedis 代理模式 | 打断点看 __getattr__ 劫持 |

```
实操：uvicorn app.main:app --reload → 访问 /health → 看日志输出
```

---

## 第 2 天：数据层 — 理解"数据怎么存的"

| 顺序 | 文件 | 重点 |
|------|------|------|
| 7 | `app/models/base.py` | Base 为什么要独立 |
| 8 | `app/models/user.py` | username unique=True 的真正含义 |
| 9 | `app/models/category.py` | 一对多关系 |
| 10 | `app/models/tag.py` | 多对多的"多"端 |
| 11 | `app/models/article.py` | **重点**：多对多中间表 + viewonly |
| 12 | `app/models/comment.py` | **重点**：parent_id 自关联树 |
| 13 | `app/models/like_favorite.py` | UNIQUE(article_id, user_id) 幂等底线 |

```
实操：MySQL 执行 SHOW CREATE TABLE user; 看 comment 注释是否生效
```

---

## 第 3 天：校验 + 工具层

| 顺序 | 文件 | 重点 |
|------|------|------|
| 14 | `app/schemas/common.py` | Generic[T] 泛型统一响应 |
| 15 | `app/schemas/auth.py` | Pydantic Field 自动校验 |
| 16 | `app/schemas/user.py` | 公开 vs 私有信息分离 |
| 17 | `app/schemas/article.py` | 列表项 vs 详情不同结构 |
| 18 | `app/schemas/comment.py` | 树形 Schema |
| 19 | `app/utils/security.py` | **重点**：JWT 三部分 + bcrypt 盐值 |
| 20 | `app/utils/pagination.py` | OFFSET vs 游标分页 |

```
实操：Swagger 发注册请求，故意填错密码格式 → 看 422 校验错误结构
```

---

## 第 4 天：中间件 + 业务层

| 顺序 | 文件 | 重点 |
|------|------|------|
| 21 | `app/middleware/request_id.py` | contextvars 异步安全 |
| 22 | `app/middleware/timing.py` | 洋葱模型 |
| 23 | `app/middleware/auth.py` | **重点**：Depends 链式调用原理 |
| 24 | `app/services/auth.py` | flush() vs commit() |
| 25 | `app/services/article.py` | **重点**：Cache-Aside + selectinload 防 N+1 |
| 26 | `app/services/comment.py` | **重点**：递归构建树 |
| 27 | `app/services/search.py` | FULLTEXT vs LIKE |

```
实操：注册 → 登录 → 拿到 token → 贴到 Swagger Authorize 按钮
```

---

## 第 5 天：接口层 — 理解"对外的门面"

| 顺序 | 文件 | 重点 |
|------|------|------|
| 28 | `app/routers/health.py` | K8s liveness probe |
| 29 | `app/routers/auth.py` | **重点**：限流 + Token Rotation |
| 30 | `app/routers/user.py` | 收藏列表 N+1 优化 |
| 31 | `app/routers/article.py` | **重点**：Cache-Aside 完整流程 + 幂等去重 |
| 32 | `app/routers/category.py` | Pydantic Schema 替代 dict |
| 33 | `app/routers/tag.py` | |
| 34 | `app/routers/comment.py` | 嵌套路由设计 |
| 35 | `app/routers/search.py` | 输入清洗防注入 |

```
实操：完整链路：注册 → 登录 → 创建文章 → 评论 → 点赞 → 收藏 → 搜索
```

---

## 第 6 天：组装 + 测试

| 顺序 | 文件 | 重点 |
|------|------|------|
| 36 | `app/exceptions.py` | 异常分层设计 |
| 37 | `app/exception_handlers.py` | 统一 JSONResponse |
| 38 | `app/limiter.py` | 令牌桶算法 |
| 39 | `app/main.py` | **重点**：中间件顺序 + 异常处理顺序 |
| 40 | `tests/conftest.py` | **重点**：Mock + dependency_overrides |
| 41 | `tests/test_*.py` | 12 个测试逐个看 |

```
实操：pytest -v，打断点看 MockRedis 怎么替代真 Redis
```

---

## 第 7 天：工程化

| 顺序 | 文件 | 重点 |
|------|------|------|
| 42 | `docker-compose.yml` | 密码保护 |
| 43 | `Dockerfile` | 多阶段构建 |
| 44 | `alembic.ini` + `migrations/` | 动态注入 URL 防密码泄露 |
| 45 | `.github/workflows/ci.yml` | GitHub Actions 自动化 |
| 46 | `.pre-commit-config.yaml` | Git hooks |

```
实操：docker compose down && docker compose up -d → alembic upgrade head → 推 GitHub 看 CI 绿勾
```
