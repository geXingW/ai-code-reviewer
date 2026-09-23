"""MR 生命周期动作（close / merge / reopen）与 reuse 短路路径子包。

PR3 of the orchestration split：把散在 persistence.py / planning.py 的
"不跑 engine 的短路路径"集中到这里 ——
  - close / merge / reopen 以 Command 模式封装（:class:`LifecycleAction` 协议 +
    ``_LIFECYCLE_ACTIONS`` 注册表），orchestrator 查表分发，不再写 if 链；
  - reuse（head 未变的 CI 重跑，原 planning.py ``_handle_reuse``）同样不跑
    engine，语义同族，一并搬入。

公共 API 重导出；orchestrator 只 import :func:`get_lifecycle_action` 与
:func:`_handle_reuse`，不碰注册表字典。
"""

from services.review_orchestration.lifecycle.actions import (
    _LIFECYCLE_ACTIONS,
    LifecycleAction,
    MrClosedAction,
    MrMergedAction,
    MrReopenedAction,
    get_lifecycle_action,
)
from services.review_orchestration.lifecycle.event import _handle_lifecycle_event
from services.review_orchestration.lifecycle.reuse import _handle_reuse

__all__ = [
    "LifecycleAction",
    "MrClosedAction",
    "MrMergedAction",
    "MrReopenedAction",
    "_LIFECYCLE_ACTIONS",
    "_handle_lifecycle_event",
    "_handle_reuse",
    "get_lifecycle_action",
]
