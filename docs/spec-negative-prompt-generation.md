# 负样本提示词生成机制 - 实现 Spec

## v2 变更说明（项目级化改造）

本 spec 已从「全局配置」升级为「项目级配置」（v2）。核心变更：

- **存储**：新增 `project_negative_prompts` 表（`project_id` PK + FK -> `projects.id` ondelete CASCADE，一对一），替代 `global_settings` 里的 `negative_prompt` key；全局 key 彻底删除，无全局 fallback。
- **端点**：旧端点 `GET/PUT /api/settings/negative-prompt`、`POST /api/settings/negative-prompt/generate` 删除，替换为 `GET/PUT /api/projects/{project_id}/negative-prompt` 与 `POST /api/projects/{project_id}/negative-prompt/generate`。
- **生成范围**：generate 只取 `NegativeExample.project_id == project_id` 且已批准的样本（不含 project_id 为 NULL 的全局负例），最多 100 条，`approved_at DESC`。
- **引擎注入**：`_load_negative_prompt(project_id)` 改为 per-project 60s TTL 缓存（`dict[UUID, tuple[str, float]]`）；`_resolve_negative_prompt_text(history, project_id)` 用 `ctx.project_id` 取该项目提示词，为空回退结构化 `_format_history`。
- **前端入口**：全局提示词页的负样本卡片移除；项目列表每行新增「负样本提示词」按钮，打开自包含的 `NegativePromptDialog`（查看 / 生成 / 预览编辑 / 保存）。
- **迁移策略**：alembic `0010_project_negative_prompts` 建表后，把旧全局 key 的值非空时下发为每个已存在项目的初始记录（INSERT ... SELECT，跨方言兼容、offline SQL 生成可用），再删除全局 key。

---

## 背景

当前负样本（误报）机制：`negative_examples` 表存已批准误报样本，审查时把结构化 history 列表直接格式化塞进 prompt 的 `history_block`。
目标：优化为"根据**该项目**负样本库生成一段负样本提示词"，审查时只发这段提示词，用户可按项目手动编辑。

## 后端改动

### 1. Model - 新增 `ProjectNegativePrompt`

**文件**：`backend/models/project_negative_prompt.py`

```python
class ProjectNegativePrompt(Base, TimestampMixin):
    """项目级负样本提示词，一对一。"""
    __tablename__ = "project_negative_prompts"

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
```

`Project` model 加反向 relationship `negative_prompt`（`uselist=False`，一对多写法见 `Project.negative_examples`）。

### 2. Repository

**文件**：`backend/repositories/project_negative_prompt.py`

```python
class ProjectNegativePromptRepository:
    async def get_content(self, project_id: UUID) -> str  # 无记录返回 ""
    async def upsert(self, project_id: UUID, content: str) -> None  # 有则 UPDATE 无则 INSERT
    async def count_approved_examples(self, project_id: UUID) -> int  # approved_at IS NOT NULL 且 project_id 匹配
```

**文件**：`backend/repositories/negative_example.py`

`list_all_approved` 加可选参数 `project_id: UUID | None = None`，非 None 时加 `WHERE project_id == project_id`。

### 3. Schemas

**文件**：`backend/schemas/project_negative_prompt.py`（旧 `schemas/global_setting.py` 里的 NegativePrompt* schema 整体迁走）

```python
class ProjectNegativePromptResponse(BaseModel):
    content: str
    example_count: int = Field(description="该项目已批准负样本数量")

class ProjectNegativePromptUpdate(BaseModel):
    content: str = Field(min_length=0, max_length=50000)

class ProjectNegativePromptGenerateRequest(BaseModel):
    provider_id: UUID | None = None

class ProjectNegativePromptGenerateResponse(BaseModel):
    content: str
    source_count: int
```

### 4. API - 3 个项目级端点

**文件**：`backend/api/admin.py`

#### `GET /api/projects/{project_id}/negative-prompt`
- 项目不存在 -> 404
- 返回 `ProjectNegativePromptResponse`：content（未配置为 ""）+ example_count（该项目 approved 负样本数）

#### `PUT /api/projects/{project_id}/negative-prompt`
- 请求体：`{ content: str }`，最大长度 50000
- upsert，返回 `ProjectNegativePromptResponse`（example_count 现算）

#### `POST /api/projects/{project_id}/negative-prompt/generate`
- 只取 `NegativeExample.project_id == project_id` 且 `approved_at IS NOT NULL` 的样本（不含 project_id 为 NULL 的全局负例），按 `approved_at DESC` 排序，最多取 100 条
- 该项目负样本为 0，返回 400 + "该项目负样本库为空，无法生成"
- provider 选择：不传 `provider_id` 按 `created_at ASC` 取第一个 enabled 的，没有则 400 + "无可用的 LLM Provider"
- LLM 调用失败 -> 500
- 不自动保存，只返回生成结果
- 返回 `ProjectNegativePromptGenerateResponse`（content + source_count）

**生成负样本提示词的 System Prompt**（`_NEGATIVE_PROMPT_GENERATE_SYSTEM_PROMPT`，保持不变）：
```
你是专业的代码审查提示词工程师。你的任务是根据已确认的误报样本，生成一段精准、专业的"负样本提示词"。

这段提示词将被注入到代码审查 LLM 的 system prompt 中，用于告诉审查模型"哪些模式是已经确认的误报，不要再报"。

要求：
1. 按规则（rule_id）分组归纳，每组提炼出该规则下的误报模式特征
2. 语言专业、精准，描述"什么场景/什么形态的代码属于误报"而非"某条具体代码是误报"
3. 结构清晰，使用"规则名 + 误报模式描述"的条目化格式
4. 直接输出提示词正文，不要包含解释、开场白、结束语等多余内容
5. 输出中文
```

**User Prompt 格式**：
```
以下是已确认的误报样本列表（按规则分组）：

{按 rule_id 分组的该项目样本，每组包含 code_snippet 和 explanation}

请根据以上样本生成负样本提示词。
```

**样本输入格式**（`_format_negative_examples_by_rule`，保持不变，按 rule_id 分组后）：
```
## 规则：{rule_id}

### 样本 {n}
代码片段：
```
{code_snippet}
```
误报说明：{explanation or "无"}

```

### 5. LLM Engine - 负样本注入读项目级提示词

**文件**：`backend/engines/llm_engine/engine.py`

- `_format_history` 方法保留（供硬过滤用），但 `history_block` 的值不再是结构化格式化
- `_load_negative_prompt(project_id)`（per-project 60s TTL 缓存，`_negative_prompt_cache: dict[UUID, tuple[str, float]]`）
  - 读 `project_negative_prompts` 表 `WHERE project_id = ?`
  - 项目没配置记录时 value = "" 也要缓存（避免每批都查库）
  - DB 故障时 logger.warning + 缓存值兜底，TTL 缩短到 10 秒
  - 有值则用它作为 history_block；为空则回退到旧的 `_format_history` 格式化输出（保证向后兼容）
- `_base_fixed_values` 中：`"history_block": await self._resolve_negative_prompt_text(ctx.history, ctx.project_id)`
- `_matches_false_positive_history` 硬过滤逻辑保持不变（仍用结构化 `ctx.history`）
- `_load_global_prompt`（全局提示词）保持不变

### 6. Alembic 迁移

**文件**：`backend/alembic/versions/0010_project_negative_prompts.py`（基于 head `0009_merge_0008_heads`）

- **upgrade**：
  1. `op.create_table("project_negative_prompts", ...)`：project_id Uuid PK + FK -> projects.id ondelete CASCADE，content Text NOT NULL server_default ''，created_at/updated_at DateTime(timezone=True)
  2. 存量数据迁移：旧全局 key 的值非空时，为每个已存在的项目插一条（`INSERT INTO ... SELECT p.id, g.value, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM projects p CROSS JOIN global_settings g WHERE g."key" = 'negative_prompt' AND g.value <> ''`，纯 SQL 跨方言兼容且 alembic offline 模式可输出）
  3. 删除 `global_settings` 中 key='negative_prompt' 的行
- **downgrade**：`op.drop_table("project_negative_prompts")`（全局 key 不恢复，downgrade 后旧全局配置视为放弃）

## 前端改动

### 1. API 层

**文件**：`frontend/src/api.ts`（删除旧 `fetchNegativePrompt` / `updateNegativePrompt` / `generateNegativePrompt`）

```typescript
export type ProjectNegativePrompt = { content: string; example_count: number };
export type ProjectNegativePromptGenerateResult = { content: string; source_count: number };

export async function fetchProjectNegativePrompt(projectId: string): Promise<ProjectNegativePrompt>
export async function updateProjectNegativePrompt(projectId: string, content: string): Promise<ProjectNegativePrompt>
export async function generateProjectNegativePrompt(projectId: string): Promise<ProjectNegativePromptGenerateResult>
```

### 2. 新组件 `NegativePromptDialog`

**文件**：`frontend/src/components/dialogs/NegativePromptDialog.tsx`

自包含组件（自己管数据加载，不走 App.tsx 全局 state）：

- Props：`open` / `onClose` / `projectId` / `projectName`（subtitle 用）
- `open` 变 true 时 GET 拉当前 content + example_count，loading 态显示"加载中..."
- 布局：标题「负样本提示词」+ 描述（仅作用于该项目的 MR 审查；为空时回退结构化负样本注入）+ textarea（min-h-[240px]）+ 按钮行
- 「生成」按钮：调 generate，loading 中禁用并显示"生成中..."；成功后 content 填入 textarea，显示"基于 {source_count} 条负样本生成"；example_count=0 时按钮禁用，提示"该项目负样本库为空"
- 「保存」按钮：调 update，成功提示"已保存"
- 生成失败 / 保存失败：Dialog 内错误提示
- 生成结果不自动保存，用户编辑后手动「保存」

### 3. 项目列表入口

**文件**：`frontend/src/App.tsx`

- `ProjectCard` 按钮组加「负样本提示词」按钮（ghost 样式，「删除」之后「展开策略」之前），点击打开 `NegativePromptDialog`（Dialog state 放 ProjectCard 内部，`open` 时才渲染）
- 全局提示词页的负样本卡片删除（只保留全局提示词卡片）；相关 state / handler / API 调用一并移除

## 注意事项

1. **向后兼容**：项目负样本提示词为空时，该项目审查仍走旧的结构化 history 注入方式，不影响已有部署
2. **硬过滤保留**：`_matches_false_positive_history` 不变，仍是兜底机制
3. **生成失败处理**：generate API 中 LLM 调用失败返回 500，前端在 Dialog 内显示错误提示
4. **长度限制**：负样本提示词最大 50000 字符，和全局提示词一致
5. **缓存**：引擎层读项目级提示词用 per-project TTL 缓存（60 秒），项目间互不干扰；未配置项目也缓存空值
6. **alembic 迁移**：`0010_project_negative_prompts` 建表 + 旧全局 key 存量下发到每个项目 + 删除全局 key；数据迁移用纯 SQL 保证 SQLite / MySQL / PG 及 offline SQL 生成模式均可用
