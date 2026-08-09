# 部署指南

> 当前文档覆盖三种部署方式，按推荐程度排序：
>
> 1. **Docker 单镜像部署** — 推荐生产用，一个容器跑前后端
> 2. **pip 安装部署（非 Docker）** — 裸机/虚拟机直接跑 Python 服务
> 3. **Docker Compose 开发模式** — 前后端分离，本地开发用

---

## 一、Docker 单镜像部署（推荐）

前端静态资源已打包进镜像，一个容器提供完整服务（管理台 + API + Webhook）。

### 1.1 前置条件

- Docker 24+
- 已准备好 PostgreSQL 15 / MySQL 8.0（可以是容器或独立服务）
- 后端所在机器可访问 GitLab 内网地址

### 1.2 启动

```bash
# 拉取镜像（从 GHCR 或自行构建）
docker pull ghcr.io/gexingw/ai-code-reviewer:latest  # 替换为实际镜像地址

# 或者本地从源码构建
docker build -t ai-code-reviewer .

# 启动
docker run -d \
  --name ai-code-reviewer \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://user:pass@db-host:5432/ai_code_reviewer" \
  -e SECRET_KEY="your-fernet-key" \
  -e ADMIN_PASSWORD="your-admin-password" \
  -e JWT_SECRET="your-jwt-secret" \
  -e INTERNAL_API_TOKEN="your-internal-token" \
  ai-code-reviewer
```

### 1.3 初始化数据库

首次启动前需要执行数据库迁移和种子数据：

```bash
# 执行迁移
docker exec ai-code-reviewer alembic upgrade head

# 写入种子数据（默认规则、引擎配置等）
docker exec ai-code-reviewer python scripts/seed.py
```

#### SQL 归档文件

镜像内 `/app/sql/` 目录附带了可追溯的 SQL 文件，方便离线部署或 DBA 审阅：

- `sql/schema-full.sql`：全量初始化 SQL（从空库到当前版本）
- `sql/migrations/*.sql`：每个版本的增量 DDL
- `sql/VERSION`：当前版本对应的 Alembic revision

新部署也可以直接执行 `sql/schema-full.sql` 建库，跳过 `alembic upgrade head`。

### 1.4 访问入口

- 管理台：`http://<server-ip>:8000/`
- 后端健康检查：`http://<server-ip>:8000/health`
- OpenAPI 文档：`http://<server-ip>:8000/docs`

---

## 二、源码安装部署（非 Docker）

从源码安装运行，适合裸机/虚拟机部署。

### 2.1 前置条件

- Python 3.11+
- PostgreSQL 15 / MySQL 8.0
- systemd（推荐用于进程管理）

### 2.2 安装

```bash
# 克隆代码
git clone https://github.com/geXingW/ai-code-reviewer.git
cd ai-code-reviewer/backend

# 安装（推荐用虚拟环境）
python -m venv /opt/ai-code-reviewer/venv
source /opt/ai-code-reviewer/venv/bin/activate
pip install -e .
```

### 2.3 配置

创建环境变量文件：

```bash
cat > /opt/ai-code-reviewer/.env << 'EOF'
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_code_reviewer
SECRET_KEY=CHANGE_ME_GENERATE_A_REAL_FERNET_KEY
ADMIN_USERNAME=admin
ADMIN_PASSWORD=CHANGE_ME_STRONG_PASSWORD
JWT_SECRET=CHANGE_ME_32_BYTES_MIN
JWT_ALGORITHM=HS256
JWT_EXPIRES_IN=86400
INTERNAL_API_TOKEN=CHANGE_ME_INTERNAL_TOKEN
DEFAULT_REVIEW_ENGINE=llm-direct
CORS_ORIGINS=["http://localhost:8000"]
EOF
```

> **注意**：GitLab 相关配置（`GITLAB_BASE_URL`、`GITLAB_TOKEN`、`GITLAB_WEBHOOK_SECRET`）已下沉到项目级。
> 启动后在管理台创建项目时填写每个项目的 GitLab 地址、Access Token 和 Webhook Secret。

生成 Fernet 密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2.4 初始化数据库

```bash
source /opt/ai-code-reviewer/venv/bin/activate
set -a; source /opt/ai-code-reviewer/.env; set +a

cd /opt/ai-code-reviewer/backend

alembic upgrade head
python scripts/seed.py
```

### 2.5 systemd 服务（推荐）

```ini
# /etc/systemd/system/ai-code-reviewer.service
[Unit]
Description=AI Code Reviewer
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/ai-code-reviewer
EnvironmentFile=/opt/ai-code-reviewer/.env
ExecStart=/opt/ai-code-reviewer/venv/bin/python app.py --host 0.0.0.0 --port 8000 --migrate
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

启动：

```bash
systemctl daemon-reload
systemctl enable --now ai-code-reviewer
systemctl status ai-code-reviewer
```

### 2.6 访问

同 Docker 单镜像部署，服务监听在 `:8000`。

---

## 三、Docker Compose 开发模式（前后端分离）

适合本地开发、联调。前后端各自热更新，前端走 Vite dev server。

### 3.1 前置条件

- Docker 24+ 与 Docker Compose v2
- （可选）后端服务所在机器可访问 GitLab 内网地址

### 3.2 启动

```bash
# 1. 克隆代码
git clone https://github.com/geXingW/ai-code-reviewer.git
cd ai-code-reviewer

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少替换 SECRET_KEY 等敏感配置

# 3. 启动全套服务（PostgreSQL + 后端 + 前端）
docker compose --profile postgres up -d --build

# 用 MySQL 替代 PostgreSQL：
# docker compose --profile mysql up -d --build
```

### 3.3 访问入口

- 管理台（Vite dev server）：`http://localhost:5173`
- 后端 API：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`

后端容器启动时自动执行：
1. `python scripts/seed.py` — 写入种子数据
2. `python app.py --reload --migrate` — 启动服务（带热重载 + 自动迁移）

---

## 四、仅部署后端（不需要前端管理台）

某些场景下只需要 API 服务（如纯 Webhook + Jenkins 触发模式），可以只部署后端。

### Docker 方式

用 `backend/Dockerfile` 构建纯后端镜像：

```bash
cd backend
docker build -t ai-code-reviewer-backend .
```

### pip 方式

从源码安装的包本身就是纯后端。如果 `static/` 目录不存在，FastAPI 不会挂载静态文件，完全不影响 API 服务。

---

## 五、环境变量参考

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DATABASE_URL` | 数据库连接串（async） | `postgresql+asyncpg://...` |
| `SECRET_KEY` | Fernet 加密密钥（必填） | 需设置 |
| `INTERNAL_API_TOKEN` | 内部调用令牌（Jenkins/Webhook） | `test-internal-token` |
| `ADMIN_USERNAME` | 管理后台用户名 | `admin` |
| `ADMIN_PASSWORD` | 管理后台密码 | `admin` |
| `JWT_SECRET` | JWT 签名密钥（≥32字节） | 需设置 |
| `JWT_ALGORITHM` | JWT 签名算法 | `HS256` |
| `JWT_EXPIRES_IN` | JWT 有效期（秒） | `86400` |
| `GITLAB_BASE_URL` | GitLab 实例默认地址（项目未配置时的兜底值） | `https://gitlab.com` |
| `DEFAULT_REVIEW_ENGINE` | 默认评审引擎 | `llm-direct` |
| `CORS_ORIGINS` | 允许的跨域来源 | `["http://localhost:5173"]` |

> **注意**：GitLab Access Token、Webhook Secret 等凭证已下沉到**项目级**配置，在管理台创建项目时填写，不再通过全局 ENV 配置。详见 [GitLab Webhook 接入指南](gitlab-setup.md)。

---

## 六、常用运维命令

### Docker 单镜像

```bash
# 查看日志
docker logs -f ai-code-reviewer

# 重新执行迁移
docker exec ai-code-reviewer alembic upgrade head

# 重新写入种子数据
docker exec ai-code-reviewer python scripts/seed.py

# 重启
docker restart ai-code-reviewer
```

### systemd（裸机部署）

```bash
# 查看状态
systemctl status ai-code-reviewer

# 查看日志
journalctl -u ai-code-reviewer -f

# 重启
systemctl restart ai-code-reviewer
```

### Docker Compose 开发模式

```bash
# 查看状态
docker compose ps

# 查看后端日志
docker compose logs -f backend

# 重新执行迁移
docker compose exec backend alembic upgrade head

# 停止（保留数据）
docker compose down

# 停止并清空数据
docker compose down -v
```

---

## 七、排错

**健康检查显示 `db=error`**
- 确认数据库已启动且网络可达
- 检查 `DATABASE_URL` 格式和凭据是否正确
- 查看应用日志

**管理台页面空白 / 加载失败**
- Docker 单镜像模式：确认访问的是 `http://<host>:8000/` 而不是 `:5173`
- Compose 开发模式：确认 `frontend` 服务已启动，访问 `:5173`
- 浏览器控制台查看具体错误

**GitLab Webhook 返回 401**
- 确认 GitLab Webhook 的 Secret Token 与**管理台项目配置的 Webhook Secret** 一致（项目级，非全局 ENV）
- 确认项目已在管理台创建，且 `gitlab_project_id` 与 GitLab 侧一致
- GitLab 通过 `X-Gitlab-Token` 请求头传递该值

**API 调用返回 401**
- 管理台接口：确认 JWT token 有效且未过期
- 内部接口（`/api/reviews`）：确认 `X-Internal-Token` 与 `INTERNAL_API_TOKEN` 一致
