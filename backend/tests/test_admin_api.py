"""Tests for MVP admin REST API and false-positive workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.api.admin import (
    FalsePositiveMarkRequest,
    FalsePositiveReviewRequest,
    confirm_false_positive,
    mark_false_positive,
    reject_false_positive,
)
from app.models.negative_example import NegativeExample


@dataclass
class FakeProject:
    """Minimal project test double used by _finding_to_read enrichment."""

    # 只保留 admin API enrich 层实际访问到的两个属性；不模拟 SQLAlchemy 的
    # relationship 语义，测试只关心 name / id 能被读到。
    id: UUID
    name: str = "demo-project"
    gitlab_project_id: str = "123"
    gitlab_access_token: str | None = "test-token"


@dataclass
class FakeReview:
    """Minimal review test double used to attach negative examples to projects."""

    id: UUID
    project_id: UUID
    # PR #92：admin _finding_to_read 读 review.mr_iid / review.created_at /
    # review.project.name & id；老测试没这几个字段就会 AttributeError。
    mr_iid: str = "1"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    project: FakeProject | None = None


@dataclass
class FakeFinding:
    """Minimal finding test double matching fields used by admin FP endpoints."""

    id: UUID
    review_id: UUID
    file_path: str = "app/example.py"
    line_number: int | None = 12
    rule_id: str = "PY-SAFE-001"
    severity: str = "WARNING"
    title: str = "False alarm"
    description: str | None = "Looks unsafe but is guarded."
    suggestion: str | None = "No change needed."
    existing_code: str | None = "safe_call(user_input)"
    confidence: float = 0.7
    gitlab_discussion_id: str | None = None
    fp_status: str = "NONE"
    fp_marked_by: str | None = None
    fp_marked_at: datetime | None = None
    fp_marked_reason: str | None = None
    fp_reviewed_by: str | None = None
    fp_reviewed_at: datetime | None = None
    fp_review_note: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # PR #92：admin _finding_to_read 会 finding.review 读关系里的 MR / project
    # 上下文。默认 None 时 enrich 会走\"review 未 attach\"分支，展示字段全 None，
    # 符合老测试的既有断言（老断言不校验展示字段）。
    review: FakeReview | None = None


class FakeSession:
    """Tiny async session fake for endpoint-level false-positive tests."""

    def __init__(self, finding: FakeFinding, review: FakeReview | None = None) -> None:
        self.finding = finding
        self.review = review
        self.added: list[object] = []
        self.committed = False
        self.refreshed: list[object] = []

    async def get(self, model_type: type[object], model_id: UUID) -> object | None:
        """Return fake objects by SQLAlchemy model class name and ID."""

        if model_type.__name__ == "Finding" and model_id == self.finding.id:
            return self.finding
        if self.review and model_type.__name__ == "Review" and model_id == self.review.id:
            return self.review
        return None

    def add(self, model: object) -> None:
        """Capture ORM objects that would be persisted."""

        self.added.append(model)

    async def commit(self) -> None:
        """Record successful commit."""

        self.committed = True

    async def rollback(self) -> None:
        """No-op rollback for compatibility."""

    async def refresh(
        self,
        model: object,
        attribute_names: list[str] | None = None,
    ) -> None:
        """Record refreshed ORM objects.

        PR #92 起 admin.py 里的 mark/confirm/reject 会显式传 attribute_names 来
        强制重新加载 relationship（避免 stale），fake 也要接受这个 kwarg，否则
        TypeError。fake 不真的执行 refresh 逻辑，参数忽略即可。
        """

        del attribute_names
        self.refreshed.append(model)


@pytest.fixture
def resolve_discussion_calls() -> list[dict[str, Any]]:
    """
    记录 resolve_discussion 调用参数，方便 assert GitLab API 是否被正确调用。
    """
    return []


@pytest.fixture(autouse=True)
def patch_gitlab_client(
    monkeypatch: pytest.MonkeyPatch,
    resolve_discussion_calls: list[dict[str, Any]],
) -> None:
    """
    替换 admin.py 里的 _build_gitlab_client 工厂方法，让它返回一个 fake 客户端。
    每次 resolve_discussion 被调用就把参数记到 resolve_discussion_calls 里。
    """

    class FakeGitLabClient:
        async def resolve_discussion(
            self, project_id: int, mr_iid: int, discussion_id: str, resolved: bool
        ) -> None:
            resolve_discussion_calls.append({
                "project_id": project_id,
                "mr_iid": mr_iid,
                "discussion_id": discussion_id,
                "resolved": resolved,
            })

    def fake_build(project: FakeProject) -> FakeGitLabClient | None:
        if not project.gitlab_access_token:
            return None
        return FakeGitLabClient()

    monkeypatch.setattr("app.api.admin._build_gitlab_client", fake_build)


@pytest.mark.asyncio
async def test_login_rejects_invalid_credentials(client: AsyncClient) -> None:
    """MVP login endpoint rejects bad credentials without touching the database."""

    response = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_returns_bearer_token_for_valid_credentials(client: AsyncClient) -> None:
    """MVP login endpoint returns a bearer token that can authenticate admin APIs."""

    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] > 0
    assert isinstance(payload["access_token"], str)
    # Standard JWT has three dot-separated segments: header.payload.signature
    assert payload["access_token"].count(".") == 2
    # PR-B：LoginResponse 增加 username 字段，前端会存 sessionStorage 用作默认标记人。
    assert payload["username"] == "admin"


@pytest.mark.asyncio
async def test_admin_api_rejects_missing_bearer_token(client: AsyncClient) -> None:
    """Management APIs other than login require a bearer token."""

    response = await client.get("/api/providers")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid admin token"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_mark_false_positive_sets_pending_audit_fields() -> None:
    """Developers can mark a finding as pending false-positive review."""

    finding = FakeFinding(id=uuid4(), review_id=uuid4())
    session = FakeSession(finding)

    response = await mark_false_positive(
        finding.id,
        FalsePositiveMarkRequest(marked_by="dev@example.com", reason="Generated file"),
        session,  # type: ignore[arg-type]
    )

    assert response.fp_status == "PENDING"
    assert response.fp_marked_by == "dev@example.com"
    assert response.fp_marked_reason == "Generated file"
    assert response.fp_marked_at is not None
    assert response.fp_reviewed_by is None
    assert session.committed is True


@pytest.mark.asyncio
async def test_confirm_false_positive_creates_negative_example() -> None:
    """Confirming a pending FP creates an approved negative example for prompting."""

    review = FakeReview(id=uuid4(), project_id=uuid4())
    finding = FakeFinding(id=uuid4(), review_id=review.id, fp_status="PENDING")
    session = FakeSession(finding, review)

    response = await confirm_false_positive(
        finding.id,
        FalsePositiveReviewRequest(reviewed_by="lead@example.com", note="Known safe wrapper"),
        session,  # type: ignore[arg-type]
    )

    assert response.fp_status == "CONFIRMED"
    assert response.fp_reviewed_by == "lead@example.com"
    assert response.fp_review_note == "Known safe wrapper"
    assert len(session.added) == 1
    negative_example = session.added[0]
    assert isinstance(negative_example, NegativeExample)
    assert negative_example.rule_id == finding.rule_id
    assert negative_example.project_id == review.project_id
    assert negative_example.code_snippet == finding.existing_code
    assert negative_example.approved_by == "lead@example.com"


@pytest.mark.asyncio
async def test_reject_false_positive_keeps_finding_out_of_negative_examples() -> None:
    """Rejecting a pending FP records audit fields without creating prompt examples."""

    finding = FakeFinding(id=uuid4(), review_id=uuid4(), fp_status="PENDING")
    session = FakeSession(finding)

    response = await reject_false_positive(
        finding.id,
        FalsePositiveReviewRequest(reviewed_by="lead@example.com", note="Real blocker"),
        session,  # type: ignore[arg-type]
    )

    assert response.fp_status == "REJECTED"
    assert response.fp_reviewed_by == "lead@example.com"
    assert response.fp_review_note == "Real blocker"
    assert session.added == []


@pytest.mark.asyncio
async def test_jwt_token_contains_standard_claims(client: AsyncClient) -> None:
    """Login token must be a decodable JWT with sub, exp, and iat claims."""

    import jwt as pyjwt

    from app.core.config import get_settings

    response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    token = response.json()["access_token"]

    settings = get_settings()
    decoded = pyjwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )
    assert decoded["sub"] == "admin"
    assert "exp" in decoded
    assert "iat" in decoded


@pytest.mark.asyncio
async def test_expired_jwt_token_is_rejected(client: AsyncClient) -> None:
    """Expired JWT tokens must be rejected with 401 by admin auth middleware."""

    from datetime import datetime, timedelta

    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    expired_token = pyjwt.encode(
        {"sub": "admin", "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp())},
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get(
        "/api/providers",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_tampered_jwt_token_is_rejected(client: AsyncClient) -> None:
    """Tokens signed with a different secret must be rejected with 401."""

    from datetime import datetime, timedelta

    import jwt as pyjwt

    from app.core.config import get_settings

    settings = get_settings()
    tampered_token = pyjwt.encode(
        {"sub": "admin", "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp())},
        "completely-wrong-secret-key",
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get(
        "/api/providers",
        headers={"Authorization": f"Bearer {tampered_token}"},
    )
    assert response.status_code == 401
