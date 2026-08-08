# ============================================================
# ai-code-reviewer 全栈单镜像 Dockerfile
# 前端 + 后端打包进一个镜像，部署只需要一个容器。
# 构建上下文：项目根目录
# 构建命令：docker build -t ai-code-reviewer .
# ============================================================

# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm config set registry https://registry.npmmirror.com \
    && npm ci

COPY frontend/ .
RUN npm run build

# ---------- 阶段 2：构建后端 wheel ----------
FROM python:3.11-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    PIP_DEFAULT_TIMEOUT=60

WORKDIR /build

COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app

RUN pip wheel --wheel-dir /wheels .

# ---------- 阶段 3：运行时 ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 创建非 root 用户
RUN addgroup --gid 1000 appuser \
    && adduser --disabled-password --gecos "" --uid 1000 --gid 1000 appuser

# 安装后端依赖
COPY --from=backend-builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

# 后端代码 + 迁移 + 脚本
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/scripts ./scripts

# 前端静态文件（由阶段 1 构建）
COPY --from=frontend-builder /frontend/dist ./app/static

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
