# MVP 部署指南

> 当前文档面向 GitLab + Jenkins 内网试运行。它可以支撑 MVP 联调和小范围试用，但还不是生产级高可用部署方案。

## 一、部署拓扑

MVP Compose 栈包含 4 个服务：

- `frontend`：React/Vite 管理台，Nginx 托管静态资源，并反向代理 `/api`、`/health` 到后端。
- `backend`：FastAPI 服务，接收 GitLab Webhook、Jenkins 同步触发和管理台请求。
- `postgres`：保存项目、规则、供应商、评审记录等结构化数据。
- `redis`：保留给运行时缓存/异步任务；当前 `/health` 会检查连通性。

## 二、前置条件

- Docker 24+ 与 Docker Compose v2。
- 后端服务所在机器可访问 GitLab 内网地址。
- GitLab 项目中准备一个 Access Token，至少具备读取 MR diff、写入 MR discussion/status 所需权限。
- Jenkins 节点可访问后端 `POST /api/reviews`。
- 如果使用真实 LLM 评审，当前 MVP 暂无 provider 配置 UI，请先通过数据库或后续配置脚本写入 provider；不要把 provider API Key 写入仓库。

## 三、初始化配置

复制环境变量模板：

```bash
cp .env.example .env
```

生成 Fernet 密钥，用于加密数据库中的供应商密钥等敏感字段：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

编辑 `.env`，至少替换这些值：

- `POSTGRES_PASSWORD`：PostgreSQL 密码。
- `SECRET_KEY`：上一步生成的 Fernet key。初始化后应长期保持不变，否则旧加密字段无法解密。
- `GITLAB_BASE_URL`：GitLab 实例地址，例如 `https://gitlab.example.com`。
- `GITLAB_TOKEN`：GitLab Access Token。
- `GITLAB_WEBHOOK_SECRET`：GitLab Webhook 使用的共享密钥。
- `INTERNAL_API_TOKEN`：Jenkins 和管理台调用内部接口时使用的 token。
- `CORS_ORIGINS`：管理台地址列表；本地默认是 `["http://localhost:5173"]`。

安全建议：

- `.env` 不提交到 Git。
- token 只通过环境变量、密钥管理系统或部署平台注入。
- 飞书、Issue、PR 评论里出现过的真实 token 建议立即 revoke 后重建。

## 四、一键启动

推荐使用仓库内一键脚本启动，它会在首次运行时创建 `.env`、生成本地随机密钥，并拉起 Compose 栈：

```bash
./scripts/start-mvp.sh
```

如果已经手工准备好 `.env`，也可以直接使用 Compose：

```bash
docker compose up -d --build
```

启动后后端容器会自动执行：

1. `alembic upgrade head`：初始化/升级数据库结构。
2. `python scripts/seed.py`：写入默认引擎、默认阻断策略和基础规则。
3. `uvicorn app.main:app`：启动 API 服务。

访问入口：

- 管理台：`http://localhost:5173`
- 后端健康检查：`http://localhost:8000/health`

管理台的内部 token 只保存在页面表单状态中，刷新后会丢失，不会写入 `localStorage` 或 `sessionStorage`。

## 五、GitLab Webhook 配置

在目标 GitLab 项目中打开 Webhook 设置：

- URL：`http://<backend-host>:8000/api/webhooks/gitlab`
- Secret Token：填 `.env` 中的 `GITLAB_WEBHOOK_SECRET`
- 触发事件：选择 Merge request events
- SSL verification：按内网证书情况开启或关闭

Webhook 处理逻辑会读取 MR diff，调用默认评审引擎，并把结果写回 GitLab MR Discussion。命中阻断级别时，会配合 Jenkins 同步接口让流水线失败。

## 六、Jenkins Pipeline 接入

Jenkins 可在 MR 构建阶段调用同步评审接口：

```groovy
stage('AI Code Review') {
  steps {
    sh '''
      curl -fsS -X POST "$AI_REVIEWER_URL/api/reviews" \
        -H "Content-Type: application/json" \
        -H "X-Internal-Token: $AI_REVIEWER_INTERNAL_TOKEN" \
        -d "{\
          \"project_id\": ${gitlabMergeRequestTargetProjectId},\
          \"mr_iid\": ${gitlabMergeRequestIid},\
          \"target_branch\": \"${gitlabTargetBranch}\",\
          \"source_branch\": \"${gitlabSourceBranch}\",\
          \"commit_sha\": \"${GIT_COMMIT}\",\
          \"project_path\": \"${gitlabSourceNamespace}/${gitlabSourceRepoName}\",\
          \"title\": \"${gitlabMergeRequestTitle}\",\
          \"web_url\": \"${gitlabMergeRequestUrl}\"\
        }" | tee ai-review-result.json
      python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path('ai-review-result.json').read_text())
if payload.get('has_blocker'):
    raise SystemExit('AI review found blocker issues')
PY
    '''
  }
}
```

变量名可能随 Jenkins GitLab 插件版本不同而变化。内网试跑时，可以先用管理台「手动触发 Review」验证同一个 MR，再把字段映射固化进 Jenkinsfile。

## 七、常用运维命令

查看服务状态：

```bash
docker compose ps
```

查看后端日志：

```bash
docker compose logs -f backend
```

重新执行迁移：

```bash
docker compose exec backend alembic upgrade head
```

重新写入种子数据：

```bash
docker compose exec backend python scripts/seed.py
```

停止服务但保留数据卷：

```bash
docker compose down
```

清空本地试运行数据：

```bash
docker compose down -v
```

## 八、排错

**后端健康检查显示 `db=error`**

- 查看 `docker compose ps`，确认 `postgres` 已 healthy。
- 检查 `.env` 中 `POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB` 是否和 Compose 配置一致。
- 查看 `docker compose logs postgres backend`。

**后端健康检查显示 `redis=error`**

- 确认 `redis` 容器 healthy。
- 检查后端容器内 `REDIS_URL` 是否是 `redis://redis:6379/0`。

**管理台可打开但接口报错**

- 确认 `frontend/nginx.conf` 代理到了 `backend:8000`。
- 确认输入的 `INTERNAL_API_TOKEN` 与 `.env` 完全一致。
- 查看浏览器 Network 中 `/api/reviews/recent` 的状态码，401 通常表示 token 不匹配。

**GitLab Webhook 返回 401**

- 确认 GitLab Webhook Secret Token 与 `.env` 的 `GITLAB_WEBHOOK_SECRET` 一致。
- 注意 GitLab 会通过 `X-Gitlab-Token` 请求头传递该值。

**GitLab MR 没有行级 Discussion**

- 确认 GitLab Token 具备写 MR discussion 权限。
- 确认 MR diff 没有超过服务限制；超限时系统会给出泛化错误摘要，不会外显内部异常。
- 查看后端日志中与该 project id / MR iid 相关的安全摘要。

## 九、生产化前仍需补齐

- 后端多副本部署和队列化执行，避免同步 review 占用 Web worker。
- HTTPS 终止、鉴权网关、IP allowlist 和审计日志。
- PostgreSQL/Redis 备份、恢复演练与监控告警。
- provider/project 配置管理 UI 的完整 CRUD 与权限模型。
- Jenkins/GitLab 合并阻断策略在内网项目中的端到端验收。
