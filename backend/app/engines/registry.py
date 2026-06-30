"""Engine registry — singleton lookup table for :class:`ReviewEngine`.

Engines self-register at import time via :func:`register_engine`:

.. code-block:: python

    from app.engines.registry import register_engine
    from app.engines.base import ReviewEngine

    @register_engine
    class MyEngine(ReviewEngine):
        def name(self) -> str: return "my-engine"
        ...

Calling :func:`app.engines.load_builtin_engines` from application
startup ensures all built-in engines have been imported (and therefore
registered) before the first request hits the engines API.

The registry is a singleton because:

* Engines are stateless from the application's perspective — they hold
  configuration but no per-request mutable state.
* Tests need a deterministic way to swap engines via
  :meth:`EngineRegistry.clear` / :meth:`EngineRegistry.register`.
"""

from __future__ import annotations

from threading import RLock
from typing import TypeVar

from app.engines.base import ReviewEngine

T = TypeVar("T", bound=ReviewEngine)


class EngineAlreadyRegisteredError(RuntimeError):
    """Raised when two engines try to claim the same name."""


class EngineNotFoundError(LookupError):
    """Raised when :meth:`EngineRegistry.get` cannot find an engine."""


class EngineRegistry:
    """Thread-safe in-memory registry of :class:`ReviewEngine` instances.

    Treat this as a singleton — get the shared one via
    :func:`get_engine_registry`. Direct construction is permitted only
    for tests that need an isolated registry.
    """

    def __init__(self) -> None:
        self._engines: dict[str, ReviewEngine] = {}
        self._lock = RLock()

    def register(self, engine: ReviewEngine) -> None:
        """Register ``engine`` under its :meth:`ReviewEngine.name`.

        Args:
            engine: Engine instance to register.

        Raises:
            EngineAlreadyRegisteredError: If an engine with the same
                name is already registered. Call :meth:`clear` first
                in tests if you need to replace one.
        """

        name = engine.name()
        with self._lock:
            if name in self._engines:
                msg = f"Engine '{name}' is already registered."
                raise EngineAlreadyRegisteredError(msg)
            self._engines[name] = engine

    def unregister(self, name: str) -> None:
        """Remove an engine by name (no-op if absent).

        Primarily used by tests via :meth:`clear`, but exposed for
        completeness so dynamic plug-ins can detach themselves.

        Args:
            name: Engine identifier.
        """

        with self._lock:
            self._engines.pop(name, None)

    def get(self, name: str) -> ReviewEngine:
        """Look up an engine by name.

        Args:
            name: Engine identifier.

        Returns:
            ReviewEngine: The registered engine instance.

        Raises:
            EngineNotFoundError: If no engine with ``name`` is registered.
        """

        with self._lock:
            engine = self._engines.get(name)
        if engine is None:
            msg = f"Engine '{name}' is not registered."
            raise EngineNotFoundError(msg)
        return engine

    def all(self) -> list[ReviewEngine]:
        """Return every registered engine.

        Returns:
            list[ReviewEngine]: Snapshot of all engines (order: by name).
        """

        with self._lock:
            return [self._engines[n] for n in sorted(self._engines)]

    def names(self) -> list[str]:
        """Return the sorted list of registered engine names.

        Returns:
            list[str]: Sorted engine identifiers.
        """

        with self._lock:
            return sorted(self._engines)

    def clear(self) -> None:
        """Drop every registered engine (test helper)."""

        with self._lock:
            self._engines.clear()


_registry = EngineRegistry()


def get_engine_registry() -> EngineRegistry:
    """Return the process-wide :class:`EngineRegistry` singleton.

    Returns:
        EngineRegistry: The shared registry instance.
    """

    return _registry


def register_engine(cls: type[T]) -> type[T]:
    """Class decorator that registers an engine at import time.

    The decorator instantiates the class with no arguments and registers
    the resulting instance in the shared registry. Engines that need
    runtime configuration should accept it from
    :class:`~app.engines.types.ReviewContext` (per-review) or read
    application settings inside :meth:`review` / :meth:`health_check`
    (process-wide).

    Args:
        cls: A concrete :class:`ReviewEngine` subclass.

    Returns:
        type[T]: The class, unchanged, so the decorator is transparent.

    Raises:
        EngineAlreadyRegisteredError: If the name is already taken.
    """

    instance = cls()
    get_engine_registry().register(instance)
    return cls
