# 注释规范

> 目标读者：要给本项目提 PR、或基于它做二次开发的人。
> 一句话原则：**docstring 写契约，行内注释写"为什么"**。代码能说明"做了什么"，
> 注释只该解释"为什么这么做、不能怎么做、哪里容易踩坑"。

## 1. 三层注释各管什么

| 层级 | 位置 | 必须回答 |
|---|---|---|
| 模块 docstring | 文件头 | 这个模块负责哪一块？不负责什么？与相邻模块的边界 |
| 函数 docstring | `def` 下第一行 | 契约：参数含义、返回值、副作用（写库/发请求）、失败语义（抛错还是降级） |
| 行内注释 | 函数体内 | **为什么**这样写：业务规则、顺序/并发约束、外部协议怪癖、兜底决策、魔法数字 |

反面例子不是"注释太少"，而是"三层都在重复同一句话"：

```python
# ❌ 三层信息量相同
def compute_has_blocker(...):
    """计算是否阻断。"""
    threshold = _parse_block_severity(policy.block_severity)  # 解析阈值
```

## 2. 必须写行内注释的六类位置

### 2.1 业务规则与阈值

读者看代码只能看到 `>=`，看不到"为什么是 `>=`"。

```python
# ✅ 说明语义方向，而不是复述比较
# "≥ 阈值" 语义：阈值 INFO 时任何 finding 都阻断；阈值 BLOCKER 时只有 BLOCKER 阻断。
blocker_count = sum(1 for f in findings if _finding_rank(f.severity) >= threshold_rank)
```

### 2.2 顺序与并发约束

这类约束一旦被"顺手重排"就会变成线上事故，必须写在动作旁边。

```python
# ✅ 解释为什么必须串行、为什么返回值要和 findings 同序
# 返回值必须与 findings 同序同长：调用方用 zip 落库，错位会把 discussion id
# 写到别的 finding 上（下一轮就会 resolve 掉错误的评论）。
discussion_ids: list[str | None] = []
```

```python
# ✅ 单例上的状态是禁区，写清楚原因与替代方案
# 引擎实例是进程级单例，per-review 状态必须放局部变量或 ctx 回填字段，
# 否则并发 review 会互相清空（历史事故：去重缓存跨 review 污染）。
```

### 2.3 外部系统的怪癖

`httpx` / GitLab / Anthropic 的行为不会写在我们的类型里，只能靠注释传下去。

```python
# ✅ GitLab：position 三件套必须同源，new_line 必须是 post-image 行号
# 随遍拼 SHA 会被判 "Note position is invalid"；new_line 必须落在 hunk 新增行
# 范围内，这就是行号漂移最后要在 orchestrator 侧 re-locate 的原因。
```

```python
# ✅ Anthropic：tool_result 不能拆成多条 user 消息
# 连续 tool_result 必须合并进同一条 user 消息，否则 400（unexpected role sequence）。
```

### 2.4 兜底与降级决策（fail-open / fail-fast）

"吞异常"是设计选择，不写清楚就会被后人当成 bug 修掉。

```python
# ✅ 明确这是 fail-open，并说明后果可接受
except Exception:  # noqa: BLE001 - fail-open：过滤阶段失败不能拖累主流程
    logger.warning("filter stage: LLM call failed, keeping original findings: %s", exc)
    return findings
```

```python
# ✅ 反过来，fail-fast 也要说明为什么不能吞
# 未知 severity 直接抛错而不是当 INFO：engine 吐出拼错的值时，
# 静默降级会让本该阻断的 finding 悄悄放行。
```

### 2.5 魔法数字与超时

```python
# ✅ 数字 + 约束 + 谁来调
# 指数退避上限 1s：审查是交互式场景，等太久不如尽快把错误暴露给编排层。
await asyncio.sleep(min(0.1 * (2**attempt), 1.0))
```

### 2.6 兼容层与临时方案

必须带**移除条件**，否则永远没人敢删。

```python
# ✅ 写清为什么存在、什么条件下能删
# 历史数据没记 discussion_id（创建时失败），这里跳过；等老数据自然过期后可删。
```

## 3. 不要写的四类注释

| 反模式 | 例子 | 替代做法 |
|---|---|---|
| 复述代码 | `# 把 line_number 赋给变量` | 删掉 |
| 写变更史 | `# PR2 拆分时加的` / `# feat/xxx 引入` | 交给 `git log` / `CHANGELOG.md` |
| 大段设计推演 | 函数里 30 行"为什么选这个架构" | 移到 `docs/ARCHITECTURE.md` / `docs/orchestration.md` |
| 注释掉的代码 | `# await old_call()` | 直接删，需要时从 git 历史取 |

另外两条硬要求：

- **注释必须与代码同步**。改逻辑时先看注释是否还成立；注释比代码旧，比没有注释更糟。
- **不要注释"显然"。** 覆盖率不是 KPI，`comment_audit.py` 指出的函数该先问"这段真的需要解释吗"。

## 4. 写法约定

- 语言：正文中文，术语保留英文（`fail-open`、`hunk`、`post-image`、`discussion`）。
- 格式：`#` 后一个空格；行尾注释只用于特别短的补充（`except Exception:  # noqa: BLE001 - 原因`）。
- 一个注释块讲一件事；超过 4 行且涉及多步推导的，考虑抽函数或写进 docs。
- 面向"下一个要改这里的人"写，而不是面向评审者写。

## 5. 自检

```bash
cd backend
python scripts/comment_audit.py            # 列出 ≥30 行且无行内注释的函数
python scripts/comment_audit.py --min-lines 20 --top 30
```

输出只是**提示**，不是门禁：命中后先判断该函数是否真的需要解释（纯渲染 / 纯字段映射可以不管）。

PR 提交前建议自查：

- [ ] 新增/修改的分支判断、`try/except`、并发/顺序相关代码，都有"为什么"
- [ ] 新增的魔法数字有来源或约束说明
- [ ] 降级路径写明"失败后会发生什么"
- [ ] docstring 描述的是**当前**行为（不是曾经的）
- [ ] 没有注释掉的代码、没有过时的分支名/PR 号

## 6. 参考样例（本仓库内）

| 文件 | 值得看的注释 |
|---|---|
| `backend/core/block_policy.py` | 阈值语义、fnmatchcase 的选择、为什么未知 severity 要抛错 |
| `backend/integrations/gitlab/client.py` | position 三件套、数组响应包 `{"data": ...}`、per_page 上限 |
| `backend/llm/base.py` | 重试策略、Anthropic system 顶层化、tool_result 合并 |
| `backend/engines/llm_agent/engine.py` | `_RunState` 与单例无状态契约、预算/收口逻辑 |
| `backend/services/review_orchestration/persistence.py` | 增量"按文件换代"的合并语义 |
| `backend/api/gitlab_webhook.py` | 为什么用 422、为什么后台任务 + 202 |
