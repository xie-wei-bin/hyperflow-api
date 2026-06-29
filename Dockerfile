# 博客系统 API — 多阶段构建 Dockerfile
#
# 面试点：多阶段构建的好处
# 1. 构建阶段装 gcc/编译依赖 → 产出的包拷到运行阶段
# 2. 运行阶段只装运行时依赖 → 镜像体积缩小 50%+
# 3. 最终镜像不含编译工具链 → 攻击面更小（没有 gcc 不能被利用）

# ── 构建阶段 ──────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
RUN pip install --no-cache-dir --user -e ".[dev]"

# ── 运行阶段 ──────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
