# 规则编写指南

> 适用范围：v0.1.0 MVP。本文档说明如何编写可复用的评审规则、如何组织 `prompt_snippet`，以及如何把规则关联到具体项目。
> 规则的存储模型见 `backend/app/models/rule.py`，REST 接口见 [docs/api.md](api.md#七规则库管理jwt)。

## 一、规则是什么

一条 **Rule** 是一段跨项目共享的「评审指令」，最终会被拼进 LLM 评审 prompt 的「Active rules」段。引擎拿到 diff + 启用的规则集后，按规则描述去发现问题，并以结构化 finding 回传。

规则本身**不绑定语言/项目**——它是一个可被任意项目勾选启用的知识单元；项目通过 `project_rules` 关联表决定「启用哪些规则、是否覆盖严重度」。

## 二、规则字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `rule_id` | string | 是 | 跨项目唯一的人类可读键，建议 `<scope>.<topic>` 命名，如 `general.hardcoded-secret`、`python.exception-handling`。一旦发布尽量不要改名（误报负样本按 `rule_id` 关联）。 |
| `title` | string | 是 | 简短一句话标题，会出现在 finding 与 GitLab discussion 中。 |
| `prompt_snippet` | string | 是 | 规则的核心指令文本，拼进 prompt。见下文编写技巧。 |
| `severity_default` | `INFO`/`WARNING`/`BLOCKER` | 否（默认 `WARNING`） | 默认严重度。项目可通过 `severity_override` 覆盖。 |
| `languages` | list | 否 | 适用语言，如 `["python","java"]`；`["*"]` 表示通用。当前为元数据，便于检索。 |
| `path_patterns` | list | 否 | 适用路径 glob，如 `["**/*.py"]`。当前为元数据。 |
| `enabled` | bool | 否（默认 `true`） | 全局启用/禁用。 |
| `grace_period_until` | datetime \| null | 否 | 宽限期：在此之前命中的 finding 可降级处理（预留字段）。 |

`severity` 三档语义：

- `INFO`：提示性，不阻断合并。
- `WARNING`：需关注；是否阻断取决于项目阻断策略阈值。
- `BLOCKER`：严重问题；在 `master`/`release/*` 等阻断分支上会触发合并阻断。

> 严重度只决定「是否阻断」，不决定「是否报告」——只要规则启用且命中，finding 都会被写回 GitLab discussion。

## 三、`prompt_snippet` 编写技巧

`prompt_snippet` 是规则的灵魂。LLM 引擎（`llm-direct`）会把所有启用规则的 `rule_id` / `title` / `severity` / `description`（即 snippet）/ `examples` 拼成 prompt 的第 2 段「Active rules」，再要求模型按固定 JSON 契约回吐 finding。写得好，召回与精度都高；写得差，模型会乱报或漏报。

### 3.1 基本原则

1. **描述「问题模式」而非「泛泛要求」**。`prompt_snippet` 应说明「什么样的代码算违规、为什么违规」，而不是「请检查安全性」这种放之四海皆可的话。
2. **聚焦可判定的信号**：明确触发条件（字符串拼接 SQL、裸 `except:`、硬编码密钥字面量等），让模型有据可依。
3. **避免与输出契约冲突**：引擎已统一要求模型输出 `{"findings":[...]}` JSON，snippet 里**不要**再要求模型「输出 JSON / 按某格式回答」——只描述要找什么。
4. **控制长度**：单条 snippet 建议控制在 1–3 句。所有启用规则会一起进 prompt，过长会挤占 diff 上下文预算。
5. **给出反例/正例**：用一两行代码点明「违规」与「合规」形态，比纯文字描述更有效。

### 3.2 引导结构化结果

引擎的输出契约固定为（见 `LLMDirectEngine._format_output_contract`）：

```json
{
  "findings": [
    {
      "file_path": "string",
      "line_number": "number|null",
      "rule_id": "string",
      "severity": "INFO|WARNING|BLOCKER",
      "title": "string",
      "description": "string|null",
      "suggestion": "string|null",
      "existing_code": "string|null",
      "confidence": "number"
    }
  ]
}
```

为了让模型把 finding 正确归到你的规则上：

- **`rule_id` 必须回填一致**：snippet 里可以点明「`rule_id` 填 `general.hardcoded-secret`」。模型回填的 `rule_id` 用于关联误报负样本，写错会导致负样本失效。
- **`line_number` 指向新增行**：契约要求 `line_number` 指 diff 的新侧（post-merge 行号）。若模型拿不准行号，可让其填 `existing_code`（违规代码片段），引擎会回退定位。
- **`title` 简短且稳定**：误报过滤会按 `title`（大小写不敏感）与 `description` 做去重匹配，标题应稳定描述问题类别，避免每次措辞不同。
- **`suggestion` 给可操作修复**：一句话告诉作者怎么改，会原样写进 GitLab discussion。

### 3.3 反面 vs 正面写法

❌ 差（太泛、无可判定信号）：

```
检查代码安全性，注意潜在风险。
```

✅ 好（明确信号 + 反例 + 修复方向）：

```
检查是否硬编码密钥、密码、Token 等敏感凭据。命中场景包括：
- 字符串字面量形如 API Key / 密码（如 password = 'xxx'、api_key = "sk-..."）。
- 配置内联的数据库连接串含明文密码。
正例：从环境变量或配置中心读取。rule_id 填 general.hardcoded-secret。
```

## 四、示例规则

下面给出 5 条不同语言/不同严重度的示例，可直接用于 `POST /api/rules`。

### 4.1 通用 — 硬编码密钥（BLOCKER）

```json
{
  "rule_id": "general.hardcoded-secret",
  "title": "硬编码密钥/密码",
  "prompt_snippet": "检查是否硬编码密钥、密码、Token 或其他敏感凭据。命中：字符串字面量形如密码/API Key、含明文密码的连接串。正例：从环境变量或配置中心读取。rule_id 填 general.hardcoded-secret。",
  "severity_default": "BLOCKER",
  "languages": ["*"],
  "path_patterns": [],
  "enabled": true
}
```

### 4.2 通用 — SQL 注入（BLOCKER）

```json
{
  "rule_id": "general.sql-injection",
  "title": "SQL 注入风险",
  "prompt_snippet": "检查是否存在 SQL 注入风险，尤其是字符串拼接构造 SQL 的场景（如 \"SELECT * FROM t WHERE id=\" + id）。正例：使用参数化查询 / 预编译语句。rule_id 填 general.sql-injection。",
  "severity_default": "BLOCKER",
  "languages": ["java", "python", "go", "javascript"],
  "path_patterns": [],
  "enabled": true
}
```

### 4.3 Python — 异常处理不当（WARNING）

```json
{
  "rule_id": "python.exception-handling",
  "title": "异常处理不当",
  "prompt_snippet": "检查 Python 异常处理是否过宽、吞掉异常或缺失上下文。命中：裸 except:、except Exception 后仅 pass、异常未记录栈。正例：捕获具体异常类型并记录日志。rule_id 填 python.exception-handling。",
  "severity_default": "WARNING",
  "languages": ["python"],
  "path_patterns": ["**/*.py"],
  "enabled": true
}
```

### 4.4 Java — 潜在 NPE（WARNING）

```json
{
  "rule_id": "java.null-safety",
  "title": "潜在空指针",
  "prompt_snippet": "检查 Java/Kotlin 代码中可能触发空指针的访问路径，如未判空就调用链式方法、方法返回值未校验即解引用。正例：使用 Optional / Objects.requireNonNull / 显式空判断。rule_id 填 java.null-safety。",
  "severity_default": "WARNING",
  "languages": ["java", "kotlin"],
  "path_patterns": ["**/*.java", "**/*.kt"],
  "enabled": true
}
```

### 4.5 前端 — 调试代码残留（INFO）

```json
{
  "rule_id": "js.debug-leftover",
  "title": "调试代码残留",
  "prompt_snippet": "检查是否残留调试代码：console.log/debugger 语句、被注释掉的大段代码块、TODO/FIXME 标记的关键路径。命中后给出清理建议。rule_id 填 js.debug-leftover。",
  "severity_default": "INFO",
  "languages": ["javascript", "typescript"],
  "path_patterns": ["**/*.js", "**/*.ts", "**/*.tsx"],
  "enabled": true
}
```

## 五、把规则关联到项目

规则创建后默认**不会被任何项目使用**——需通过 `project_rules` 关联表启用。创建/更新项目时在 `rules` 字段传入关联：

```json
POST /api/projects
{
  "name": "demo",
  "gitlab_project_id": "group/demo",
  "gitlab_access_token": "glp-x",
  "webhook_secret": "hs",
  "rules": [
    { "rule_id": "<rule-uuid>", "enabled": true, "severity_override": "BLOCKER" },
    { "rule_id": "<rule-uuid-2>", "enabled": true }
  ],
  "block_policies": [
    { "branch_pattern": "master", "block_severity": "BLOCKER", "priority": 1 },
    { "branch_pattern": "*", "block_severity": "NONE", "priority": 99 }
  ]
}
```

关联字段（`ProjectRuleCreate`）：

| 字段 | 说明 |
|---|---|
| `rule_id` | 规则的 **UUID**（`Rule.id`），不是 `rule_id` 字符串。 |
| `enabled` | 在该项目下是否启用。可对某项目单独关闭一条全局规则。 |
| `severity_override` | 可选，覆盖规则的 `severity_default`。例如把全局 `WARNING` 规则在核心项目提到 `BLOCKER`。 |

更新项目时传 `rules` 会**整体替换**该项目的规则关联集合（非增量）。只想增量调整时，应先 `GET /api/projects/{id}` 拿到现有 `rules`，修改后再 `PATCH` 回去。

## 六、规则的生效路径（当前 MVP 边界）

了解规则在评审流程中实际如何流转，有助于定位「规则没生效」的问题：

```
POST /api/reviews (Jenkins) 或 GitLab Webhook
   └─ ReviewOrchestrator
        ├─ 拉取 MR diff（GitLab API）
        ├─ 过滤 diff（ignore_paths / max_diff_bytes / 二进制 / 删除文件）
        ├─ 构建 ReviewContext（含 diff_hunks）
        ├─ 选择引擎（DEFAULT_REVIEW_ENGINE，默认 llm-direct）
        ├─ 引擎 review(ctx) → 产出 Finding[]
        ├─ 按目标分支匹配阻断策略 → 计算 has_blocker
        └─ 写回 GitLab（行级 discussion + 摘要 note + commit status）
```

> ⚠️ **MVP 已知边界**：当前编排器尚未把数据库中的 `project_rules` / `ProviderConfig` 注入到引擎运行时上下文（`ReviewContext.rules` / `ReviewContext.provider`）。因此：
> - `llm-direct` 引擎在未注入 provider 时会安全降级为「不产出 finding」。
> - 规则的 `prompt_snippet` 进入 prompt 的能力取决于引擎实现对 `ctx.rules` 的消费。
>
> 也就是说，规则库与项目关联的 CRUD 已经可用（管理后台可建可改可关联），但「规则真正驱动 LLM 评审」的运行时注入是后续 Issue 的工作。在此期间，可通过引擎健康检查、`/api/reviews` 返回的 `finding_count` / `policy_applied` 验证链路，并用误报闭环（`/api/findings/{id}/false-positive`）积累负样本。

## 七、迭代规则的最佳实践

1. **先小范围试跑**：在测试项目上启用新规则，用一个已知有问题的 MR 验证是否命中。
2. **看 finding，调 snippet**：若漏报，补反例与触发信号；若误报，在 snippet 里加「正例/排除条件」。
3. **用误报闭环沉淀**：开发者标记误报 → 管理员确认 → 自动写入 `negative_examples` → 后续评审自动追加负样本，模型逐步收敛。
4. **严重度分层**：`BLOCKER` 留给真正会阻断合并的高危项；一般问题用 `WARNING`/`INFO`，避免「狼来了」稀释阻断信号。
5. **`rule_id` 保持稳定**：负样本按 `rule_id` 关联，改名等于丢历史。
