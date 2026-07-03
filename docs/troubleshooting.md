# 常见问题排查

> 适用范围：v0.1.0 MVP 内网试运行。遇到问题先看本文档；更多背景见 [docs/setup.md](setup.md)、[docs/gitlab-setup.md](gitlab-setup.md)、[docs/jenkins-setup.md](jenkins-setup.md)。
> 通用排错第一步：`docker compose ps` 看服务状态，`docker compose logs -f backend` 看后端日志。

## 一、安装 / 启动问题

### 1.1 `docker compose up` 起不来

**现象**：服务反复重启或容器 `unhealthy`。

排查：

- 确认 Docker 24+ 与 Compose v2：`docker version` / `docker compose version`。
- `docker compose ps` 看哪个服务不健康。
- `docker compose logs <service>` 看具体报错。
- 端口冲突：5432（Postgres）/ 6379（Redis）/ 8000（backend）/ 5173（frontend）被占用。`docker compose down` 后释放端口或改 `docker-compose.yml` 端口映射。

### 1.2 PostgreSQL 连不上 / `db=error`

**现象**：`/health` 返回 `db=error`，或后端日志报 `connection refused` / `authentication failed`。

排查：

- 确认 `postgres` 容器 healthy：`docker compose ps`。
- 检查 `DATABASE_URL` 与 Compose 环境一致。容器内应是 `postgresql+asyncpg://ai_reviewer:<pwd>@postgres:5432/ai_code_reviewer`（主机名 `postgres`），本地直连用 `localhost`。
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` 在 `.env` 与 Compose 间必须一致；改过密码需 `docker compose down -v` 清掉旧数据卷再重启（旧卷里仍是旧密码）。
- 首次启动后端会跑 `alembic upgrade head`；若迁移失败，`docker compose exec backend alembic upgrade head` 手动重试并查看报错。
- 本地跑 pytest 时连接串默认 `postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer`，需确保该库与账号存在（`docker compose up postgres` 即可创建）。

### 1.3 Redis 连不上 / `redis=error`

**现象**：`/health` 返回 `redis=error`。

排查：

- 确认 `redis` 容器 healthy。
- 容器内 `REDIS_URL` 应为 `redis://redis:6379/0`（主机名 `redis`）。
- `docker compose exec redis redis-cli ping` 应返回 `PONG`。

### 1.4 后端启动报 `SECRET_KEY` / Fernet 错误

**现象**：启动即崩溃，或读写 provider/project 时报解密失败。

排查：

- `SECRET_KEY` 必须是合法 Fernet key，用 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成。
- **初始化后不要更换** `SECRET_KEY`：旧加密字段（provider api_key、project token 等）将无法解密。若必须换，需清空相关表重新录入。
- `.env` 中 `SECRET_KEY` 不能是占位符 `CHANGE_ME...`。

### 1.5 前端能打开但接口 401 / 报错

**现象**：管理台打开，但列表为空或请求 401。

排查：

- 确认 `frontend/nginx.conf` 把 `/api`、`/health` 代理到 `backend:8000`。
- 管理台输入的 `INTERNAL_API_TOKEN` 与 `.env` 完全一致；该 token 仅保存在页面内存，刷新即丢。
- 浏览器 Network 看 `/api/reviews/recent` 状态码：401 通常是 token 不匹配；5xx 看 backend 日志。

### 1.6 `alembic upgrade head` 报错

- 多半是数据库非空且与迁移历史不一致。`docker compose exec backend alembic current` 看当前版本。
- 开发环境可 `docker compose down -v` 清数据卷后重启（会丢数据，仅限试运行）。
- 切勿在生产直接 `down -v`；用 `alembic downgrade` 回退或手动修表。

## 二、GitLab Webhook 不触发

### 2.1 GitLab 测试 Webhook 返回 401

- GitLab Webhook 配置页的 **Secret Token** 与后端 `GITLAB_WEBHOOK_SECRET` 不一致。
- 后端未重启，仍在用旧环境变量：`docker compose restart backend`。
- 后端收到的 `X-Gitlab-Token` 请求头被反向代理剥离——检查 nginx/网关是否透传该自定义头。

### 2.2 GitLab 显示已投递，但后端无反应

- URL 写错：正确地址是 `http://<backend-host>:8000/api/webhooks/gitlab`（注意带 `/api` 前缀）。
- 触发事件没勾选 **Merge request events**——只勾 push 等不会触发。
- 事件被忽略返回 `202 processed:false, reason:"ignored_event"`：只有 `X-Gitlab-Event: Merge Request Hook` 且 `object_kind=merge_request` 才处理。
- MR `action` 不在 `open`/`reopen`/`update` 内（如 `close`）→ `reason:"ignored_action"`，属正常忽略。

### 2.3 Webhook 超时

- GitLab 服务器无法访问后端地址（内网不通 / 防火墙）。在后端机器上 `curl http://localhost:8000/health` 自测。
- 后端同步执行一次较慢的 review 占住请求。MVP 建议先用小 MR 验证；大 MR 可能让 GitLab 等到超时。
- 反向代理超时时间过短：调大 proxy_read_timeout。

### 2.4 payload 422

- 缺字段：`project.id`、`object_attributes.iid`、`object_attributes.last_commit.id` 等必填项缺失。
- `last_commit.id` 为空 → `Invalid GitLab merge request payload: missing last commit id`。
- 不同 GitLab 版本字段略有差异；用 `docker compose logs backend` 看具体缺失项。

## 三、Jenkins Pipeline 401 / 422

### 3.1 `POST /api/reviews` 返回 401

- `X-Internal-Token` 与后端 `INTERNAL_API_TOKEN` 不一致（常量时间比较，多/少一个字符都会失败）。
- Jenkins credential 绑定的变量名写错，或 `withCredentials` 没把 secret 注入到 shell 环境。
- 后端未重启加载新 token。
- 注意：`/api/reviews` 走 `X-Internal-Token`，**不是** JWT Bearer。用错认证方式也会 401。

### 3.2 `POST /api/reviews` 返回 422

- `project_id`、`mr_iid` 必须是正整数（`gt=0`）。
- `target_branch`、`source_branch`、`commit_sha` 不能为空字符串。
- `web_url` 必须是合法 http(s) URL；`javascript:` 等不安全 scheme 会被拒。
- 请求体含未定义字段 → 422（请求体 `extra="forbid"`）。对照 [docs/api.md](api.md#十三jenkins-同步评审x-internal-token) 的字段表。
- Jenkins 变量未展开（如 `${gitlabMergeRequestIid}` 为空）→ 先打印环境变量定位变量名（**不要打印凭据**）。

### 3.3 Jenkins 变量名对不上

不同 GitLab 插件版本变量名不同。在 MR 构建里打印环境变量名定位（排除凭据）：

```bash
printenv | sort | grep -E 'gitlab|GITLAB|CHANGE_|GIT_' | grep -vi 'token\|password\|secret'
```

常见变量：`gitlabMergeRequestTargetProjectId`、`gitlabMergeRequestIid`、`gitlabTargetBranch`、`gitlabSourceBranch`、`gitlabMergeRequestUrl`、`GIT_COMMIT`。

### 3.4 Jenkins stage 超时

- 先用小 MR 验证字段映射与连通性。
- 评审接口当前同步执行；大 MR 耗时长，建议 stage 超时设 10–15 分钟。
- 检查后端能否访问 GitLab（`GITLAB_BASE_URL` / `GITLAB_TOKEN` 配置与网络）。
- 查看 `docker compose logs -f backend` 中该 MR 的安全摘要。

### 3.5 `has_blocker=true` 但 MR 没被阻断

- Jenkins stage 是否真的失败：`has_blocker=true` 时脚本应以非 0 退出码失败。
- GitLab 项目是否要求 **pipeline 成功后才能合并**（Settings → Merge requests → Merge checks）。
- 目标分支保护规则是否允许 Maintainer 绕过；确认未开启 "Pipelines must succeed" 的绕过选项。
- 确认 Jenkins 构建被 GitLab 识别为该 MR 的 pipeline（commit sha 一致）。

## 四、评审结果为空

**现象**：`POST /api/reviews` 返回 `finding_count=0` / `has_blocker=false`，GitLab MR 没有行级 discussion，只有「No findings」摘要。

排查（按概率从高到低）：

1. **引擎未注入 provider（MVP 已知边界）**：`llm-direct` 引擎在 `ReviewContext.provider is None` 时安全降级为「不产出 finding」。当前编排器尚未把 DB 中的 provider 配置注入运行时上下文。日志会出现 `llm-direct review skipped: provider config missing`。此为预期行为，待 provider 注入 Issue 落地。
2. **diff 被过滤**：`ignore_paths`、`max_diff_bytes`（默认 200KB）、二进制文件、纯删除文件会被 `filter_gitlab_changes` 跳过。日志看 diff hunk 数量。
3. **MR diff 为空**：MR 没有改动，或 GitLab 返回的 `changes` 数组为空。`docker compose logs backend` 确认拉到的 diff。
4. **LLM 返回非法 JSON**：引擎会捕获 `JSONDecodeError`/`ValidationError` 并降级为空 finding，日志 `llm-direct review degraded to no findings`。检查模型是否遵守输出契约（返回纯 JSON，不要包在 prose 里）。
5. **finding 被误报历史过滤**：若 `ReviewContext.history` 含同 `rule_id`+`file_path`+近似行号+相同标题的已确认误报，引擎会主动抑制。确认是否该报。
6. **GitLab Token 权限不足**：能读 diff 但写 discussion 失败（discussion 创建是 best-effort，失败只记日志不阻断）。日志 `failed to create GitLab MR discussion`。

> 当前 MVP 编排器**不把 finding 持久化到数据库**——`/api/findings` 仅返回通过管理 API 手动创建的记录，评审产出的 finding 通过 GitLab discussion 与 `POST /api/reviews` 响应摘要体现。若 `/api/findings` 为空但评审有结果，属预期。

## 五、评审超时 / 引擎异常

**现象**：`POST /api/reviews` 返回 `status="engine_error"`，GitLab 摘要 note 含「Engine error」，commit status 可能 `failed` 也可能 `success`。

机制：引擎 `review()` 抛异常时，编排器走 `_handle_engine_error`：

- `block_on_engine_error=true` 或策略 `block_severity=ENGINE_ERROR_ONLY` → `has_blocker=true`，commit status `failed`。
- 否则 → `has_blocker=false`，commit status `success`（不阻断，仅评论）。

排查：

1. **LLM 提供商超时**：`llm-direct` 默认 `timeout_seconds=30`。模型响应慢或网络抖动会超时。日志看异常类型。
2. **provider 不可达**：`base_url` 错误、API key 失效、出口网络不通。用 `curl` 直接打 provider 的 chat 端点验证。
3. **是否要阻断**：若希望引擎异常也阻断合并，给目标分支策略设 `block_on_engine_error=true`（或 `block_severity=ENGINE_ERROR_ONLY`）。
4. **异常详情不外泄**：编排器不会把异常堆栈写进 GitLab note（只写固定「engine failed」文案），敏感信息不会泄漏到 MR。详细异常只在后端日志。
5. **Jenkins 侧**：`status="engine_error"` 时按 `has_blocker` 决定是否失败，与 `done` 一致。

## 六、GitLab 行级 Discussion 位置不对 / 没出现

- finding 的 `line_number` 必须指 diff **新侧**行号（post-merge）。引擎会校验 `line_number` 是否落在新增行内，不在则丢弃该 finding。
- diff refs（`base_sha`/`start_sha`/`head_sha`）缺失时回退到 webhook 的 target/source commit sha，可能导致 GitLab 拒绝 position——确保 MR diff_refs 可用。
- GitLab Token 需具备写 discussion 权限。
- finding 无 `line_number`（`None`）时不会创建行级 discussion，只出现在摘要 note 里。

## 七、误报闭环不生效

- **confirm/reject 返回 409**：finding 当前 `fp_status` 不是 `PENDING`。先 `POST /api/findings/{id}/false-positive` 标记为 PENDING。
- **confirm 后 `negative_examples` 没新增**：确认时会用 `finding.existing_code`（回退 `description`/`title`）作为 `code_snippet` 写入。检查 finding 是否有可用的代码片段。
- **负样本没影响后续评审**：当前 MVP 引擎对 `ReviewContext.history` 的消费取决于引擎实现与 provider 注入；负样本库已沉淀，运行时注入待后续 Issue。

## 八、认证 / JWT 问题

- **401 Invalid admin token**：JWT 缺失/过期/签名不匹配。`JWT_SECRET` 或 `JWT_ALGORITHM` 改过后旧 token 全部失效，重新登录。
- **401 Invalid credentials**：`ADMIN_USERNAME`/`ADMIN_PASSWORD` 不对。
- **token 提前失效**：检查 `JWT_EXPIRES_IN`（默认 86400 秒）与客户端时钟。
- MVP 为单账号，JWT `sub` 必须等于 `ADMIN_USERNAME`；自行签发的不合规 token 会被拒。

## 九、日志与诊断

- 后端日志：`docker compose logs -f backend`。`LOG_LEVEL=DEBUG` 可看更详细流水。
- `/health`：快速判断 db / redis 连通性。
- `/api/engines` 与 `/api/engines/{name}/health`：判断引擎注册与健康状态。
- `POST /api/reviews` 响应里的 `policy_applied` / `finding_count` / `has_blocker` 是定位「阻断与否」的第一手信息。
- 安全提醒：日志与异常中不应出现明文 token / api_key；若发现泄漏，立即 revoke 并重建。

## 十、仍未解决

收集以下信息后到 Issue 反馈：

1. `docker compose ps` 与 `/health` 输出。
2. `docker compose logs backend` 相关时间段日志（脱敏）。
3. 复现请求（curl 命令，去掉真实 token）与完整响应。
4. GitLab / Jenkins 版本与相关配置截图。
