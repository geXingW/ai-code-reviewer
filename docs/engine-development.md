# 引擎开发指南

> 目标读者：想新增一个评审引擎（静态分析器、OCR CLI、第三方 Agent 等）的贡献者。
> 契约实现：`backend/engines/base.py`；数据结构：`backend/engines/types.py`。

## 1. 引擎是什么

引擎是**唯一需要实现评审能力的扩展点**：输入一个 `ReviewContext`，输出 `list[Finding]`。
它不知道 GitLab、不知道数据库、不知道阻断策略——那些都在编排层。

```
编排层（services/review_orchestration）
   │  解析 rules / provider / history / repo_reader，构建 ReviewContext
   ↓
引擎（engines/*）  review(ctx) -> list[Finding]（+ 回填 ctx.skipped_files）
   │
   ↓
编排层  写 GitLab discussion / commit status / 落库 / 通知
```

## 2. 必须实现的四个方法

```python
from engines import Finding, HealthStatus, ReviewContext, ReviewEngine
from engines.registry import register_engine


@register_engine
class MyEngine(ReviewEngine):
    _NAME = "my-engine"          # 稳定标识，落库到 reviews.engine_used

    def name(self) -> str:
        return self._NAME

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        ...

    def supports_feedback(self) -> bool:
        """是否消费 ctx.history（已确认的误报样本）。"""

    async def health_check(self) -> HealthStatus:
        """轻量自检，不 ping 上游、不抛异常。"""
```

| 方法 | 要求 |
|---|---|
| `name()` | 小写 kebab-case，**上线后不可改名**（改名等于换引擎，历史数据会失联） |
| `review(ctx)` | async；返回的 `file_path` / `line_number` 必须指向 diff 的**新侧**行号 |
| `supports_feedback()` | 返回 `False` 时编排层不会注入 `ctx.history` |
| `health_check()` | < 2s；失败用 `HealthStatus(status="error", message=...)` 表达，不要 raise |
| `requires_repo_clone()` | 可选，默认 `False`；需要完整克隆的引擎（如本地静态分析）覆写为 `True` |

## 3. 三条硬性契约

### 3.1 必须无状态

引擎实例在 `EngineRegistry` 里是**进程级单例**，会被并发 review 共享。
任何 per-review 数据只能放在：

1. 方法内的局部变量；
2. `ReviewContext` 上的回填字段（目前只有 `ctx.skipped_files`）。

```python
# ❌ 错误：并发 review 互相覆盖
self.cache[key] = value

# ✅ 正确：review 局部
def review(self, ctx):
    cache: dict[str, str] = {}
```

历史事故：agent 引擎的观察去重缓存曾挂在 `self` 上，两个并发 review 会互相清空/污染，
导致基于错误版本的代码下结论。

### 3.2 不碰数据库 / 不发外部请求取配置

引擎只能使用 `ctx` 提供的输入：`provider`（已解密的供应商配置）、`rules`、`history`、
`repo_reader`（只读仓库访问器）。不得 `from core.db import ...`，也不得自己去查 `global_settings`
——全局提示词、项目级负样本提示词由编排层解析后注入。

### 3.3 失败语义

- **可降级的失败**（单文件超时、单文件解析失败、diff 过大）→ 记进 `ctx.skipped_files`，
  继续审其余文件，不抛异常。
- **不可恢复的失败**（Provider 缺失且无法降级、上游整体不可用）→ 抛异常，
  编排层会写 `status='engine_error'` 的 review 行 + 失败评论（commit 链路 status 恒 failed）。

```python
ctx.skipped_files.append(
    SkippedFile(file_path=hunk.file_path, reason="too_large", detail=f"{len(hunk.diff)} chars")
)
```

`reason` 取值：`too_large` / `review_failed` / `filtered_out`（预留）。

## 4. 注册与启用

1. 在 `backend/engines/<your_engine>/` 下实现，用 `@register_engine` 装饰类。
2. 在 `engines/load_builtin_engines()` 里 import 该模块（应用启动时调用一次）。
3. 管理后台「引擎配置」里能看到它；在「GitLab 项目」里按项目选择引擎
   （`projects.engine_id → engines.name`，解析失败回退全局 `default_review_engine`）。

## 5. 测试怎么写得快

引擎测试不需要数据库、不需要 GitLab：直接构造 `ReviewContext` + 注入假的 LLM 客户端。

```python
engine = MyEngine(client=_FakeClient(responses=['{"findings": []}']))
ctx = ReviewContext(
    review_id=uuid4(), project_id=uuid4(), mr_iid="1",
    source_branch="feat/x", target_branch="master",
    source_commit_sha="b" * 40, target_commit_sha="a" * 40,
    diff_hunks=[
        DiffHunk(
            file_path="app/a.py",
            new_start=1, new_lines=1, old_start=1, old_lines=1,
            content="@@ -1 +1 @@\n-x\n+y",
        )
    ],
    provider=ProviderConfig(
        provider_id=uuid4(),
        provider_type="openai_compatible",
        base_url="http://localhost:8000",
        model="test-model",
        api_key="test-key",
    ),
)
findings = await engine.review(ctx)
assert ctx.skipped_files == []      # 引擎回填的跳过文件
```

参考实现：`backend/tests/test_llm_direct_engine.py`、`backend/tests/test_llm_agent_engine.py`。

## 6. 提交前自检清单

- [ ] `name()` 唯一且稳定；`@register_engine` 已加；`load_builtin_engines()` 已 import
- [ ] 无 `self.xxx = ...` 形式的 per-review 状态
- [ ] 无数据库 / 全局配置读取
- [ ] 单文件失败降级到 `ctx.skipped_files`，整体失败才抛异常
- [ ] `health_check()` 不抛异常
- [ ] 有覆盖「正常 / 降级 / 整体失败」三条路径的测试
- [ ] 在 `docs/ARCHITECTURE.md` 的引擎表格与 `README.md` 特性列表里登记
