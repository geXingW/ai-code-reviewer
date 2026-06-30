"""Unit tests for the engine registry and decorator semantics."""

from __future__ import annotations

import pytest

from app.engines import (
    EngineRegistry,
    Finding,
    HealthStatus,
    ReviewContext,
    ReviewEngine,
)
from app.engines.registry import (
    EngineAlreadyRegisteredError,
    EngineNotFoundError,
)


class _DummyEngine(ReviewEngine):
    """Minimal engine for registry unit tests."""

    def __init__(self, name: str = "dummy", feedback: bool = False) -> None:
        self._name = name
        self._feedback = feedback

    def name(self) -> str:
        return self._name

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        _ = ctx
        return []

    def supports_feedback(self) -> bool:
        return self._feedback

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")


def test_register_and_get_returns_same_instance() -> None:
    """register() then get() yields the exact instance that was stored."""

    registry = EngineRegistry()
    engine = _DummyEngine()
    registry.register(engine)

    assert registry.get("dummy") is engine


def test_get_unknown_raises_not_found() -> None:
    """Looking up an unregistered name raises EngineNotFoundError."""

    registry = EngineRegistry()
    with pytest.raises(EngineNotFoundError):
        registry.get("missing")


def test_duplicate_registration_raises() -> None:
    """Two engines may not share a name in the same registry."""

    registry = EngineRegistry()
    registry.register(_DummyEngine("dup"))
    with pytest.raises(EngineAlreadyRegisteredError):
        registry.register(_DummyEngine("dup"))


def test_unregister_is_idempotent() -> None:
    """unregister() never raises and removes the entry when present."""

    registry = EngineRegistry()
    registry.register(_DummyEngine("ephemeral"))
    registry.unregister("ephemeral")
    registry.unregister("ephemeral")  # second call must be a no-op
    with pytest.raises(EngineNotFoundError):
        registry.get("ephemeral")


def test_all_returns_sorted_snapshot() -> None:
    """all() returns engines ordered by name and is a snapshot copy."""

    registry = EngineRegistry()
    registry.register(_DummyEngine("zeta"))
    registry.register(_DummyEngine("alpha"))
    registry.register(_DummyEngine("mu"))

    names = [e.name() for e in registry.all()]
    assert names == ["alpha", "mu", "zeta"]
    assert registry.names() == ["alpha", "mu", "zeta"]


def test_clear_removes_everything() -> None:
    """clear() empties the registry — needed by test fixtures."""

    registry = EngineRegistry()
    registry.register(_DummyEngine("a"))
    registry.register(_DummyEngine("b"))
    registry.clear()
    assert registry.names() == []


def test_builtin_llm_direct_is_registered() -> None:
    """Importing the engines package self-registers ``llm-direct``.

    Note: ``load_builtin_engines`` triggers ``@register_engine`` only on
    first import. To exercise a *fresh* registry, we use a local one
    and instantiate the engine class directly.
    """

    from app.engines import EngineRegistry
    from app.engines.llm_engine.engine import LLMDirectEngine

    local = EngineRegistry()
    local.register(LLMDirectEngine())
    assert "llm-direct" in local.names()
    engine = local.get("llm-direct")
    assert engine.supports_feedback() is True
    assert engine.requires_repo_clone() is False
