# 任务书：Filter 阶段分批 + 测试 + 收尾

**项目路径**: /home/ubuntu/ai-code-reviewer
**当前分支**: fix/rules-injection-and-prompt-truncation
**前置状态**: 主审查阶段的按文件分批逻辑已经写完（`_build_batches`、`_build_batch_prompt`、`_rules_for_files`、`_base_fixed_values` 等方法已在 `LLMDirectEngine` 中），`RuleSpec.path_patterns` 字段已加，`_resolve_rules` 已填充该字段。

---

## 剩余任务

### 任务 1：Filter 阶段改为按文件分批

**文件**: `backend/engines/llm_engine/engine.py`

当前 `_filter_findings` 方法把所有 findings + 完整 diff 放一个 prompt 里，已经加了 diff 截断但还是单批。需要改成按文件分批：

**改造思路**：
1. 按文件把 findings 分组（`finding.file_path` 作为 key）
2. 每批包含：本批文件的 diff + 本批文件对应的 findings
3. 每批独立调一次 filter LLM，得到 decisions
4. 注意：decisions 里的 index 是相对于「该批 candidate findings」的 index，需要转换回原始 findings 列表的全局 index
5. 所有批次的 decisions 合并后，统一调用 `apply_decisions`
6. **fail-open 原则不变**：任一批出错，记录 warning，该批 findings 全部保留（不影响其他批）
7. 每批也有 diff 截断保护（复用 `_truncate_diff`）

**分批策略**：和主审查一样用贪心装箱，但批的单位是「finding+对应文件diff」。简单做法：按 file 分组，每组一个文件一批（因为 filter 阶段 findings 数量通常不多，按文件分就够了，不用再做多文件合并）。这样实现最简单，正确性最高。

**index 映射**：
- 把全局 findings 按文件分组，记录每个 finding 的原始 index
- 每批 filter 调用时，candidate findings 是该文件的子集，decisions 里的 index 是子集内的 index
- 把 decisions 的 index 替换成全局 index，再合并到总 decisions 列表

### 任务 2：新增/更新测试用例

**文件**: `backend/tests/test_llm_filter_stage.py`、`backend/tests/test_llm_prompt_truncation.py`

新增测试：
1. `test_engine_review_splits_into_batches` — 验证多文件 MR 会分成多批调用 LLM（通过 mock client 的 call 次数判断）
2. `test_engine_review_single_batch_when_small` — 小 diff 只分一批（行为与改造前一致）
3. `test_filter_stage_batched_by_file` — filter 阶段按文件分批调用（多文件时多次 filter LLM 调用）
4. `test_rules_filtered_by_path_pattern` — 验证规则按文件路径过滤，不相关规则不出现在该批次 prompt 中
5. `test_batch_finding_index_mapping` — 验证分批后 decisions 的 index 映射正确（不会搞混不同文件的 finding）

**文件**: `backend/tests/test_rules_path_filter.py`（可新建）
- 测试 `_rules_for_files` 的路径匹配逻辑（通用规则始终注入、path_patterns 匹配、不匹配不注入、多 pattern 多文件等）

### 任务 3：验证 & 收尾

1. `cd backend && ruff check .` — 确保 0 issues
2. 跑测试：用 `SECRET_KEY` 合法 Fernet key + `DATABASE_URL=sqlite+aiosqlite:///:memory:`，跑 `tests/test_llm_*` 和 `tests/test_orchestrator_rules_injection.py`，确保全绿
   - 如果 Fernet 环境问题导致 orchestrator 测试挂，跳过即可，只要 engine 测试全过
3. 前端 TypeScript 不用动（这次改动都是后端）
4. 把本次所有改动 commit，commit message：`feat: 按文件分批评审 + 规则路径匹配过滤`
5. push 到 origin 的当前分支

---

## 注意事项

- **Fail-error 原则**：主审查阶段任何一批 LLM 失败 → 整体失败（抛出 LLMError）。Filter 阶段任何一批失败 → fail-open（该批保留，不影响其他批）。这是既有设计，不要改。
- **不要破坏现有 API**：`review(ctx) -> list[Finding]` 签名不变，返回值语义不变（findings 顺序大致按文件出现顺序）
- **单文件超大**：每批只有一个文件时，仍走内部 diff 截断，不报错
- 代码注释要完整
- 如果发现已有测试因重构失败，修复测试而不是绕过
