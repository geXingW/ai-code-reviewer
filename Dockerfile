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

# ---------- 阶段 2：安装后端依赖 ----------
FROM python:3.11-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /build

# 创建虚拟环境（便于阶段 3 完整复制，不污染 runtime 标准库）
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Pre-install pip upgrade (no build tooling needed for requirements.txt install).
RUN pip install --upgrade pip

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------- 阶段 3：运行时 ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app

# 创建非 root 用户
RUN addgroup --gid 1000 appuser \
    && adduser --disabled-password --gecos "" --uid 1000 --gid 1000 appuser

# 从 builder 复制虚拟环境（已包含所有依赖）
COPY --from=backend-builder /opt/venv /opt/venv

# 后端代码（扁平结构，无 app/ 子包）
COPY backend/main.py backend/__main__.py backend/app.py ./
COPY backend/api ./api
COPY backend/core ./core
COPY backend/engines ./engines
COPY backend/integrations ./integrations
COPY backend/llm ./llm
COPY backend/models ./models
COPY backend/repositories ./repositories
COPY backend/schemas ./schemas
COPY backend/services ./services
COPY backend/static ./static
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/scripts ./scripts

# 前端静态文件（由阶段 1 构建）
COPY --from=frontend-builder /frontend/dist ./static

# Generate release SQL artifacts (full schema + incremental migrations)
RUN python scripts/generate_release_sql.py --output-dir /app/sql

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000", "--migrate"]
