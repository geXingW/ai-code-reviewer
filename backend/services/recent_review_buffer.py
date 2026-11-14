"""Process-local fallback buffer for the dashboard "recent reviews" panel.

设计约束（重要）：

* **数据库是唯一事实源**。``GET /api/reviews/recent`` 直接查 ``reviews`` 表；
  本模块只在 DB 查询抛异常时兜底，避免面板整体 500。
* 缓冲区只在 ``POST /api/reviews``（Jenkins 同步审查入口）写入，因此它反映的
  是"本进程最近触发的手动审查"，**不是**全量审查历史。
* 进程内状态：``uvicorn --workers N`` 部署下每个 worker 各有一份，内容可能
  不一致。这是**已知限制**，仅作为降级路径存在，不参与正常数据展示。

之所以单独成模块而不是留在路由里：避免 ``api.admin`` 通过私有符号
（``from api.reviews import _recent_reviews``）跨模块读取路由内部状态。
"""

from __future__ import annotations

from collections import deque

from schemas.review import RecentReviewRead

_MAX_ITEMS = 20

_buffer: deque[RecentReviewRead] = deque(maxlen=_MAX_ITEMS)


def record(item: RecentReviewRead) -> None:
    """把一次审查摘要放进降级缓冲区（最新的在前）。"""

    _buffer.appendleft(item)


def snapshot() -> list[RecentReviewRead]:
    """返回当前缓冲区快照（DB 查询失败时的降级数据）。"""

    return list(_buffer)


def clear() -> None:
    """清空缓冲区（测试隔离用）。"""

    _buffer.clear()


__all__ = ["_MAX_ITEMS", "clear", "record", "snapshot"]
