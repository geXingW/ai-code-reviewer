"""Placeholder LLM engine implementation.

The fully-fledged engine — with the 5-section prompt template, sliding
window line resolution, and second-pass false-positive filtering — is
delivered in Issue #6. This module exists so:

* Issue #3 can wire up registry discovery without a circular dep.
* ``/api/engines`` returns a non-empty list out of the box.
* The contract surface (``review`` returning ``[]``,
  ``supports_feedback`` returning ``True``, ``health_check`` returning
  ``ok``) is covered by tests today and won't regress when the real
  implementation lands.
"""

from app.engines.base import ReviewEngine
from app.engines.registry import register_engine
from app.engines.types import Finding, HealthStatus, ReviewContext


@register_engine
class LLMDirectEngine(ReviewEngine):
    """Skeleton LLM engine — returns no findings until Issue #6.

    The class is decorated with :func:`register_engine` so the act of
    importing this module is sufficient to populate the registry.
    """

    _NAME = "llm-direct"

    def name(self) -> str:
        """Return the registry identifier.

        Returns:
            str: Always ``"llm-direct"``.
        """

        return self._NAME

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        """Return an empty finding list — real implementation is Issue #6.

        Args:
            ctx: Ignored in this placeholder.

        Returns:
            list[Finding]: Empty list.
        """

        # Reference ctx so static analyzers don't flag the unused arg.
        _ = ctx
        return []

    def supports_feedback(self) -> bool:
        """Return ``True`` — the real engine will consume FP history.

        Declaring this up front lets the frontend's false-positive
        review queue surface ``llm-direct`` projects today, so the
        flow is testable before Issue #6 lands.

        Returns:
            bool: Always ``True``.
        """

        return True

    async def health_check(self) -> HealthStatus:
        """Report engine health.

        The placeholder reports ``ok`` because there is no upstream
        provider to ping yet. Issue #6 will replace this with a real
        provider round-trip.

        Returns:
            HealthStatus: ``ok`` with a placeholder note.
        """

        return HealthStatus(
            status="ok",
            details={"implementation": "placeholder", "tracking_issue": 6},
            message="Skeleton engine — full implementation lands in Issue #6.",
        )
