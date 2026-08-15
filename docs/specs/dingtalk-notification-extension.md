# 钉钉通知扩展 + 用户映射表 功能 Spec

> 项目：ai-code-reviewer
> 版本：v1
> 状态：待实现

## 一、功能总览

本次改造包含 **3 个独立但关联的功能点**：

| # | 功能 | 说明 |
|---|------|------|
| 1 | MR 链接 | 钉钉通知中增加 GitLab MR 跳转链接 |
| 2 | 审查记录列表 | 钉钉通知正文增加按严重级别分组的 finding 列表（截断） |
| 3 | @MR 创建人 | 通过用户映射表，通知中 @ 对应 MR 创建人的钉钉账号 |

## 二、详细设计

### 2.1 用户映射表（核心新增）

#### 2.1.1 数据库表 `user_mappings`

```
表名：user_mappings
说明：GitLab 用户名 ↔ 钉钉手机号 的映射关系，项目级隔离
```

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | 主键 |
| `project_id` | UUID | FK → projects.id, NOT NULL | 所属项目 |
| `gitlab_username` | VARCHAR(255) | NOT NULL | GitLab 用户名（唯一键(project_id, gitlab_username)） |
| `dingtalk_mobile` | VARCHAR(32) | NOT NULL | 钉钉绑定的手机号，用于 @ 人 |
| `dingtalk_userid` | VARCHAR(128) | NULL | 钉钉 userid（预留，可选） |
| `display_name` | VARCHAR(128) | NULL | 显示名，便于管理员识别 |
| `created_at` | TIMESTAMP | NOT NULL | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL | 更新时间 |

索引：
- `idx_user_mappings_project_id` (project_id)
- `uniq_user_mappings_project_gitlab` UNIQUE (project_id, gitlab_username)

#### 2.1.2 Model / Repository / Schema

遵循项目现有分层：
- **Model**：`backend/models/user_mapping.py` → `UserMapping`
  - 在 `backend/models/__init__.py` 导出
- **Repository**：`backend/repositories/user_mapping_repository.py` → `UserMappingRepository`
  - 继承 `BaseRepository`
  - 方法：`get_by_gitlab_username(project_id, gitlab_username)`、`list_by_project(project_id)`、`create()`、`update()`、`delete()`
- **Schema**：`backend/schemas/user_mapping.py`
  - `UserMappingCreate`（project_id, gitlab_username, dingtalk_mobile, dingtalk_userid?, display_name?）
  - `UserMappingUpdate`（dingtalk_mobile?, dingtalk_userid?, display_name?, gitlab_username?）
  - `UserMappingResponse`（全部字段 + created_at/updated_at）

#### 2.1.3 Admin API

在 `backend/api/admin.py` 中新增路由，JWT 鉴权：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects/{project_id}/user-mappings` | 列出项目下所有映射 |
| POST | `/api/projects/{project_id}/user-mappings` | 新增映射 |
| PUT | `/api/user-mappings/{id}` | 更新映射 |
| DELETE | `/api/user-mappings/{id}` | 删除映射 |

#### 2.1.4 Alembic 迁移

新版本号：`0008_user_mappings.py`
- 新建 `user_mappings` 表
- 唯一约束 (project_id, gitlab_username)

---

### 2.2 MR 作者信息解析

#### 2.2.1 GitLabMergeRequestEvent 扩展

文件：`backend/services/review_orchestrator.py`

在 `GitLabMergeRequestEvent` dataclass 中新增字段：
```python
author_username: str | None   # MR 创建人的 GitLab 用户名
author_name: str | None       # MR 创建人的显示名
```

#### 2.2.2 Webhook 解析补全

文件：`backend/api/gitlab_webhook.py`，函数 `_parse_merge_request_event()`

从 payload 中解析：
- `object_attributes.author_id` → 不直接用，取 `user.username`（触发事件的用户）
- 注意：GitLab webhook 顶层 `user` 对象是**触发事件的用户**，`object_attributes.author_id` 是 **MR 创建人**。两者在 open 事件中通常相同，但 update/merge 事件可能不同。
- **取 MR 创建人**：优先从 `user` 字段取（open 事件），如果没有则留空。

> 注：准确的 MR 作者需要调 GitLab API 或从 `object_attributes.author_id` + user 字段组合。本期简化：直接用顶层 `user.username` 和 `user.name`（open 事件中即为创建人；update 事件中也是触发者，@ 触发者也是合理的通知对象）。

#### 2.2.3 review_data 传递

`review_orchestrator.py` 中 `_push_review_notification()` 调用 `notification_service.send_review_completed()` 时，在 `review_data` dict 中新增：
```python
"mr_author_username": event.author_username,
"mr_author_name": event.author_name,
"mr_web_url": event.web_url,
"findings_summary": [  # 按严重级别分组的精简列表
    {"severity": "BLOCKER", "items": [{"title": "...", "file_path": "...", "line_number": 123}, ...]},
    {"severity": "WARNING", ...},
    {"severity": "INFO", ...},
]
```

---

### 2.3 钉钉客户端扩展 @ 功能

文件：`backend/integrations/dingtalk/client.py`

#### 2.3.1 send_markdown 增加 at 参数

`send_markdown(title, text, at_mobiles=None, at_user_ids=None, is_at_all=False)`

钉钉 markdown 消息的 `at` 字段结构：
```json
{
  "atMobiles": ["13800138000"],
  "atUserIds": ["user123"],
  "isAtAll": false
}
```

同时正文 markdown 中需要在文本末尾追加 `@手机号` 才会真正高亮（钉钉的约定，`atMobiles` 只是指定范围，正文也得 @ 才显示）。

所以发送时要把 @ 人的文本追加到 markdown 末尾，格式：`\n\n@13800138000`

---

### 2.4 通知服务改造

文件：`backend/services/notification_service.py`

#### 2.4.1 review_data 新增字段约定

新增字段（全部可选，向后兼容）：
- `mr_web_url: str | None` — MR 链接
- `mr_author_username: str | None` — MR 创建人 GitLab 用户名
- `mr_author_name: str | None` — MR 创建人显示名
- `findings_summary: list[dict] | None` — 审查记录摘要列表

#### 2.4.2 _build_review_message 改造

新的消息模板结构（markdown）：

```
【AI Code Review】MR !42 审查完成 - 存在阻断

**MR**: 修复用户登录bug
**链接**: [点击查看](https://gitlab.example.com/group/project/-/merge_requests/42)
**结果**: 🔴 阻断 2 个  ·  🟡 警告 5 个  ·  🔵 提示 3 个

---

### 🔴 阻断问题 (2)

1. **SQL 注入风险** — `auth/login.py:45`
2. **硬编码密钥** — `config/database.py:12`

### 🟡 警告问题 (5)

1. **未处理异常** — `services/user.py:88`
2. **魔法数字** — `utils/validator.py:23`
...（最多展示前 5 条 WARNING，超出省略）

### 🔵 提示问题 (3)

...（最多展示前 5 条 INFO，超出省略）

---

**详情**: [点击查看详情](https://review.example.com/reviews/xxx)

@13800138000
```

截断规则：
- BLOCKER：全部展示（通常数量少）
- WARNING：最多 5 条
- INFO：最多 5 条
- 超出部分显示「还有 N 条，详见详情页」
- 总字数控制在钉钉 markdown 限制内（约 15000 字以内，预留安全余量）

#### 2.4.3 @创建人 解析逻辑

在 `send_review_completed()` 中：
1. 从 `review_data` 取 `mr_author_username` 和 `gitlab_project_id`
2. 调 `UserMappingRepository.get_by_gitlab_username(project_id, author_username)` 查映射
3. 找到则取 `dingtalk_mobile` 加入 `at_mobiles` 列表
4. 找不到则不 @，记 debug 日志，不报错

注意：需要把 `UserMappingRepository` 注入到 `NotificationService`（或通过 session_factory 即时创建）。

---

### 2.5 Review Orchestrator 改造

文件：`backend/services/review_orchestrator.py`

#### 2.5.1 _push_review_notification 补充数据

在调用 `notification_service.send_review_completed()` 之前：
1. 从 `event` 取 `web_url`、`author_username`、`author_name`
2. 从审查结果中构造 `findings_summary`（按 severity 分组，取 title + file_path + line_number）
3. 全部塞进 `review_data`

#### 2.5.2 findings_summary 构造逻辑

从 `combined_findings`（或 DB 中的 findings）构造：
```python
findings_summary = []
for sev in ["BLOCKER", "WARNING", "INFO"]:
    items = [
        {"title": f.title, "file_path": f.file_path, "line_number": f.line_number}
        for f in findings if f.severity == sev
    ]
    if items:
        findings_summary.append({"severity": sev, "items": items})
```

---

### 2.6 前端（低优先级，本期可选）

管理后台「项目设置」中增加「用户映射管理」Tab，用于增删改查映射关系。

**本期不做前端 UI**，只提供 API，管理员通过 API 或后续迭代添加。

---

## 三、改动文件清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `backend/models/user_mapping.py` | UserMapping Model |
| `backend/repositories/user_mapping_repository.py` | UserMapping Repository |
| `backend/schemas/user_mapping.py` | UserMapping Pydantic Schema |
| `backend/alembic/versions/0008_user_mappings.py` | 数据库迁移 |

### 修改文件
| 文件 | 改动点 |
|------|--------|
| `backend/models/__init__.py` | 导出 UserMapping |
| `backend/api/gitlab_webhook.py` | 解析 MR 作者信息 |
| `backend/services/review_orchestrator.py` | GitLabMergeRequestEvent 加字段 + _push_review_notification 传 findings_summary |
| `backend/services/notification_service.py` | 消息模板重构 + @人逻辑 + 映射查询 |
| `backend/integrations/dingtalk/client.py` | send_markdown 增加 at 参数 |
| `backend/api/admin.py` | 新增 user-mappings CRUD 路由 |

### 测试文件
| 文件 | 说明 |
|------|------|
| `backend/tests/test_notification_service.py` | （新增或修改）通知服务测试 |
| `backend/tests/test_dingtalk_client.py` | 增加 @ 功能测试 |
| `backend/tests/test_user_mapping.py` | （新增）映射表 API 测试 |

---

## 四、关键设计决策

1. **映射表项目级隔离**：不同项目可能有不同的 GitLab 实例和钉钉群，按项目隔离更安全。
2. **手机号优先于 userid**：钉钉 @ 用手机号更简单通用（`atMobiles`），userid 预留字段。
3. **找不到映射不报错**：fail-silent 原则，只记日志，不阻断通知流程。
4. **finding 列表截断**：避免消息过长被钉钉截断，重要的 BLOCKER 全展示，WARNING/INFO 各 5 条。
5. **正文也追加 @手机号**：钉钉 markdown 消息需要正文中也有 @手机号 才会高亮提醒。

---

## 五、测试要求

### 单元测试
1. `DingTalkClient.send_markdown` 带 at_mobiles 参数时，请求体包含 `at` 字段
2. `_build_review_message` 生成的消息包含 MR 链接、finding 列表、正确的分组
3. `_build_review_message` finding 数量超出限制时正确截断
4. `NotificationService.send_review_completed` 找不到映射时正常发送不 @
5. `UserMappingRepository` CRUD 正常

### 错误路径测试
1. 映射表为空 → 正常通知，不 @ 人
2. MR 无作者信息 → 正常通知，不 @ 人
3. 钉钉 webhook 失败 → fail-silent，不影响主流程
4. finding 列表为空 → 消息中不显示问题列表部分
5. 超长消息（接近 20000 字）→ 正常截断不报错

### 集成测试
1. 完整 webhook 触发 → 审查完成 → 钉钉通知包含 MR 链接 + finding 列表 + @创建人
