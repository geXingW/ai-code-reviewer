# AI Code Reviewer - Bug Fix Spec

**项目路径**: /home/ubuntu/ai-code-reviewer
**目的**: 投产前修复两个关键正确性问题

---

## 问题 1：审查时提示词中没有项目选中的规则

### 根因
1A. 新增项目时前端始终传 `rules: []`，导致后端「安全默认」策略失效（`payload.rules is None` 才自动关联 BLOCKER 规则，但前端永远传空列表）
1B. `ProjectRepository.get_by_gitlab_project_id()` 没有显式 `selectinload`，依赖隐式 lazy load，在某些 async 配置下可能失败并被静默吞掉

### 修复要求

#### 1A. 前端：新增项目时不传 rules 字段

**文件**: `frontend/src/api.ts`
- `createProject` 函数中，`body.rules` 只在 `payload.rules` 非空时才设置。如果 `payload.rules` 为空数组，不添加到 body，让后端走默认策略。

**文件**: `frontend/src/components/dialogs/ProjectDialog.tsx`
- `initialEmptyForm.rules` 从 `[]` 保持不变（UI 层仍需要数组来管理选中状态）
- `handleSubmit` 中新增模式下，如果 `form.rules` 为空数组，从 payload 中删除 rules 字段（类似密钥字段的处理方式）

验证方式：新增项目时不传 rules → 后端自动关联 BLOCKER 规则 → 编辑项目时能看到已选中的 BLOCKER 规则。

#### 1B. 后端：get_by_gitlab_project_id 显式 eager load

**文件**: `backend/repositories/project.py`
- 修改 `get_by_gitlab_project_id` 方法，加上 `selectinload(Project.project_rules).selectinload(ProjectRule.rule)`
- 确保 `_resolve_rules` 中访问 `project.project_rules` 和 `link.rule` 时不触发隐式 lazy load

**文件**: `backend/services/review_orchestrator.py`
- `_resolve_rules` 方法：不依赖隐式加载，直接用 repo 方法返回的已加载数据
- 异常日志增强：SQLAlchemyError 和其他 Exception 都打 `logger.exception`，但要明确区分错误类型（现在已经是 exception 级别，无需改级别，只需确保错误原因能在日志里看清）

---

## 问题 2：修改太多时报错提示词太长

### 根因
2A. Filter 阶段的 prompt 完全没有长度保护，直接塞完整 diff。大 MR 时主审查阶段截断了但 filter 阶段还是完整 diff，直接撞 context window。
2B. 截断阈值用字符计数不准确（中文 1 token ≈ 1.5-2 chars），实际 token 数可能超预期。

### 修复要求

#### 2A. Filter 阶段加上 diff 截断

**文件**: `backend/engines/llm_engine/engine.py`
- 在 `_filter_findings` 方法中，对 `diff_block` 做截断处理，复用 `_maybe_truncate_diff` 的模式
- 提取一个通用的 `_truncate_diff_to_budget` 辅助方法（或直接复用 `_maybe_truncate_diff` 的逻辑），让主审查和 filter 阶段共用
- filter 阶段用同样的 `llm_prompt_max_chars` 配置做上限保护
- filter 阶段如果 diff 被截断，打 WARNING 日志

实现思路：
- 把 `_maybe_truncate_diff` 的思路抽成通用方法，但 filter 模板不同（占位符不同），所以可以：
  - 方案一：给 `_filter_findings` 也加一套类似的截断逻辑（先算固定开销，再算 diff 预算）
  - 方案二（推荐）：把截断 diff 的策略独立出来，输入（模板、diff_block、values_dict、max_chars）→ 输出（truncated_diff, was_truncated）

#### 2B. 用 token 计数替代字符计数（可选改进，不强制）

**如果时间充裕**，把 `llm_prompt_max_chars` 改成基于 token 的预算，使用已有的 `llm.base.count_tokens` 函数。

注意：这是改进项不是修复项，如果改动大就先只做 2A。优先保证 filter 阶段有截断保护。

---

## 测试要求

### 问题 1 测试
- 验证前端新增项目不传 rules 时，后端自动关联默认 BLOCKER 规则（后端单测：`test_create_project_default_rules_when_rules_omitted`）
- 验证 `get_by_gitlab_project_id` 返回的 project 能直接访问 `project_rules` 和 `rule`（后端单测：验证 eager load，可在现有 test_orchestrator_rules_injection 基础上增加断言）

### 问题 2 测试
- 新增测试：filter 阶段超大 diff 时 prompt 被截断到 max_chars 以内（在 test_llm_filter_stage.py 或新增 test_llm_prompt_truncation_filter.py）
- 验证截断标记出现在 filter prompt 中
- 验证截断后 filter 仍然 fail-open（不影响主流程结果）

---

## 注意事项

1. **前端改动保持向后兼容**：编辑模式仍然正常传 rules，只改新增模式
2. **后端 API 行为不变**：`rules=null` → 默认，`rules=[]` → 清空，`rules=[...]` → 设置。这是已约定的语义。
3. **不要引入 breaking change**
4. **所有改动加注释**
5. **先跑现有测试确保基线通过**（可能需要数据库，跳过也可以，至少保证 lint 通过）
6. **filter 阶段截断后仍然 fail-open**——如果截断导致 filter 无法正常工作，宁可不过滤也不能影响主审查结果
