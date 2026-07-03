# REST API 文档

> 适用范围：v0.1.0 MVP。后端默认监听 `http://localhost:8000`，交互式文档见 `http://localhost:8000/docs`（OpenAPI）。
> 所有示例中的 `$TOKEN` 指管理后台 JWT，`$INTERNAL_TOKEN` 指 `INTERNAL_API_TOKEN`。

## 一、认证方式

系统按调用方分为三类，每类使用不同的认证方式：

| 调用方 | 认证方式 | 请求头 | 适用端点 |
|---|---|---|---|
| 管理后台 / 运维 | JWT Bearer Token | `Authorization: Bearer <jwt>` | `/api/providers`、`/api/rules`、`/api/projects`、`/api/reviews/records`、`/api/findings`、`/api/false-positives/*`、`/api/negative-examples`、`/api/engines/configs` |
| Jenkins / 内部触发 | 内部共享令牌 | `X-Internal-Token: <token>` | `POST /api/reviews`、`GET /api/reviews/recent` |
| GitLab Webhook | Webhook 共享密钥 | `X-Gitlab-Token: <secret>` | `POST /api/webhooks/gitlab` |

`/health` 与 `/api/engines`（运行时引擎列表）无需认证。

### 1.1 JWT Bearer Token（管理 API）

先登录获取 JWT，再携带 `Authorization: Bearer <jwt>` 调用管理 API。JWT 由 `JWT_SECRET` + `JWT_ALGORITHM`（默认 `HS256`）签名，有效期 `JWT_EXPIRES_IN`（默认 86400 秒）。

```bash
# 登录
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

```bash
# 携带 token 调用管理 API
curl http://localhost:8000/api/providers \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

> MVP 为单账号模型（`ADMIN_USERNAME` / `ADMIN_PASSWORD`），JWT 的 `sub` 必须等于 `ADMIN_USERNAME`，否则返回 401。

### 1.2 X-Internal-Token（Jenkins / 内部触发）

`POST /api/reviews` 与 `GET /api/reviews/recent` 使用 `X-Internal-Token`，其值必须与后端 `INTERNAL_API_TOKEN` 完全一致（常量时间比较）。该 token 不走 JWT，供 Jenkins Pipeline 等服务间调用。

```bash
curl -X POST http://localhost:8000/api/reviews \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d '{...}'
```

### 1.3 X-Gitlab-Token（GitLab Webhook）

GitLab Webhook 配置页填写的 Secret Token 会通过 `X-Gitlab-Token` 请求头传递，后端用常量时间比较校验，值必须等于 `GITLAB_WEBHOOK_SECRET`。

```bash
curl -X POST http://localhost:8000/api/webhooks/gitlab \
  -H "X-Gitlab-Event: Merge Request Hook" \
  -H "X-Gitlab-Token: $GITLAB_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

## 二、通用约定

### 2.1 列表端点通用参数

所有 `GET` 列表端点（providers / rules / projects / reviews / findings / negative-examples / engines/configs）支持：

| 参数 | 类型 | 说明 |
|---|---|---|
| `limit` | int (1-100) | 每页条数，默认 20 |
| `offset` | int (≥0) | 偏移量，默认 0 |
| `sort` | string | 排序字段，`-` 前缀降序，如 `-created_at`（默认） |
| `q` | string | 关键词模糊搜索（部分端点支持） |
| `enabled` | bool | 按启用状态筛选（providers / rules / projects / engines/configs） |

**分页响应信封**：

```json
{
  "items": [ /* 该页记录 */ ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

### 2.2 各资源允许的排序字段

| 资源 | 允许字段 |
|---|---|
| providers | `created_at`、`updated_at`、`name`、`enabled` |
| rules | `created_at`、`updated_at`、`rule_id`、`title`、`enabled` |
| projects | `created_at`、`updated_at`、`name`、`enabled` |
| reviews | `created_at`、`updated_at`、`status`、`mr_iid`、`finding_count` |
| findings | `created_at`、`updated_at`、`severity`、`file_path`、`fp_status` |
| negative_examples | `created_at`、`updated_at`、`rule_id` |
| engines | `created_at`、`updated_at`、`name`、`enabled` |

传入不在白名单内的字段返回 `400 Invalid sort field`。

### 2.3 敏感字段脱敏

`ProviderRead.api_key`、`ProjectRead.gitlab_access_token`、`ProjectRead.webhook_secret` 在响应中固定返回 `"****"`，原始值经 Fernet 加密落库，不会外显。

## 三、错误码

| 状态码 | 含义 | 典型场景 |
|---|---|---|
| `200 OK` | 请求成功 | GET / PATCH / 同步评审 |
| `201 Created` | 创建成功 | POST 创建资源 |
| `202 Accepted` | 已受理（异步语义） | GitLab Webhook |
| `204 No Content` | 删除成功 | DELETE |
| `400 Bad Request` | 请求非法 | 排序字段非法、DB 持久化失败、Webhook URL scheme 不安全 |
| `401 Unauthorized` | 认证失败 | JWT 缺失/过期/非法、内部 token 不匹配、Webhook 密钥不匹配、凭据错误 |
| `404 Not Found` | 资源不存在 | 按 ID 查询/更新/删除不存在记录 |
| `409 Conflict` | 冲突 | 唯一约束冲突、误报状态不在 PENDING |
| `422 Unprocessable Entity` | 校验失败 | 请求体字段缺失/类型错误/超出约束 |

**错误响应统一格式**（FastAPI 默认）：

```json
{ "detail": "Provider not found" }
```

请求体校验失败（422）示例：

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "name"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

## 四、认证端点

### POST /api/auth/login

MVP 单账号登录，返回标准 JWT。

```json
// 请求体
{ "username": "admin", "password": "admin" }
```

```json
// 200
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 86400 }
```

凭据错误返回 `401 Invalid credentials`。

## 五、健康与运行时引擎

### GET /health

服务、数据库、Redis 健康检查（无需认证）。

```json
// 200
{ "status": "ok", "version": "0.1.0-dev", "db": "ok", "redis": "ok" }
```

`db=error` 或 `redis=error` 表示对应依赖不可达，但仍返回 200 以便探活。

### GET /api/engines

列出运行时引擎注册表中所有引擎，并对每个引擎做一次轻量健康探测。

```json
// 200
[
  {
    "name": "llm-direct",
    "supports_feedback": true,
    "requires_repo_clone": false,
    "healthy": true,
    "health_status": "ok"
  }
]
```

### GET /api/engines/{name}/health

单个引擎的详细健康报告。引擎未注册返回 `404 Engine '{name}' is not registered.`。

```json
// 200
{
  "name": "llm-direct",
  "status": "ok",
  "message": "LLMDirectEngine is configured; provider health is checked during review calls.",
  "details": {
    "implementation": "llm-direct",
    "supports_feedback": true,
    "requires_repo_clone": false,
    "timeout_seconds": 30.0
  }
}
```

## 六、LLM 供应商管理（JWT）

### POST /api/providers

```json
// 请求体
{
  "name": "ark",
  "protocol": "openai_compatible",
  "base_url": "https://llm.example.com/v1",
  "api_key": "sk-xxxxx",
  "model": "glm-4",
  "temperature": 0.0,
  "max_tokens": 4096,
  "extra_headers": null,
  "enabled": true
}
```

`protocol` 取值：`openai_compatible` / `anthropic` / `custom`。

```json
// 201
{
  "id": "8f6e...uuid",
  "name": "ark",
  "protocol": "openai_compatible",
  "base_url": "https://llm.example.com/v1",
  "api_key": "****",
  "model": "glm-4",
  "temperature": 0.0,
  "max_tokens": 4096,
  "extra_headers": null,
  "enabled": true,
  "created_at": "2026-07-03T08:00:00Z",
  "updated_at": "2026-07-03T08:00:00Z"
}
```

### GET /api/providers

支持 `limit` / `offset` / `sort` / `q`（按 `name` 模糊）/ `enabled`。

```bash
curl "http://localhost:8000/api/providers?limit=10&offset=0&sort=-created_at&enabled=true" \
  -H "Authorization: Bearer $TOKEN"
```

```json
// 200
{ "items": [ { "id": "...", "name": "ark", "api_key": "****", ... } ], "total": 1, "limit": 10, "offset": 0 }
```

### GET /api/providers/{id}

返回单个供应商，`api_key` 仍脱敏。

### PATCH /api/providers/{id}

部分更新，未传字段保持不变。

```json
// 请求体（任意子集）
{ "enabled": false, "model": "glm-4-air" }
```

```json
// 200
{ "id": "...", "name": "ark", "model": "glm-4-air", "enabled": false, "api_key": "****", ... }
```

### DELETE /api/providers/{id}

成功返回 `204 No Content`；不存在返回 `404`。

## 七、规则库管理（JWT）

### POST /api/rules

```json
// 请求体
{
  "rule_id": "general.hardcoded-secret",
  "title": "硬编码密钥/密码",
  "prompt_snippet": "检查代码中是否硬编码密钥、密码、Token 或其他敏感凭据。",
  "severity_default": "BLOCKER",
  "languages": ["python", "java"],
  "path_patterns": [],
  "enabled": true,
  "grace_period_until": null
}
```

`severity_default` 取值：`INFO` / `WARNING` / `BLOCKER`。`rule_id` 为跨项目共享的人类可读键，需唯一。

```json
// 201
{
  "id": "uuid",
  "rule_id": "general.hardcoded-secret",
  "title": "硬编码密钥/密码",
  "prompt_snippet": "...",
  "severity_default": "BLOCKER",
  "languages": ["python", "java"],
  "path_patterns": [],
  "enabled": true,
  "grace_period_until": null,
  "created_at": "...",
  "updated_at": "..."
}
```

### GET /api/rules

支持 `limit` / `offset` / `sort` / `q`（按 `rule_id` 或 `title` 模糊）/ `enabled`。

### GET /api/rules/{id}

返回单个规则。`{id}` 为规则 UUID（非 `rule_id` 字符串）。

### PATCH /api/rules/{id} / DELETE /api/rules/{id}

同供应商语义。

## 八、项目管理（JWT，含嵌套 rules + block_policies）

### POST /api/projects

创建项目时可同时传入 `rules`（规则关联）和 `block_policies`（分支阻断策略）嵌套数组。

```json
// 请求体
{
  "name": "demo",
  "gitlab_project_id": "group/demo",
  "gitlab_access_token": "glp-xxxxx",
  "webhook_secret": "hook-secret",
  "engine_id": null,
  "provider_id": "8f6e...uuid",
  "enabled": true,
  "timeout_seconds": 300,
  "max_files": 50,
  "ignore_paths": null,
  "default_block_severity": "BLOCKER",
  "deleted_at": null,
  "rules": [
    {
      "rule_id": "uuid-of-rule",
      "enabled": true,
      "severity_override": "BLOCKER"
    }
  ],
  "block_policies": [
    {
      "branch_pattern": "master",
      "block_severity": "BLOCKER",
      "block_on_engine_error": false,
      "require_all_resolved": false,
      "priority": 1
    },
    {
      "branch_pattern": "*",
      "block_severity": "NONE",
      "block_on_engine_error": false,
      "require_all_resolved": false,
      "priority": 99
    }
  ]
}
```

`block_severity` 取值：`NONE` / `INFO` / `WARNING` / `BLOCKER` / `ENGINE_ERROR_ONLY`。`rules[].rule_id` 是规则的 UUID；`severity_override` 可选，覆盖规则 `severity_default`。

```json
// 201
{
  "id": "uuid",
  "name": "demo",
  "gitlab_project_id": "group/demo",
  "gitlab_access_token": "****",
  "webhook_secret": "****",
  "engine_id": null,
  "provider_id": "8f6e...uuid",
  "enabled": true,
  "timeout_seconds": 300,
  "max_files": 50,
  "ignore_paths": null,
  "default_block_severity": "BLOCKER",
  "deleted_at": null,
  "rules": [
    { "project_id": "uuid", "rule_id": "uuid-of-rule", "enabled": true, "severity_override": "BLOCKER", "created_at": "...", "updated_at": "..." }
  ],
  "block_policies": [
    { "id": "uuid", "project_id": "uuid", "branch_pattern": "master", "block_severity": "BLOCKER", "block_on_engine_error": false, "require_all_resolved": false, "priority": 1, "created_at": "...", "updated_at": "..." }
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

### GET /api/projects

支持 `limit` / `offset` / `sort` / `q`（按 `name` 或 `gitlab_project_id` 模糊）/ `enabled`。

### GET /api/projects/{id}

返回单个项目，含嵌套 `rules` 与 `block_policies`。

### PATCH /api/projects/{id}

部分更新。**注意：传入 `rules` 或 `block_policies` 会整体替换**对应集合（非增量合并）。

```json
// 请求体（任意子集；rules/block_policies 传则整体替换）
{
  "name": "demo-renamed",
  "rules": [{ "rule_id": "uuid-of-rule", "enabled": false, "severity_override": "WARNING" }],
  "block_policies": [
    { "branch_pattern": "release/*", "block_severity": "WARNING", "priority": 1 }
  ]
}
```

### DELETE /api/projects/{id}

级联删除项目下的规则关联、阻断策略与评审记录。返回 `204`。

## 九、评审记录管理（JWT）

> 注意：`/api/reviews` 下有两组语义不同的端点 —— 同步触发评审（Jenkins，`X-Internal-Token`）与管理评审记录（JWT）。后者用于内部建表/修正。

### GET /api/reviews 与 GET /api/reviews/records

两个路径等价，均返回评审记录分页。支持 `project_id` / `status` / `mr_iid` 过滤。

```bash
curl "http://localhost:8000/api/reviews/records?project_id=uuid&status=done&mr_iid=7&limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/reviews/records

创建一条评审记录（内部建表/测试 seeding，非触发评审）。

```json
// 请求体
{
  "project_id": "uuid",
  "mr_iid": "7",
  "source_branch": "feature/demo",
  "target_branch": "master",
  "commit_sha": "abc123",
  "status": "done",
  "engine_used": "llm-direct",
  "provider_used": "ark",
  "policy_applied": null,
  "has_blocker": false,
  "finding_count": 0,
  "duration_ms": 1200,
  "raw_llm_output": null
}
```

`status` 取值：`pending` / `running` / `done` / `failed`。

### GET /api/reviews/{id} / PATCH /api/reviews/{id} / DELETE /api/reviews/{id}

按评审 UUID 查询 / 修正 / 删除。

## 十、评审发现管理（JWT）

### GET /api/findings

支持 `review_id` / `severity` / `fp_status` / `file_path` 过滤。

```bash
curl "http://localhost:8000/api/findings?review_id=uuid&severity=BLOCKER&fp_status=NONE" \
  -H "Authorization: Bearer $TOKEN"
```

```json
// 200
{
  "items": [
    {
      "id": "uuid",
      "review_id": "uuid",
      "file_path": "app.py",
      "line_number": 42,
      "rule_id": "general.hardcoded-secret",
      "severity": "BLOCKER",
      "title": "Hard-coded secret detected",
      "description": "...",
      "suggestion": "...",
      "existing_code": "password = 'hunter2'",
      "confidence": 0.92,
      "gitlab_discussion_id": "discussion-1",
      "fp_status": "NONE",
      "fp_marked_by": null,
      "fp_marked_at": null,
      "fp_marked_reason": null,
      "fp_reviewed_by": null,
      "fp_reviewed_at": null,
      "fp_review_note": null,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

### POST /api/findings

创建一条 finding（内部 seeding）。

```json
// 请求体
{
  "review_id": "uuid",
  "file_path": "src/app.py",
  "line_number": 42,
  "rule_id": "PY001",
  "severity": "WARNING",
  "title": "Demo issue",
  "description": null,
  "suggestion": null,
  "existing_code": "print('ok')",
  "confidence": 0.0,
  "gitlab_discussion_id": null,
  "fp_status": "NONE"
}
```

`fp_status` 取值：`NONE` / `PENDING` / `CONFIRMED` / `REJECTED`。

### GET /api/findings/{id} / PATCH /api/findings/{id} / DELETE /api/findings/{id}

按 finding UUID 查询 / 修正 / 删除。

## 十一、误报闭环（JWT）

### POST /api/findings/{id}/false-positive

开发者将 finding 标记为误报候选，状态置为 `PENDING`。

```json
// 请求体
{ "marked_by": "dev@example.com", "reason": "Generated file, not real code" }
```

```json
// 200
{ "id": "uuid", "fp_status": "PENDING", "fp_marked_by": "dev@example.com", "fp_marked_at": "...", ... }
```

### GET /api/false-positives/pending

返回 `fp_status=PENDING` 的待评审队列。

### POST /api/false-positives/{id}/confirm

管理员确认误报 → 状态置为 `CONFIRMED`，并自动写入 `negative_examples` 表（供后续评审 prompt 追加负样本）。

```json
// 请求体
{ "reviewed_by": "lead@example.com", "note": "Known safe wrapper" }
```

确认后可通过 `GET /api/negative-examples` 看到新生成的负样本，其 `source_finding_id` 指向原 finding。

### POST /api/false-positives/{id}/reject

管理员驳回误报 → 状态置为 `REJECTED`，保留评审审计字段。请求体同 confirm。

> confirm / reject 要求 finding 当前为 `PENDING`，否则返回 `409 Finding is not pending false-positive review`。

### GET /api/negative-examples

列出已确认的负样本，支持 `rule_id` / `project_id` 过滤。

```json
// 200
{
  "items": [
    {
      "id": "uuid",
      "rule_id": "PY001",
      "project_id": "uuid",
      "code_snippet": "print('ok')",
      "explanation": "Known safe wrapper",
      "source_finding_id": "uuid",
      "approved_by": "lead@example.com",
      "approved_at": "...",
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

## 十二、引擎配置管理（JWT，持久化）

> 与 `/api/engines`（运行时注册表）区分：`/api/engines/configs` 操作的是 `engines` 数据库表。

### POST /api/engines/configs

```json
// 请求体
{ "name": "llm-direct", "engine_type": "builtin", "config": { "max_context_tokens": 128000 }, "enabled": true }
```

```json
// 201
{ "id": "uuid", "name": "llm-direct", "engine_type": "builtin", "config": { "max_context_tokens": 128000 }, "enabled": true, "created_at": "...", "updated_at": "..." }
```

### GET /api/engines/configs

支持 `limit` / `offset` / `sort` / `q`（按 `name` 模糊）/ `enabled`。

### GET /api/engines/configs/{id} / PATCH /api/engines/configs/{id} / DELETE /api/engines/configs/{id}

按 UUID 查询 / 更新（含启用/禁用）/ 删除。

## 十三、Jenkins 同步评审（X-Internal-Token）

### POST /api/reviews

同步触发一次 MR 评审，供 Jenkins Pipeline 调用并据 `has_blocker` 决定是否让 stage 失败。认证用 `X-Internal-Token`（非 JWT）。

```json
// 请求体
{
  "project_id": 123,
  "project_path": "group/demo",
  "mr_iid": 7,
  "target_branch": "master",
  "source_branch": "feature/demo",
  "commit_sha": "abc123",
  "target_commit_sha": "base456",
  "title": "Demo MR",
  "web_url": "https://gitlab.example.com/group/demo/-/merge_requests/7"
}
```

`project_id`、`mr_iid` 为正整数；`target_branch`、`source_branch`、`commit_sha` 非空；`web_url` 必须为合法 http(s) URL（`javascript:` 等不安全 scheme 返回 422）。请求体不允许包含未定义字段（`extra="forbid"`）。

```json
// 200
{
  "review_id": "00000000-0000-0000-0000-000000000123",
  "status": "done",
  "has_blocker": true,
  "finding_count": 1,
  "blocker_count": 1,
  "policy_applied": "master -> BLOCKER",
  "review_url": "https://gitlab.example.com/group/demo/-/merge_requests/7#note_42"
}
```

字段说明：

- `status`：`done`（评审完成）或 `engine_error`（引擎异常，已按策略降级）。
- `has_blocker`：是否命中阻断级问题（决定 Jenkins 是否失败）。
- `policy_applied`：形如 `{branch_pattern} -> {block_severity}`，例如 `master -> BLOCKER`、`* -> NONE`。
- `review_url`：优先指向 GitLab note（`{web_url}#note_{note_id}`），无 note 时回退到 `/api/reviews/{review_id}`。

### GET /api/reviews/recent

返回内存中最近 20 条同步触发评审的摘要（MVP Dashboard 用，非持久化）。

```bash
curl http://localhost:8000/api/reviews/recent -H "X-Internal-Token: $INTERNAL_TOKEN"
```

```json
// 200
[
  {
    "review_id": "uuid",
    "project_id": 123,
    "project_path": "group/demo",
    "mr_iid": 7,
    "title": "Demo MR",
    "web_url": "https://gitlab.example.com/group/demo/-/merge_requests/7",
    "status": "done",
    "has_blocker": true,
    "finding_count": 1,
    "blocker_count": 1,
    "policy_applied": "master -> BLOCKER",
    "review_url": "https://gitlab.example.com/group/demo/-/merge_requests/7#note_42"
  }
]
```

## 十四、GitLab Webhook（X-Gitlab-Token）

### POST /api/webhooks/gitlab

接收 GitLab Merge Request Hook，校验 `X-Gitlab-Token` 后解析并触发评审。返回 `202 Accepted`。

```bash
curl -X POST http://localhost:8000/api/webhooks/gitlab \
  -H "X-Gitlab-Event: Merge Request Hook" \
  -H "X-Gitlab-Token: $GITLAB_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "object_kind": "merge_request",
    "project": { "id": 123, "path_with_namespace": "group/demo" },
    "object_attributes": {
      "iid": 7,
      "action": "open",
      "source_branch": "feature/demo",
      "target_branch": "master",
      "last_commit": { "id": "abc123" },
      "target": { "default_branch": "master" },
      "title": "Demo MR",
      "url": "https://gitlab.example.com/group/demo/-/merge_requests/7"
    }
  }'
```

```json
// 202
{
  "processed": true,
  "reason": null,
  "status": "done",
  "finding_count": 1,
  "has_blocker": true,
  "note_id": 42
}
```

仅处理 `X-Gitlab-Event: Merge Request Hook` 且 `object_kind=merge_request` 的事件；非 MR 事件返回 `processed=false, reason="ignored_event"`。MR `action` 仅 `open` / `reopen` / `update` 被处理，其余返回 `reason="ignored_action"`。

- 密钥不匹配 → `401 Invalid webhook token`。
- payload 缺字段（project / object_attributes / iid / last_commit.id 等）→ `422 Invalid GitLab merge request payload: ...`。

## 十五、阻断策略语义

`has_blocker` 由「目标分支匹配到的阻断策略」与「finding 严重度」共同决定：

- 策略按 `priority` 升序匹配第一个 `branch_pattern`（fnmatch glob）命中的目标分支。
- `block_severity` 为阈值：finding 严重度 ≥ 阈值即计入阻断。
  - `NONE`：永不阻断（只评论）。
  - `INFO` / `WARNING` / `BLOCKER`：对应严重度及以上 finding 触发阻断。
  - `ENGINE_ERROR_ONLY`：仅引擎异常时阻断。
- 引擎异常时：`block_on_engine_error=true` 或 `block_severity=ENGINE_ERROR_ONLY` → `has_blocker=true`，否则不阻断。

默认策略模板（见 `app/core/block_policy.py` / `scripts/seed.py`）：

| priority | branch_pattern | block_severity |
|---|---|---|
| 1 | `master` | `BLOCKER` |
| 2 | `release/*` | `BLOCKER` |
| 3 | `hotfix/*` | `BLOCKER` |
| 99 | `*` | `NONE` |

## 十六、curl 速查

```bash
# 登录拿 JWT
TOKEN=$(curl -fsS -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 创建规则 + 项目
RULE_UUID=$(curl -fsS -X POST http://localhost:8000/api/rules -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"rule_id":"general.hardcoded-secret","title":"硬编码密钥","prompt_snippet":"...","severity_default":"BLOCKER"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -fsS -X POST http://localhost:8000/api/projects -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"demo\",\"gitlab_project_id\":\"group/demo\",\"gitlab_access_token\":\"glp-x\",\"webhook_secret\":\"hs\",\"rules\":[{\"rule_id\":\"$RULE_UUID\",\"enabled\":true}]}"

# Jenkins 同步触发评审
curl -fsS -X POST http://localhost:8000/api/reviews \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d '{"project_id":123,"mr_iid":7,"target_branch":"master","source_branch":"feature/demo","commit_sha":"abc123"}'
```
