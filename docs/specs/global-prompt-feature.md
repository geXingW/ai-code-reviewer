# 全局提示词维护功能 实现 Spec

## 背景
当前 LLM 引擎的 system prompt 完全硬编码在 `backend/engines/llm_engine/prompts/system.md` 中，管理员无法通过界面自定义全局审查原则。
需要增加「全局提示词」功能，管理员可在界面上编辑一段文本，该文本会被注入到每次 LLM 审查的 system prompt 中。

## 后端实现

### 1. 数据模型：`global_settings` 表
位置：`backend/models/global_setting.py`

使用 key-value 结构，便于未来扩展其他全局配置：
- `key` (VARCHAR(255), PK) — 配置项键名
- `value` (TEXT) — 配置值
- `created_at` / `updated_at` — 时间戳

初始 key: `global_system_prompt`（全局 system prompt 附加文本）

### 2. Alembic 迁移
位置：`backend/alembic/versions/0008_global_settings.py`

创建 `global_settings` 表。

### 3. Repository 层
位置：`backend/repositories/global_setting.py`

- `get_by_key(key: str) -> GlobalSetting | None`
- `upsert(key: str, value: str) -> GlobalSetting`

### 4. API 层
位置：`backend/api/admin.py` 中新增两个 endpoint（或新建 `backend/api/settings.py`）

**新增路由（需 admin 鉴权）：**
- `GET /api/settings/global-prompt` — 返回 `{ "content": "..." }`（不存在时返回空字符串）
- `PUT /api/settings/global-prompt` — 接收 `{ "content": "..." }`，保存后返回同样结构
  - content 最大长度：50000 字符

### 5. LLM 引擎集成
位置：`backend/engines/llm_engine/engine.py`

在 `_build_batch_prompt` 中组装 system prompt 时：
- 从 DB 读取 `global_system_prompt` 的值
- 如果非空，将其作为 system prompt 的**开头部分**（在 system.md 模板内容之前），用分隔线隔开
- 使用缓存（如 `functools.lru_cache` + 定期失效或直接每次读 DB，因为频率不高）
- 注意：这是审查用的主 system prompt，filter 阶段的 prompt 不注入全局提示词

### 6. Schemas
位置：`backend/schemas/` 中新增 `global_setting.py`

```python
class GlobalPromptResponse(BaseModel):
    content: str

class GlobalPromptUpdate(BaseModel):
    content: str = Field(..., max_length=50000)
```

## 前端实现

### 1. 菜单
在 `navItems` 数组中新增：
- key: `globalPrompt`
- label: `全局提示词`
- 放在「审查规则」之前，「模型供应商」之后

图标使用 `ScrollText`（已导入）。

### 2. API 调用（`src/api.ts`）
新增两个函数：
- `fetchGlobalPrompt(): Promise<{ content: string }>` — GET `/api/settings/global-prompt`
- `updateGlobalPrompt(content: string): Promise<{ content: string }>` — PUT `/api/settings/global-prompt`

### 3. 页面
在 `App.tsx` 中新增 `globalPrompt` 页面的渲染逻辑：
- 页面标题：「全局提示词」
- 描述：这段提示词会被注入到每次 LLM 代码审查的 system prompt 开头，用于定义全局审查原则和风格
- 一个大的 Textarea（行高较大，比如 20 行），用于编辑提示词内容
- 底部操作区：「保存」按钮 + 状态提示
- 保存成功后显示成功消息
- 加载中显示骨架/禁用状态
- 页面进入时加载当前内容

页面布局遵循现有页面风格（PageHeader + Card + 表单）。

### 4. 路由
URL path: `/global-prompt`（和 key 对应，现有的 getInitialPage 逻辑需要适配——因为 key 是 `globalPrompt` 但 URL 是 `/global-prompt`，可以把 key 改成 `global-prompt` 保持一致）

## 注意事项
1. 遵循项目现有代码风格和命名约定
2. 后端 API 需要 admin 鉴权（和其他 admin API 一样）
3.  Alembic 迁移需同时兼容 PostgreSQL 和 MySQL
4. 前端样式使用 Tailwind + 现有的 UI 组件（Card, Button, Textarea, Label, PageHeader）
5. 全局提示词为空时，LLM 引擎行为完全不变（不注入任何内容）
6. 错误处理遵循现有模式
7. 不要改到不相关的代码
