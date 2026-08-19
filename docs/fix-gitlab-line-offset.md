# Bug 修复：GitLab MR 评论代码行显示偏差

## 根因分析

**当一个文件有多个 diff hunk 时，行号计算全部错误。**

### 问题链路

1. `_build_diff_hunks()` — 一个 GitLab change（文件）只生成一个 `DiffHunk` 对象，
   `new_start`/`new_lines`/`old_start`/`old_lines` 只取第一个 `@@` hunk header 的值，
   但 `content` 里包含了所有 hunk 的 diff 文本。

2. `_iter_added_lines(hunk)` — 从 `hunk.new_start` 开始递增行号，
   遇到第二个及以后的 `@@` 行只是 `continue` 跳过，**不重置行号计数器**。
   导致第二个 hunk 起的所有 added line 行号都偏小（偏差 = 第二个 hunk 真实起始行 - 错误计算出的行号）。

3. `_format_diff()` — 给 LLM 的 prompt 中，每个文件只标了第一个 hunk 的 `new_start`，
   但 diff 内容里有多个 hunk。LLM 数第二个 hunk 及之后的行号时也会算错。

4. `_line_in_diff()` / `_resolve_line_number()` — 依赖 `_iter_added_lines()`，
   所以也连带错误。

5. 最终结果：LLM 返回的 `line_number` 如果在第二个及以后 hunk 里，
   要么被判无效（降级成全局评论），要么恰好落在第一个 hunk 范围内 → **评论贴到完全错误的行上**。

### 复现条件

- MR 中某个文件的改动有 2 个及以上 hunk（中间隔了未改动的代码段）
- 审查 finding 落在第二个及以后的 hunk 内

### 验证方法

用一个有两个 hunk 的 diff，调用 `_iter_added_lines()` 看返回的行号是否正确。

---

## 修复方案

**保持 `DiffHunk` 语义不变（一个 DiffHunk = 一个文件的全部 diff），
修复行号计算相关函数，让它们正确处理多 hunk 内容。**

选择此方案的原因：改动最小，不影响按文件粒度并发的架构设计，风险低。

---

## 需要修改的文件

### 1. `backend/engines/llm_engine/engine.py`

#### 1.1 修复 `_iter_added_lines(hunk: DiffHunk)`

**当前问题**：遇到 `@@` 行直接 `continue`，不重置行号。

**修复**：遇到 `@@ ... @@` 行时，解析出 `new_start`，把 `current_new_line` 重置为该值。

```python
# 伪代码
for raw_line in hunk.content.splitlines():
    if raw_line.startswith("@@"):
        m = _DIFF_HEADER_RE.match(raw_line)
        if m:
            current_new_line = int(m.group("new_start"))
        continue
    # ... 原有逻辑不变
```

注意：需要导入或复用 `_DIFF_HEADER_RE` 正则。如果 `engine.py` 里没有，就定义一个（和 orchestrator 里的一样）。

#### 1.2 修复 `_format_diff(diff_hunks: list[DiffHunk])`

**当前问题**：每个文件只输出一个块，`new_start` 用的是 `hunk.new_start`（第一个 hunk 的），
但 `hunk.content` 里可能有多个真实 hunk。LLM 看到的行号标注不准确。

**修复**：解析每个 `hunk.content` 中的所有真实 hunk，按文件分组输出，
每个真实 hunk 单独一个子块，带正确的 `new_start` / `new_lines`。

输出格式调整为：

```
### File: src/app/main.py

#### Hunk: new_start=42, new_lines=15
```diff
@@ -40,12 +42,15 @@
 ...
```

#### Hunk: new_start=120, new_lines=8
```diff
@@ -118,6 +120,8 @@
 ...
```
```

即：文件级标题不变，文件内按真实 hunk 拆分，每个 hunk 有自己的 new_start 标注。

这样 LLM 看到的每个 hunk 都有正确的起始行号，数行号就不会错了。

### 2. `backend/services/review_orchestrator.py`

#### 2.1 验证 `_is_line_number_valid_for_current_diff()`

该函数已使用 `_DIFF_HEADER_RE.finditer(diff)` 遍历所有 hunk，逻辑是正确的。
但需要确认：**测试用例中没有多 hunk 场景**，需要补充测试。

无需修改代码，只需补测试。

### 3. 测试文件

#### 3.1 `backend/tests/test_diff_utils.py`

增加测试用例：

- `test_multi_hunk_line_in_second_hunk_returns_true` — 行号在第二个 hunk 内，应返回 True
- `test_multi_hunk_line_between_hunks_returns_false` — 行号在两个 hunk 之间（未改动区域），应返回 False
- `test_multi_hunk_line_in_first_hunk_returns_true` — 行号在第一个 hunk 内，应返回 True（确保现有行为不变）

#### 3.2 `backend/tests/test_llm_direct_engine.py`

增加测试用例：

- 构造一个有两个 hunk 的 `DiffHunk`，验证 `_iter_added_lines` 返回正确的行号
- 构造一个有两个 hunk 的 review context，验证 LLM 返回的第二个 hunk 内行号能通过 `_line_in_diff` 校验

#### 3.3 `backend/tests/` 新增 `test_diff_hunk_line_numbers.py`（或放入现有测试文件）

专门测试多 hunk 场景下的行号计算：

- `test_iter_added_lines_single_hunk` — 单 hunk（回归测试）
- `test_iter_added_lines_multi_hunk` — 多 hunk，验证每个 hunk 的 added line 行号正确
- `test_line_in_diff_multi_hunk` — `_line_in_diff` 对多 hunk 的判断
- `test_resolve_line_number_multi_hunk` — `_resolve_line_number` 对多 hunk 的匹配
- `test_format_diff_multi_hunk` — `_format_diff` 输出中每个真实 hunk 都有正确的 new_start 标注

---

## 验收标准

1. 单 hunk 场景行为完全不变（所有现有测试通过）
2. 多 hunk 场景下，`_iter_added_lines` 返回的行号与真实文件行号一致
3. 多 hunk 场景下，`_format_diff` 输出中每个真实 hunk 都有正确的 `new_start` 标注
4. 多 hunk 场景下，`_line_in_diff` 能正确判断第二个及以后 hunk 中的行号
5. `_is_line_number_valid_for_current_diff` 能正确判断第二个及以后 hunk 中的行号
6. 所有现有测试通过，无回归
