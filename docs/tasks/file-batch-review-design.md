# 按文件分批评审方案

## 背景
当前 `LLMDirectEngine` 把所有文件的 diff 拼在一个 prompt 里，超过 `llm_prompt_max_chars` 就硬截断尾部——导致后面的文件完全漏审。

对标 alibaba/open-code-review 的「按文件并发评审」模式，改为按文件分批调用 LLM，每批控制在 token/字符预算内，最后合并结果。

## 设计

### 1. 批次划分策略

**目标**：在不超过 `llm_prompt_max_chars` 的前提下，尽可能多放文件。

**算法**：贪心装箱（first-fit）
1. 按文件顺序逐个放入当前批次
2. 放入前估算「加上这个文件后 prompt 总长度」= 固定开销 + 当前批次已有 diff + 当前文件 diff
3. 超过预算 → 关闭当前批次，开新批次，当前文件放入新批次
4. 单个文件就超过预算 → 单文件单独一批，内部走原有的 diff 截断逻辑

**固定开销组成**：MR 上下文 + rules + checklist + history + 输出格式说明（system prompt 另算）

### 2. 每批 prompt 构建

每批的 prompt 结构：
- 共用部分：MR 上下文 + rules + checklist + history（和现在一样）
- diff 部分：只包含本批次的文件
- 额外信息：`本次为第 X 批，共 Y 批，仅审查以下文件：file1, file2...`

目的：让 LLM 知道自己在分批审查，不会因为只看到部分 diff 而困惑。

### 3. 结果合并

每批的 findings 收集后直接 append，保持文件顺序。
- finding 的 file_path 由 LLM 返回，不需要做额外映射
- 同一文件出现在两批的可能性为 0（每个文件只在一批里）

### 4. 错误处理

- **单批失败**：fail-error，把该批标记为错误，但不影响其他批次的结果
  - 不对，用户要求的是 LLM 挂了 = 审查失败
  - 正确策略：**任何一批 LLM 调用失败 → 整个 review 失败**，抛出 `LLMError`，让 orchestrator 走 engine_error 分支
  - 理由：部分文件没审到 = 审查不完整，不能冒充 PASSED
- **单文件超预算**：该文件单独一批，内部截断（保留原有截断逻辑），不会导致整批失败

### 5. Filter 阶段

Filter 阶段也改为按文件分批，和主审查阶段对齐。
- 每批包含：本批次的 diff + 本批次的 candidate findings
- 同样的贪心装箱策略

### 6. 规则按文件路径过滤（同步优化）

Rule 模型已有 `path_patterns` 字段，但目前 prompt 里全量注入所有规则。

优化：每批 prompt 里的 rules 只包含与本批次文件路径匹配的规则。
- `path_patterns` 为空的规则 → 通用规则，始终注入
- `path_patterns` 非空的规则 → 只要本批次有任意一个文件匹配某个 pattern，就注入
- 用 fnmatch 做 glob 匹配

### 7. 配置项

新增配置（可选，先用现有配置，不够再加）：
- `llm_prompt_max_chars` — 复用现有配置，作为每批的字符预算
- 暂不新增配置，后续需要再加 `llm_review_batch_size`（每批最多文件数）、`llm_max_batches`（最多批数）等

### 8. 日志

- 每批开始前打 INFO 日志：`batch X/Y, files: [file1, file2], diff_chars: N`
- 批次数量超过阈值（比如 >10）时打 WARNING，提示 MR 过大
- 单文件截断时的 WARNING 日志保留

## 改动范围

- `engines/llm_engine/engine.py` — 新增分批逻辑，改造 `_build_prompt` 和 `review`
- `engines/llm_engine/filter_stage.py` — filter 阶段分批
- 新增测试文件或在现有测试中加用例
