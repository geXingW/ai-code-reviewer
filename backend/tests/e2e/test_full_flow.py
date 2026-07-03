"""End-to-end tests for the full merge-request review flow.

These tests exercise the real HTTP layer → ``ReviewOrchestrator`` → real
``GitLabClient`` (with the GitLab REST API mocked by ``respx``) → review engine,
against a real PostgreSQL database (provider/rule/project seeded through the
admin REST API, exactly like the documented Jenkins-triggered flow).

MVP scope notes (pinned by these tests so future wiring updates them):

* The orchestrator currently returns a review summary (``has_blocker``,
  ``finding_count`` …) and writes GitLab feedback, but does **not** persist
  findings/reviews to the database yet, and does not wire a ``ProviderConfig``
  into the engine context. The production ``llm-direct`` engine therefore
  returns no findings without a provider. To keep the pipeline deterministic
  and exercise the real blocker/feedback path, a small ``StubReviewEngine`` is
  registered under ``DEFAULT_REVIEW_ENGINE`` and produces configured findings.
* There is no ``commit_sha`` deduplication yet; the duplicate-trigger scenario
  therefore asserts the current graceful re-run behavior rather than a skip.

GitLab HTTP calls are mocked with ``respx`` so no real GitLab instance is
required. Findings are verified through the synchronous ``POST /api/reviews``
response (the orchestrator's real output) and through the GitLab mock call
records (line-level discussion + commit status), which is where findings flow
in the current MVP.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
import respx
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config, db
from app.core.db import Base, get_db
from app.engines import Finding, HealthStatus, ReviewContext, ReviewEngine
from app.engines.registry import get_engine_registry
from app.main import create_app
from app.models.project_block_policy import ProjectBlockPolicy
from app.services import review_orchestrator as orchestrator_module

TEST_DATABASE_URL = "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer"
GITLAB_BASE_URL = "http://localhost"
INTERNAL_TOKEN = "test-internal-token"
STUB_ENGINE_NAME = "e2e-stub"
PROJECT_ID = 123
MR_IID = 7
COMMIT_SHA = "abc123"
NOTE_ID = 42
WEB_URL = "https://gitlab.example.com/group/demo/-/merge_requests/7"

# GitLab MR changes payload returned by the respx mock. The added line
# ``password = 'hunter2'`` lives at new-file line 1, which the stub finding
# references so the orchestrator can post a line-level discussion.
CHANGES_PAYLOAD: dict[str, Any] = {
    "diff_refs": {
        "base_sha": "base-diff-sha",
        "start_sha": "start-diff-sha",
        "head_sha": "head-diff-sha",
    },
    "changes": [
        {
            "new_path": "app.py",
            "old_path": "app.py",
            "diff": "@@ -1 +1 @@\n-old\n+password = 'hunter2'\n",
            "new_file": False,
            "deleted_file": False,
            "binary": False,
        }
    ],
}


class StubReviewEngine(ReviewEngine):
    """Deterministic engine double used in place of the provider-bound LLM engine.

    The real ``llm-direct`` engine returns no findings without a provider wired
    into the review context (MVP gap). This double returns configured findings
    or raises to simulate engine failure, letting the rest of the pipeline —
    diff filtering, block-policy matching, GitLab feedback — run for real.
    """

    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.error: Exception | None = None

    def name(self) -> str:
        return STUB_ENGINE_NAME

    async def review(self, ctx: ReviewContext) -> list[Finding]:
        del ctx
        if self.error is not None:
            raise self.error
        return [finding.model_copy() for finding in self.findings]

    def supports_feedback(self) -> bool:
        return True

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="ok")


@pytest_asyncio.fixture
async def e2e_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, StubReviewEngine], None]:
    """Yield an authenticated admin HTTP client wired to a real DB and stub engine.

    Mirrors the ``admin_client`` fixture: a fresh PostgreSQL schema, a JWT admin
    client, plus a registered ``StubReviewEngine`` selected via
    ``DEFAULT_REVIEW_ENGINE``. The stub is yielded so each test can configure
    findings or simulate an engine error.
    """

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DEFAULT_REVIEW_ENGINE", STUB_ENGINE_NAME)
    monkeypatch.setenv("GITLAB_BASE_URL", GITLAB_BASE_URL)
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()

    test_engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    registry = get_engine_registry()
    registry.unregister(STUB_ENGINE_NAME)
    stub = StubReviewEngine()
    registry.register(stub)

    application = create_app()
    application.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        assert login.status_code == 200, login.text
        client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
        yield client, stub

    application.dependency_overrides.clear()
    registry.unregister(STUB_ENGINE_NAME)
    await test_engine.dispose()
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


@pytest.fixture
def gitlab_mock() -> SimpleNamespace:
    """Mock the GitLab REST API surface used by one review run via ``respx``.

    All four endpoints are registered with ``assert_all_called=False`` because
    the engine-error path legitimately skips the line-level discussion route.
    """

    base = f"{GITLAB_BASE_URL}/api/v4/projects/{PROJECT_ID}"
    mr_url = f"{base}/merge_requests/{MR_IID}"
    with respx.mock(assert_all_called=False) as router:
        changes = router.get(f"{mr_url}/changes").mock(
            return_value=Response(200, json=CHANGES_PAYLOAD),
        )
        notes = router.post(f"{mr_url}/notes").mock(
            return_value=Response(201, json={"id": NOTE_ID, "body": "summary"}),
        )
        discussions = router.post(f"{mr_url}/discussions").mock(
            return_value=Response(201, json={"id": "discussion-1"}),
        )
        statuses = router.post(f"{base}/statuses/{COMMIT_SHA}").mock(
            return_value=Response(201, json={"id": 1, "status": "success"}),
        )
        yield SimpleNamespace(
            changes=changes,
            notes=notes,
            discussions=discussions,
            statuses=statuses,
        )


async def _seed_project(client: AsyncClient) -> dict[str, str]:
    """Create a provider, rule, and project (with block policies) via the admin API.

    This mirrors step 1 of the documented review flow. The orchestrator does
    not yet consume DB-stored provider/rules/block policies at review time
    (MVP gap), so these rows set up realistic state but do not drive the
    review outcome — the stub engine and branch-targeted default policies do.
    """

    provider = await client.post(
        "/api/providers",
        json={
            "name": "ark",
            "protocol": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "api_key": "secret-key",
            "model": "glm",
        },
    )
    assert provider.status_code == 201, provider.text
    rule = await client.post(
        "/api/rules",
        json={
            "rule_id": "general.hardcoded-secret",
            "title": "Hard-coded secret",
            "prompt_snippet": "Flag hard-coded secrets and credentials.",
            "severity_default": "BLOCKER",
        },
    )
    assert rule.status_code == 201, rule.text
    rule_uuid = rule.json()["id"]
    project = await client.post(
        "/api/projects",
        json={
            "name": "demo",
            "gitlab_project_id": "group/demo",
            "gitlab_access_token": "gl-token",
            "webhook_secret": "hook-secret",
            "provider_id": provider.json()["id"],
            "rules": [{"rule_id": rule_uuid, "enabled": True, "severity_override": "BLOCKER"}],
            "block_policies": [
                {
                    "branch_pattern": "master",
                    "block_severity": "BLOCKER",
                    "block_on_engine_error": False,
                    "priority": 1,
                },
                {
                    "branch_pattern": "*",
                    "block_severity": "NONE",
                    "block_on_engine_error": False,
                    "priority": 99,
                },
            ],
        },
    )
    assert project.status_code == 201, project.text
    return {
        "provider_id": provider.json()["id"],
        "rule_id": rule_uuid,
        "project_id": project.json()["id"],
    }


def _blocker_finding() -> Finding:
    """Return a BLOCKER finding located on the added line of the mock diff."""

    return Finding(
        file_path="app.py",
        line_number=1,
        rule_id="general.hardcoded-secret",
        severity="BLOCKER",
        title="Hard-coded secret detected",
        description="A secret literal was hard-coded in the diff.",
        suggestion="Move the secret to an environment variable.",
        confidence=0.92,
    )


def _review_payload(target_branch: str) -> dict[str, Any]:
    """Build the Jenkins-style synchronous review trigger body."""

    return {
        "project_id": PROJECT_ID,
        "project_path": "group/demo",
        "mr_iid": MR_IID,
        "target_branch": target_branch,
        "source_branch": "feature/demo",
        "commit_sha": COMMIT_SHA,
        "target_commit_sha": "base456",
        "title": "Demo MR",
        "web_url": WEB_URL,
    }


def _policies_blocking_engine_error(project_id: UUID) -> list[ProjectBlockPolicy]:
    """Default-policy replacement whose master policy blocks on engine errors."""

    return [
        ProjectBlockPolicy(
            project_id=project_id,
            branch_pattern="master",
            block_severity="BLOCKER",
            block_on_engine_error=True,
            require_all_resolved=False,
            priority=1,
        ),
        ProjectBlockPolicy(
            project_id=project_id,
            branch_pattern="*",
            block_severity="NONE",
            block_on_engine_error=False,
            require_all_resolved=False,
            priority=99,
        ),
    ]


def _status_payload(mock: SimpleNamespace) -> dict[str, Any]:
    """Parse the commit-status request body captured by the respx mock."""

    return json.loads(mock.statuses.calls.last.request.content)


@pytest.mark.asyncio
async def test_blocker_on_master_branch(
    e2e_client: tuple[AsyncClient, StubReviewEngine],
    gitlab_mock: SimpleNamespace,
) -> None:
    """BLOCKER finding on master → has_blocker=true, failed status, line discussion."""

    client, stub = e2e_client
    await _seed_project(client)
    stub.findings = [_blocker_finding()]

    response = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json=_review_payload("master"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["has_blocker"] is True
    assert body["finding_count"] == 1
    assert body["blocker_count"] == 1
    assert body["policy_applied"] == "master -> BLOCKER"
    assert body["review_url"] == f"{WEB_URL}#note_{NOTE_ID}"

    assert gitlab_mock.changes.called
    assert gitlab_mock.notes.called
    assert gitlab_mock.statuses.called
    assert _status_payload(gitlab_mock)["state"] == "failed"
    # The finding flowed through as a line-level GitLab discussion at line 1.
    assert gitlab_mock.discussions.called
    discussion = json.loads(gitlab_mock.discussions.calls.last.request.content)
    assert discussion["position"]["new_path"] == "app.py"
    assert discussion["position"]["new_line"] == 1
    assert "Hard-coded secret detected" in discussion["body"]


@pytest.mark.asyncio
async def test_non_blocker_on_test_branch(
    e2e_client: tuple[AsyncClient, StubReviewEngine],
    gitlab_mock: SimpleNamespace,
) -> None:
    """BLOCKER finding on feature/x (catch-all NONE policy) → has_blocker=false."""

    client, stub = e2e_client
    await _seed_project(client)
    stub.findings = [_blocker_finding()]

    response = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json=_review_payload("feature/x"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["has_blocker"] is False
    assert body["finding_count"] == 1
    assert body["blocker_count"] == 0
    assert body["policy_applied"] == "* -> NONE"

    # Same finding, comment-only: discussion still posted, status stays green.
    assert gitlab_mock.discussions.called
    assert _status_payload(gitlab_mock)["state"] == "success"


@pytest.mark.asyncio
async def test_engine_timeout_with_block_on_error(
    e2e_client: tuple[AsyncClient, StubReviewEngine],
    gitlab_mock: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine timeout + block_on_engine_error=true → has_blocker=true, no discussion.

    The HTTP flow uses default branch policies, whose master template does not
    block on engine errors. To exercise the real ``_handle_engine_error`` path
    through ``POST /api/reviews``, the default-policy builder is swapped for one
    whose master policy sets ``block_on_engine_error=True``.
    """

    client, stub = e2e_client
    await _seed_project(client)
    stub.error = TimeoutError("LLM provider timed out after 30s (token=secret)")

    monkeypatch.setattr(
        orchestrator_module,
        "build_default_block_policies",
        _policies_blocking_engine_error,
    )

    response = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json=_review_payload("master"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "engine_error"
    assert body["has_blocker"] is True
    assert body["finding_count"] == 0
    assert body["blocker_count"] == 1
    assert body["policy_applied"] == "master -> BLOCKER"

    # No findings → no line-level discussion; the summary note + failed status
    # are still written, and the exception detail must not leak to GitLab.
    assert not gitlab_mock.discussions.called
    assert gitlab_mock.notes.called
    note_body = gitlab_mock.notes.calls.last.request.content.decode()
    assert "engine failed" in note_body
    assert "timed out" not in note_body
    assert "secret" not in note_body
    assert _status_payload(gitlab_mock)["state"] == "failed"


@pytest.mark.asyncio
async def test_duplicate_commit_skipped(
    e2e_client: tuple[AsyncClient, StubReviewEngine],
    gitlab_mock: SimpleNamespace,
) -> None:
    """Duplicate commit_sha trigger: MVP re-runs (no dedup yet); both succeed.

    The orchestrator does not yet deduplicate by ``commit_sha``. This test pins
    the current graceful behavior — a second trigger with the same commit re-runs
    the review and returns a fresh result — and should be updated when dedup or
    result caching lands.
    """

    client, stub = e2e_client
    await _seed_project(client)
    stub.findings = [_blocker_finding()]

    first = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json=_review_payload("master"),
    )
    second = await client.post(
        "/api/reviews",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json=_review_payload("master"),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["has_blocker"] is True
    assert second.json()["has_blocker"] is True

    # No skip/dedup: GitLab diff + commit-status endpoints hit once per trigger.
    assert len(gitlab_mock.changes.calls) == 2
    assert len(gitlab_mock.statuses.calls) == 2
