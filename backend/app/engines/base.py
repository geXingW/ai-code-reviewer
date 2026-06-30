"""Abstract base class for review engines.

A ``ReviewEngine`` is the pluggable strategy that turns a
:class:`~app.engines.types.ReviewContext` into a list of
:class:`~app.engines.types.Finding` objects.

Subclasses must:

* Implement :meth:`name` returning a unique short identifier.
* Implement :meth:`review` performing the actual analysis.
* Implement :meth:`supports_feedback` declaring whether the engine can
  consume confirmed false positives for in-context learning.
* Implement :meth:`health_check` so the operator UI can monitor it.

They *may* override :meth:`requires_repo_clone` if the engine needs the
full repository checked out (e.g. an OcrEngine running static
analyzers); the default is ``False`` so the orchestrator can keep the
fast path diff-only.
"""

from abc import ABC, abstractmethod

from app.engines.types import Finding, HealthStatus, ReviewContext


class ReviewEngine(ABC):
    """Strategy interface every review engine must implement.

    Engines are looked up by :attr:`name` via the
    :class:`~app.engines.registry.EngineRegistry`. The name doubles as
    the value persisted in ``reviews.engine_used``, so once an engine
    has shipped to production, **do not rename it** — add a new engine
    instead.
    """

    @abstractmethod
    def name(self) -> str:
        """Return the unique short identifier for this engine.

        Conventions:
            * lower-case
            * kebab-case
            * stable across deploys

        Examples: ``"llm-direct"``, ``"static-ruff"``, ``"ocr-bundle"``.

        Returns:
            str: The engine identifier used in the registry and DB.
        """

    @abstractmethod
    async def review(self, ctx: ReviewContext) -> list[Finding]:
        """Analyse ``ctx`` and return zero or more findings.

        Implementations MUST:

        * Be async-safe — they will be awaited inside FastAPI.
        * Not mutate ``ctx``.
        * Return findings whose ``file_path`` and ``line_number`` refer
          to the *new* side of the diff (post-merge line numbers).
        * Raise on unrecoverable errors; the orchestrator will mark the
          review as ``failed`` and surface the exception in the audit log.

        Args:
            ctx: All data needed for the review run.

        Returns:
            list[Finding]: Findings to persist (may be empty).
        """

    @abstractmethod
    def supports_feedback(self) -> bool:
        """Declare whether the engine consumes false-positive feedback.

        When ``True``, the orchestrator passes prior confirmed false
        positives via :attr:`ReviewContext.history` and the engine is
        expected to suppress matching findings. The frontend filters
        the "false-positive review queue" to only show engines that
        return ``True`` here.

        Returns:
            bool: True if ``ReviewContext.history`` is meaningful.
        """

    def requires_repo_clone(self) -> bool:
        """Declare whether this engine needs the full repo cloned.

        Default: ``False`` (diff-only is enough). Override for engines
        that drive subprocess-based static analyzers.

        Returns:
            bool: True if the orchestrator must populate
            ``ReviewContext.repo_url`` and clone before invoking.
        """

        return False

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Run a lightweight self-check.

        Used by both ``/api/engines/{name}/health`` and the periodic
        status pings from the operator dashboard. Should return quickly
        (target <2s) and MUST NOT raise — failures should be reported
        via :class:`~app.engines.types.HealthStatus` with
        ``status="error"`` and a populated ``message``.

        Returns:
            HealthStatus: The engine's current operational state.
        """
