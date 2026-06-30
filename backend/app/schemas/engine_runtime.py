"""HTTP wire schemas for the engines REST API.

The DB-row engine schemas already live in ``app.schemas.engine`` and
cover the *configured* engines table. The runtime engines exposed by
``/api/engines`` are a different concept — the in-memory registry — so
they get their own dedicated response models here.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EngineHealthState = Literal["ok", "degraded", "error", "unknown"]


class EngineSummary(BaseModel):
    """Compact engine descriptor returned by ``GET /api/engines``.

    Attributes:
        name: Registry identifier (e.g. ``"llm-direct"``).
        supports_feedback: Whether the engine consumes confirmed false
            positives via :attr:`ReviewContext.history`.
        requires_repo_clone: Whether the orchestrator must clone the
            repo before invoking the engine.
        healthy: Convenience flag — ``True`` iff the latest
            ``health_check`` returned ``status == "ok"``.
        health_status: Raw health state from the engine.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    supports_feedback: bool
    requires_repo_clone: bool
    healthy: bool
    health_status: EngineHealthState


class EngineHealth(BaseModel):
    """Detailed engine health returned by ``GET /api/engines/{name}/health``.

    Attributes:
        name: Registry identifier.
        status: Operational state.
        message: Free-form human-readable note from the engine.
        details: Engine-specific structured payload.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    status: EngineHealthState
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
