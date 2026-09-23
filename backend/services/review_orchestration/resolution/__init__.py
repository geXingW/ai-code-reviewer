"""Provider / rules / history 上下文解析子包（原 context_builder.py）。

公共 API 重导出；包外调用方一律走本 ``__init__``
（``from services.review_orchestration.resolution import resolve_rules``）。
"""

from services.review_orchestration.resolution.history import _resolve_history
from services.review_orchestration.resolution.provider import _resolve_provider
from services.review_orchestration.resolution.rules import resolve_rules

__all__ = [
    "_resolve_history",
    "_resolve_provider",
    "resolve_rules",
]
