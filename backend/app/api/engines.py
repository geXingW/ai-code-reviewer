"""REST endpoints for the in-memory engine registry.

These endpoints expose the *runtime* :class:`~app.engines.base.ReviewEngine`
instances registered via :func:`~app.engines.registry.register_engine`,
not the persisted ``engines`` table (that one is served by a separate
CRUD router landing in Issue #10).
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.engines.registry import EngineNotFoundError, get_engine_registry
from app.schemas.engine_runtime import EngineHealth, EngineSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/engines", tags=["engines"])


@router.get("", response_model=list[EngineSummary])
async def list_engines() -> list[EngineSummary]:
    """List every registered review engine with a one-shot health probe.

    The health probe is called inline for each engine. Engines must
    keep ``health_check`` fast (target <2s) per the contract in
    :class:`~app.engines.base.ReviewEngine`. Failures are swallowed and
    represented as ``health_status="error"`` so a single broken engine
    cannot break the whole list.

    Returns:
        list[EngineSummary]: One entry per registered engine.
    """

    registry = get_engine_registry()
    summaries: list[EngineSummary] = []
    for engine in registry.all():
        engine_name = engine.name()
        try:
            health = await engine.health_check()
            health_state = health.status
        except Exception:
            logger.exception("engine '%s' health_check raised", engine_name)
            health_state = "error"

        summaries.append(
            EngineSummary(
                name=engine_name,
                supports_feedback=engine.supports_feedback(),
                requires_repo_clone=engine.requires_repo_clone(),
                healthy=health_state == "ok",
                health_status=health_state,
            )
        )
    return summaries


@router.get("/{name}/health", response_model=EngineHealth)
async def get_engine_health(name: str) -> EngineHealth:
    """Return the detailed health report for a single engine.

    Args:
        name: Engine identifier returned by ``GET /api/engines``.

    Returns:
        EngineHealth: Detailed health payload from the engine.

    Raises:
        HTTPException: 404 if the engine is not registered.
    """

    registry = get_engine_registry()
    try:
        engine = registry.get(name)
    except EngineNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engine '{name}' is not registered.",
        ) from exc

    try:
        health = await engine.health_check()
    except Exception as exc:
        logger.exception("engine '%s' health_check raised", name)
        return EngineHealth(
            name=name,
            status="error",
            message=f"health_check raised: {exc.__class__.__name__}: {exc}",
        )

    return EngineHealth(
        name=name,
        status=health.status,
        message=health.message,
        details=health.details,
    )
