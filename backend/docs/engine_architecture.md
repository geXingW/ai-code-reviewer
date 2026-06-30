# Review Engine Architecture

> **Status:** stable contract — implementation: skeleton (Issue #3); full LLM
> engine lands in Issue #6.

This document explains the *review engine* abstraction layer: what
problem it solves, the contract concrete engines must honour, and how to
add a new engine.

---

## 1. Why an abstraction layer

The product roadmap intentionally keeps the engine slot pluggable. We
need to ship three engine families over the next two quarters:

1. **`llm-direct`** — diff + rules → prompt → LLM → findings. Cheap,
   fast, no clone required.
2. **`ocr-bundle`** — run language-specific static analyzers (Ruff,
   ESLint, gosec, …) inside a sandbox and translate their reports.
   Needs a full clone of the source branch.
3. **`hybrid-*`** — fan-out to multiple inner engines and merge their
   findings with a confidence-weighted voting scheme.

Hard-coding one of these into the orchestrator would force a rewrite
for the next one. So the orchestrator only knows about the
`ReviewEngine` interface and consults the registry by name.

A second goal is **operational visibility**: ops needs a single
endpoint that lists the engines a deployment has loaded and reports
their health. That is `GET /api/engines` and
`GET /api/engines/{name}/health`.

---

## 2. The contract: `ReviewEngine`

Located in `app/engines/base.py`.

```python
class ReviewEngine(ABC):
    def name(self) -> str: ...
    async def review(self, ctx: ReviewContext) -> list[Finding]: ...
    def supports_feedback(self) -> bool: ...
    def requires_repo_clone(self) -> bool: ...  # default False
    async def health_check(self) -> HealthStatus: ...
```

### Rules every engine must follow

- `name()` is **stable forever**. It is written into the `reviews.engine_used`
  column. Rename ⇒ historical rows become orphans. Add a new engine instead.
- `review()` is async-safe and **must not mutate `ctx`**. Return findings
  with `file_path` / `line_number` referring to the **new** side of the
  diff (post-merge line numbers).
- `review()` may raise — the orchestrator marks the review as `failed`
  and logs the exception.
- `supports_feedback()` returning `True` means the engine consumes
  `ReviewContext.history` (prior confirmed false positives) to suppress
  duplicate findings. The frontend filters its false-positive review
  queue by this flag.
- `requires_repo_clone()` defaults to `False`. Flip to `True` only if
  the engine genuinely needs a checked-out tree (e.g. invoking a
  subprocess static analyzer). Diff-only engines should keep the default
  so the orchestrator avoids the clone cost.
- `health_check()` **must not raise**. Target latency < 2 s. Report
  failures via `HealthStatus(status="error", message=...)`.

---

## 3. Runtime types

Located in `app/engines/types.py`. These are **runtime** types — distinct
from `app.schemas.*` which describe DB rows.

- `ReviewContext` — everything an engine receives for one MR review:
  `diff_hunks`, `rules`, resolved `provider` (plaintext API key),
  `history` of confirmed false positives, optional `repo_url`.
- `Finding` — structured engine output. Aligned with
  `FindingCreate` so the orchestrator's mapping to DB rows is trivial.
- `HealthStatus` — what `health_check` returns. `status ∈ {ok, degraded,
  error}`.
- Supporting models: `DiffHunk`, `RuleSpec`, `ProviderConfig`,
  `ReviewHistoryItem`.

All models are `pydantic.BaseModel` with `extra="forbid"` — typos in
field names crash early instead of being silently dropped.

---

## 4. The registry

Located in `app/engines/registry.py`. A single process-wide
`EngineRegistry` singleton holds the runtime engines.

### Self-registration

Engines opt in with a decorator:

```python
from app.engines.base import ReviewEngine
from app.engines.registry import register_engine
from app.engines.types import Finding, HealthStatus, ReviewContext


@register_engine
class MyEngine(ReviewEngine):
    def name(self) -> str: return "my-engine"

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        ...

    def supports_feedback(self) -> bool: return False

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")
```

`@register_engine` instantiates the class with **no arguments** and
inserts the instance into the registry. Per-review config flows in via
`ReviewContext`; per-process config should be read inside `review()` /
`health_check()` from `app.core.config`.

### Bootstrap

`app/engines/__init__.py` exposes `load_builtin_engines()`. The lifespan
hook in `app/main.py` calls it on application startup, which imports
every built-in engine module and triggers its `@register_engine`.

### Errors

- `EngineAlreadyRegisteredError` — two engines fight for the same name.
- `EngineNotFoundError` — `registry.get(name)` for an unknown engine.

### Test hooks

`EngineRegistry.clear()` empties the registry — used by per-test
fixtures to isolate state between cases.

---

## 5. REST surface

Located in `app/api/engines.py`. Routes are mounted at `/api/engines`.

### `GET /api/engines`

Returns `list[EngineSummary]`. One entry per registered engine, with an
inline health probe per engine. Broken engines surface as
`healthy=false, health_status="error"` — one engine cannot break the
listing.

Example response (truncated):

```json
[
  {
    "name": "llm-direct",
    "supports_feedback": true,
    "requires_repo_clone": false,
    "healthy": true,
    "health_status": "ok"
  }
]
```

### `GET /api/engines/{name}/health`

Returns `EngineHealth` for a single engine. 404 when `name` is not
registered. Engine exceptions are swallowed and translated to
`status="error"` so monitoring scrapes never get an HTTP 500 from a
buggy engine.

Example response:

```json
{
  "name": "llm-direct",
  "status": "ok",
  "message": "Skeleton engine — full implementation lands in Issue #6.",
  "details": {"implementation": "placeholder", "tracking_issue": 6}
}
```

---

## 6. Adding a new engine

1. Create `app/engines/<my_engine>/__init__.py` (package docstring) and
   `app/engines/<my_engine>/engine.py`.
2. Subclass `ReviewEngine`, decorate with `@register_engine`.
3. Import the module from `load_builtin_engines()` in
   `app/engines/__init__.py` so registration runs at startup.
4. Add unit tests under `tests/`. Use an isolated `EngineRegistry` for
   registry assertions; the global registry is reset by autouse
   fixtures in `tests/test_engines_api.py`.
5. Document the engine's quirks: API-key requirements, latency target,
   whether it sets `requires_repo_clone() == True`.

---

## 7. What this Issue #3 ships vs. what comes later

This PR ships **the abstraction layer and a placeholder
`llm-direct`** that returns no findings. The placeholder exists so the
registry, REST API, and lifespan wiring are exercisable end-to-end and
covered by tests.

The real engine internals — prompt template, sliding-window line
resolution, second-pass false-positive filter, OpenAI-compat HTTP
client — arrive in Issue #6. They will replace the body of
`app/engines/llm_engine/engine.py` **without breaking the public
contract** documented above.

When new engine families (OCR bundle, hybrid) start, follow the steps
in §6 and they will appear in `/api/engines` automatically.
