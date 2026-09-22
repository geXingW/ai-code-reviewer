# Agent 化代码审查改造计划

## 设计决策（已确认）
- **落地形式**：新增 `llm-agent` 引擎，与现有 `llm-direct` 并存（引擎名是稳定 ID，不改动旧引擎）
- **上下文来源**：GitLab API（`requires_repo_clone()` 保持 False，无需 clone）
- **工具协议**：原生 function calling（改造 `llm/base.py` 的 OpenAI 兼容与 Anthropic 适配器；custom 协议不支持时引擎优雅降级）

## 执行循环

```
ReviewContext（diff + rules + history + repo_reader）
  → 系统提示（审查原则 + 工具使用策略）+ 用户提示（diff/规则/MR 上下文）
  → 循环（≤ agent_max_turns 轮）：
      LLM 返回 tool_calls → 执行工具（受限并发、结果截断）→ 以 role="tool" 回填
      LLM 返回最终 JSON findings → 退出
  → 复用现有 filter 阶段证伪（可选）→ list[Finding]
```

## 1. LLM 层支持 function calling（backend/llm/base.py）
- `ChatRole` 扩展 `"tool"`；`ChatMessage` 增加可选 `tool_calls` / `tool_call_id` 字段（向后兼容）
- 新增 `ToolSpec`（function 定义：name/description/parameters）与 `ToolCall`（id/name/arguments）模型
- `LLMProvider.chat(messages, tools: Sequence[ToolSpec] | None = None)`：
    - `OpenAICompatibleProvider`：payload 加 `tools`，解析 `choices[0].message.tool_calls`
    - `AnthropicProvider`：转 `tools` + `tool_use`/`tool_result` content blocks
    - `CustomProvider`：传入 tools 时抛 `NotImplementedError`（fail-fast，供引擎降级判断）
- 不传 tools 时行为完全不变，现有 llm-direct 测试不受影响

## 2. GitLabClient 增加只读能力（backend/integrations/gitlab/client.py）
按现有方法风格新增（含 ValueError 参数校验 + docstring）：
- `get_file_contents(project_id, file_path, ref)` — `/repository/files/:path(raw)`，返回文本
- `list_repository_tree(project_id, path, ref, recursive, page)` — `/repository/tree`
- `get_file_blame(project_id, file_path, ref)` — `/repository/files/:path/blame`
- `search_code(project_id, query)` — `/search?scope=blobs`（CE 可用）

## 3. 仓库读取抽象与注入
- `backend/engines/types.py`：新增 `RepoReader` Protocol（`read_file` / `list_tree` / `blame` / `search` / `commit_history`），`ReviewContext` 增加可选字段 `repo_reader: Any | None = None`（Protocol 仅做结构约束，不 import integrations，保持分层）
- `backend/services/repo_reader.py`：`GitLabRepoReader`，包装 `GitLabClient + gitlab_project_id + ref`，实现上述 Protocol，内部统一做异常→错误字符串、内容截断
- `backend/services/review_orchestrator.py`：3 处 `ReviewContext(...)`（约 L416 / L682 / L865：MR、commit、push 三条链路）注入 `repo_reader=GitLabRepoReader(self._gitlab_client, event.project_id, head_sha)`

## 4. 新引擎包 backend/engines/llm_agent/
- `engine.py` — `LLMAgentEngine`（`@register_engine`，name=`"llm-agent"`）：
    - 复用 `LLMCompletionClient` 注入模式与 `provider` 解析逻辑（对齐 llm-direct 的超时/重试/SkippedFile 语义）
    - Agent 循环护栏：`agent_max_turns` 轮上限、每工具结果截断、总上下文字符预算、单工具超时、非法工具名→错误观察回填
    - provider 为 None / provider 不支持 tools / 循环耗尽未产出 JSON → 降级：warning 日志 + 返回空/尽力 findings，不抛异常拖垮整体（与 llm-direct 的 fail-open 语义一致）
    - `supports_feedback()=True`（消费 history 负例）、`requires_repo_clone()=False`、`health_check()` 不抛
- `tools.py` — 工具注册表：`read_file`、`list_tree`、`get_blame`、`search_code`、`get_file_history`，每个工具含 JSON Schema + `execute()`；输出统一截断到预算
- `prompts/agent_system.md`、`agent_user.md` — 复用 `llm_engine/prompts` 的模板渲染方式与全局提示词注入逻辑；系统提示明确"影响范围调查策略"（先看调用方/被调用方，再下结论）
- `engines/__init__.py` 的 `load_builtin_engines()` 增加 import，自动出现在 `GET /api/engines`
- findings JSON 解析与 llm-direct 共享的规范化逻辑（confidence clamp、行号回填等）抽到 `backend/engines/finding_parsing.py` 供两引擎复用，避免复制

## 5. 配置（backend/core/config.py，沿 Annotated + Field 风格）
- `agent_max_turns: int = 8`（轮数上限）
- `agent_tool_result_max_chars: int = 8000`
- `agent_total_context_max_chars: int = 120000`（整个会话的观察预算）
- `agent_tool_timeout_seconds: float = 15.0`
- `.env.example` 同步补充

## 6. 测试（backend/tests/，沿用 fake client / respx 风格）
- `test_llm_tool_calling.py`：openai-compat 与 anthropic 适配器的 tools 收发、tool_calls 解析、无 tools 回归
- `test_gitlab_client_read.py`：4 个新方法的 respx mock 测试
- `test_llm_agent_engine.py`：fake LLM（先 tool_call 后最终 JSON）、fake RepoReader；覆盖正常循环、轮数耗尽、工具报错、provider 不支持 tools 降级、空 diff
- `test_repo_reader.py` + orchestrator 注入断言（ctx.repo_reader 非空且 ref 正确）
- `test_finding_parsing.py`：抽取后的共享解析回归

## 7. 文档
- `backend/docs/engine_architecture.md`：新增 §"llm-agent" 小节（契约遵循 §2 全部规则）与工具列表
- `docs/ARCHITECTURE.md` 引擎清单补一行；`backend/README.md` 配置项说明

## 测试计划
1. `cd backend && python -m pytest tests/ -x -q`（新增测试 + 现有回归）
2. `ruff check backend` 与 `mypy`（按现有配置）通过
3. 手动验证：`.env` 设 `DEFAULT_REVIEW_ENGINE=llm-agent`，向测试项目提 MR，观察 GitLab 行级评论质量与后端 `llm request`/tool 日志

## 风险与边界
- **成本/延迟上升**：agent 是串行多轮，单次审查 token 消耗约为 llm-direct 的 2-5 倍 → 轮数/预算护栏 + llm-direct 仍为默认引擎，按项目切换
- **custom provider 不可用**：引擎检测到后直接降级为空结果 + warning，不会误报失败
- **GitLab search API 覆盖**：`scope=blobs` 搜索依赖实例索引，不可用时工具返回错误观察，模型可跳过该途径
