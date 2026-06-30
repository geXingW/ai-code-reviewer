"""Review engine abstraction layer.

This package defines the :class:`ReviewEngine` abstract base class, the
``EngineRegistry`` singleton, and concrete engine implementations.

To make an engine discoverable, decorate the implementation with
:func:`app.engines.registry.register_engine` and ensure the module is
imported during application startup (see :func:`load_builtin_engines`).
"""

from app.engines.base import ReviewEngine
from app.engines.registry import (
    EngineRegistry,
    get_engine_registry,
    register_engine,
)
from app.engines.types import (
    DiffHunk,
    Finding,
    HealthStatus,
    ProviderConfig,
    ReviewContext,
    ReviewHistoryItem,
    RuleSpec,
)

__all__ = [
    "DiffHunk",
    "EngineRegistry",
    "Finding",
    "HealthStatus",
    "ProviderConfig",
    "ReviewContext",
    "ReviewEngine",
    "ReviewHistoryItem",
    "RuleSpec",
    "get_engine_registry",
    "load_builtin_engines",
    "register_engine",
]


def load_builtin_engines() -> None:
    """Import built-in engine modules so their ``@register_engine`` runs.

    Engines register themselves at import time via the
    :func:`register_engine` decorator. Calling this function from
    application startup guarantees the registry is populated before the
    first HTTP request hits the engines API.
    """

    # Importing the module triggers the @register_engine decorator.
    from app.engines.llm_engine import engine as _llm_engine  # noqa: F401
