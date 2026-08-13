# 任务：按文件分批评审 + 并发处理

## 项目路径
/home/ubuntu/ai-code-reviewer

## 分支
feat/per-file-concurrent-review（已创建，基于 fix/rules-injection-and-prompt-truncation）

## 完整 Spec
docs/specs/2026-08-13-per-file-concurrent-review.md

## 你要做的事

按照 spec 实现按文件粒度的并发 LLM 审查。核心改动涉及 4 个文件：

1. **backend/core/config.py** — 新增 llm_concurrency 和 llm_file_max_chars 配置项
2. **backend/engines/types.py** — 新增 SkippedFile 模型（已经有 path_patterns 字段的改动，保留）
3. **backend/engines/llm_engine/engine.py** — 核心改造：
   - review() 从串行 batch 改成 asyncio.gather 并发单文件
   - 新增 _review_single_file() 方法
   - 单文件超 llm_file_max_chars → 跳过（too_large）
   - 单文件 LLM 失败 / 解析失败 → 跳过（review_failed），不抛异常
   - self.skipped_files 属性存跳过文件列表
4. **backend/core/summary_builder.py** — build_review_summary_note 增加 skipped_files 参数，渲染审查覆盖说明段落
5. **backend/services/review_orchestrator.py** — 从 engine 读取 skipped_files，传给 summary builder

## 测试

在 backend/tests/ 下新增/更新测试：

1. 更新 test_llm_direct_engine.py — 并发审查的单测（正常/跳过/失败降级/并发度控制/空 diff）
2. 更新 test_summary_builder.py — 跳过文件清单渲染测试
3. 错误路径测试：LLM 超时、返回垃圾 JSON、超大文件跳过

## 关键约束

- 保持 async，用 asyncio.Semaphore 控并发
- 不修改 ReviewEngine 基类契约（review() 仍返回 list[Finding]）
- skipped_files 通过引擎实例属性回传
- Filter 阶段逻辑不变
- 所有现有测试必须通过
- 代码风格跟随项目（看现有代码学风格）
- 有完整 docstring 和日志

## 提交

完成后提交到 feat/per-file-concurrent-review 分支，commit message 用 conventional commit 格式。
