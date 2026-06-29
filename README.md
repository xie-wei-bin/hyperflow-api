# 博客系统 API

[![CI](https://github.com/你的用户名/blog-system/actions/workflows/ci.yml/badge.svg)](https://github.com/你的用户名/blog-system/actions)

企业级博客后端系统，支持用户认证、文章管理、评论互动、收藏点赞、全文搜索、热门排行。

## 技术栈

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 运行环境 |
| FastAPI | 0.138 | 异步 Web 框架 |
| SQLAlchemy | 2.0 | 异步 ORM（async session） |
| Pydantic | v2 | 数据校验 + Settings 管理 |
| MySQL | 8.0+ | 主数据库（aiomysql 驱动） |
| Redis | 7.4 | 缓存 + 热门排行 |
| Alembic | 1.18 | 数据库迁移管理 |
| structlog | 26.x | 结构化日志 |
| slowapi | 0.1 | 接口限流 |
| ruff | 0.15 | 代码检查 + 格式化 |
| mypy | 2.1 | 类型检查 |
| Docker Compose | v2 | 本地开发环境 |

## 快速开始

### 前置条件

- Python 3.11+
- MySQL 8.0+
- Docker Desktop（用于运行 Redis）

### 1. 创建数据库

```sql
CREATE DATABASE IF NOT EXISTS blog_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 MySQL 密码和密钥
```

### 3. 启动 Redis

```bash
docker compose up -d
```

### 4. 安装依赖

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows
pip install -e ".[dev]"
```

### 5. 数据库迁移

```bash
alembic upgrade head
```

### 6. 启动服务

```bash
uvicorn app.main:app --reload --port 8000
```

访问 http://localhost:8000/docs 查看 API 文档。

## API 接口

### 认证模块 `/api/auth`
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/auth/register | 用户注册（限流 5次/分钟） |
| POST | /api/auth/login | 登录（限流 10次/分钟） |
| POST | /api/auth/refresh | 刷新 access token |
| GET | /api/auth/me | 获取当前用户信息 |

### 文章模块 `/api/articles`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/articles | 文章列表（分页/筛选/搜索/排序） |
| GET | /api/articles/{slug} | 文章详情（Cache-Aside 缓存） |
| POST | /api/articles | 创建文章 |
| PUT | /api/articles/{id} | 编辑文章 |
| DELETE | /api/articles/{id} | 删除文章（软删除） |
| GET | /api/articles/hot | 热门排行（Redis ZSet） |

### 互动模块
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/articles/{id}/like | 点赞（幂等） |
| DELETE | /api/articles/{id}/like | 取消点赞 |
| POST | /api/articles/{id}/favorite | 收藏（幂等） |
| DELETE | /api/articles/{id}/favorite | 取消收藏 |

### 其他
- **系统**: GET `/health` — 健康检查
- **用户**: GET/PUT `/api/users/{id}` — 用户信息
- **分类**: `/api/categories` — CRUD（管理员）
- **标签**: `/api/tags` — 列表 + 文章筛选
- **评论**: `/api/articles/{id}/comments` — 树形结构
- **搜索**: GET `/api/search?q=关键词`

## 工程化实践

- **配置管理**: Pydantic Settings v2 类型安全，`extra="forbid"`
- **JWT 双 Token**: access 15min + refresh 7天 + 改密失效
- **限流**: slowapi — 注册 5/min、登录 10/min、全局 200/min
- **缓存**: Cache-Aside — 查询查 Redis，更新主动失效
- **阅读量**: Redis INCR + 5 分钟批量回写 MySQL
- **热门排行**: Redis ZSet（阅读×1 + 点赞×3 + 评论×5）
- **结构化日志**: structlog — 开发 ConsoleRenderer / 生产 JSONRenderer
- **全文搜索**: MySQL FULLTEXT INDEX (title, content)
- **代码质量**: ruff ✅  mypy ✅  pytest 12/12 ✅
- **数据库迁移**: Alembic 异步模板管理

## 项目结构

```
blog_system/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # Pydantic Settings
│   ├── database.py          # SQLAlchemy 异步引擎
│   ├── redis_client.py      # Redis 连接池
│   ├── limiter.py           # 接口限流器
│   ├── logger.py            # structlog 配置
│   ├── exceptions.py        # 自定义异常
│   ├── exception_handlers.py
│   ├── models/              # 8 张数据表
│   ├── schemas/             # Pydantic 校验
│   ├── routers/             # API 路由
│   ├── services/            # 业务逻辑
│   ├── middleware/          # 中间件
│   └── utils/               # 工具类
├── migrations/              # Alembic 迁移
├── tests/                   # pytest 测试
├── pyproject.toml           # 项目配置
├── docker-compose.yml       # Redis 服务
└── Dockerfile               # 多阶段构建
```

## 运行测试

```bash
pytest tests/ -v
```

## License

MIT
