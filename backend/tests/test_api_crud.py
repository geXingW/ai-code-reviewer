"""Tests for MVP admin REST CRUD and false-positive APIs."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config, db
from app.core.db import Base, get_db
from app.main import create_app

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
)


@pytest_asyncio.fixture
async def admin_client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """Create an isolated FastAPI client backed by an in-memory database."""

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode("utf-8"))
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

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login_response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        assert login_response.status_code == 200
        client.headers.update({"Authorization": f"Bearer {login_response.json()['access_token']}"})
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()
    config.get_settings.cache_clear()
    db.get_settings.cache_clear()


async def create_project(client: AsyncClient, name: str = "demo") -> str:
    """Create a project through the public REST API and return its UUID."""

    response = await client.post(
        "/api/projects",
        json={
            "name": name,
            "gitlab_project_id": f"group/{name}",
            "gitlab_access_token": "gl-token",
            "webhook_secret": "hook-secret",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


async def seed_review_with_finding(client: AsyncClient) -> dict[str, str]:
    """Seed a project, review, and finding via API calls for false-positive tests."""

    project_id = await create_project(client)
    review_response = await client.post(
        "/api/reviews/records",
        json={
            "project_id": project_id,
            "mr_iid": "1",
            "source_branch": "feature/demo",
            "target_branch": "master",
            "commit_sha": "abc123",
            "status": "done",
        },
    )
    assert review_response.status_code == 201
    review_id = str(review_response.json()["id"])
    finding_response = await client.post(
        "/api/findings",
        json={
            "review_id": review_id,
            "file_path": "src/app.py",
            "line_number": 42,
            "rule_id": "PY001",
            "severity": "WARNING",
            "title": "Demo issue",
            "existing_code": "print('ok')",
        },
    )
    assert finding_response.status_code == 201
    return {
        "project_id": project_id,
        "review_id": review_id,
        "finding_id": str(finding_response.json()["id"]),
    }


@pytest.mark.asyncio
async def test_provider_crud_masks_secret_and_supports_pagination(
    admin_client: AsyncClient,
) -> None:
    """Provider CRUD must require auth and avoid leaking API keys."""

    unauthenticated_response = await admin_client.get(
        "/api/providers",
        headers={"Authorization": ""},
    )
    assert unauthenticated_response.status_code == 401

    create_response = await admin_client.post(
        "/api/providers",
        json={
            "name": "ark",
            "protocol": "openai_compatible",
            "base_url": "https://llm.example.com/v1",
            "api_key": "secret-key",
            "model": "glm",
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["api_key"] == "****"

    list_response = await admin_client.get("/api/providers?limit=10&offset=0&sort=-created_at")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["name"] == "ark"

    provider_id = created["id"]
    detail_response = await admin_client.get(f"/api/providers/{provider_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["api_key"] == "****"

    update_response = await admin_client.patch(
        f"/api/providers/{provider_id}",
        json={"enabled": False, "model": "glm-4"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["enabled"] is False
    assert update_response.json()["model"] == "glm-4"

    delete_response = await admin_client.delete(f"/api/providers/{provider_id}")
    assert delete_response.status_code == 204
    assert (await admin_client.get(f"/api/providers/{provider_id}")).status_code == 404


@pytest.mark.asyncio
async def test_project_nested_rules_and_block_policies_crud(admin_client: AsyncClient) -> None:
    """Project API manages nested rule links and branch block policies."""

    rule_response = await admin_client.post(
        "/api/rules",
        json={"rule_id": "PY001", "title": "No prints", "prompt_snippet": "avoid print"},
    )
    assert rule_response.status_code == 201
    rule_uuid = rule_response.json()["id"]

    project_response = await admin_client.post(
        "/api/projects",
        json={
            "name": "demo",
            "gitlab_project_id": "123",
            "gitlab_access_token": "gl-token",
            "webhook_secret": "hook-secret",
            "rules": [{"rule_id": rule_uuid, "enabled": True, "severity_override": "BLOCKER"}],
            "block_policies": [
                {
                    "branch_pattern": "master",
                    "block_severity": "BLOCKER",
                    "block_on_engine_error": True,
                    "priority": 10,
                }
            ],
        },
    )

    assert project_response.status_code == 201
    project = project_response.json()
    assert project["gitlab_access_token"] == "****"
    assert len(project["rules"]) == 1
    assert project["rules"][0]["severity_override"] == "BLOCKER"
    assert len(project["block_policies"]) == 1
    assert project["block_policies"][0]["branch_pattern"] == "master"

    update_response = await admin_client.patch(
        f"/api/projects/{project['id']}",
        json={
            "name": "demo-renamed",
            "rules": [{"rule_id": rule_uuid, "enabled": False, "severity_override": "WARNING"}],
            "block_policies": [
                {"branch_pattern": "release/*", "block_severity": "WARNING", "priority": 1}
            ],
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "demo-renamed"
    assert updated["rules"][0]["enabled"] is False
    assert updated["block_policies"][0]["branch_pattern"] == "release/*"


@pytest.mark.asyncio
async def test_review_and_finding_filters_are_readable(admin_client: AsyncClient) -> None:
    """Review and finding APIs expose filterable records for the management UI."""

    seeded = await seed_review_with_finding(admin_client)

    reviews = await admin_client.get(
        f"/api/reviews/records?project_id={seeded['project_id']}&status=done"
    )
    assert reviews.status_code == 200
    assert reviews.json()["total"] == 1
    assert reviews.json()["items"][0]["id"] == seeded["review_id"]

    findings = await admin_client.get(
        f"/api/findings?review_id={seeded['review_id']}&fp_status=NONE&severity=WARNING"
    )
    assert findings.status_code == 200
    assert findings.json()["total"] == 1
    assert findings.json()["items"][0]["id"] == seeded["finding_id"]


@pytest.mark.asyncio
async def test_false_positive_confirm_creates_negative_example(admin_client: AsyncClient) -> None:
    """Confirmed false positives are reviewed and converted into negative examples."""

    seeded = await seed_review_with_finding(admin_client)

    mark_response = await admin_client.post(
        f"/api/findings/{seeded['finding_id']}/false-positive",
        json={"marked_by": "dev", "reason": "Intentional debug helper"},
    )
    assert mark_response.status_code == 200
    assert mark_response.json()["fp_status"] == "PENDING"

    pending_response = await admin_client.get("/api/false-positives/pending")
    assert pending_response.status_code == 200
    assert pending_response.json()["total"] == 1

    confirm_response = await admin_client.post(
        f"/api/false-positives/{seeded['finding_id']}/confirm",
        json={"reviewed_by": "admin", "note": "Accepted"},
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["fp_status"] == "CONFIRMED"

    examples_response = await admin_client.get("/api/negative-examples?rule_id=PY001")
    assert examples_response.status_code == 200
    body = examples_response.json()
    assert body["total"] == 1
    assert body["items"][0]["source_finding_id"] == seeded["finding_id"]
    assert body["items"][0]["code_snippet"] == "print('ok')"


@pytest.mark.asyncio
async def test_false_positive_reject_updates_review_fields(admin_client: AsyncClient) -> None:
    """Rejected false positives leave a reviewed audit trail on the finding."""

    seeded = await seed_review_with_finding(admin_client)
    await admin_client.post(
        f"/api/findings/{seeded['finding_id']}/false-positive",
        json={"marked_by": "dev", "reason": "Looks wrong"},
    )

    response = await admin_client.post(
        f"/api/false-positives/{seeded['finding_id']}/reject",
        json={"reviewed_by": "admin", "note": "Still valid"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fp_status"] == "REJECTED"
    assert body["fp_reviewed_by"] == "admin"
    assert body["fp_review_note"] == "Still valid"


@pytest.mark.asyncio
async def test_persisted_engine_crud_and_runtime_health(admin_client: AsyncClient) -> None:
    """Persisted engine config CRUD coexists with runtime engine health endpoints."""

    created = await admin_client.post(
        "/api/engines/configs",
        json={"name": "llm-direct-test", "engine_type": "builtin", "config": {"max": 1024}},
    )
    assert created.status_code == 201
    engine_id = created.json()["id"]

    listed = await admin_client.get("/api/engines/configs?enabled=true")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    updated = await admin_client.patch(f"/api/engines/configs/{engine_id}", json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    runtime = await admin_client.get("/api/engines")
    assert runtime.status_code == 200
    assert isinstance(runtime.json(), list)


@pytest.mark.asyncio
async def test_create_rule_auto_generates_slug_from_english_title(
    admin_client: AsyncClient,
) -> None:
    """不传 rule_id 时，用英文标题自动生成 kebab-case slug。"""

    response = await admin_client.post(
        "/api/rules",
        json={"title": "No Print Statements", "prompt_snippet": "avoid print"},
    )

    assert response.status_code == 201
    assert response.json()["rule_id"] == "no-print-statements"


@pytest.mark.asyncio
async def test_create_rule_falls_back_to_uuid_slug_for_chinese_title(
    admin_client: AsyncClient,
) -> None:
    """不传 rule_id 且标题含中文时，rule_id 回退为 rule-<8 位 hex>。"""

    response = await admin_client.post(
        "/api/rules",
        json={"title": "禁止使用打印语句", "prompt_snippet": "避免 print"},
    )

    assert response.status_code == 201
    assert re.fullmatch(r"rule-[0-9a-f]{8}", response.json()["rule_id"])


@pytest.mark.asyncio
async def test_create_rule_slug_appends_suffix_on_conflict(admin_client: AsyncClient) -> None:
    """自动生成的 slug 与已有 rule_id 冲突时，追加 -2 后缀直至唯一。"""

    first = await admin_client.post(
        "/api/rules",
        json={"title": "No Print Statements", "prompt_snippet": "avoid print"},
    )
    assert first.status_code == 201
    assert first.json()["rule_id"] == "no-print-statements"

    second = await admin_client.post(
        "/api/rules",
        json={"title": "No Print Statements", "prompt_snippet": "avoid print v2"},
    )
    assert second.status_code == 201
    assert second.json()["rule_id"] == "no-print-statements-2"


@pytest.mark.asyncio
async def test_review_record_returns_project_name_and_deduped_rules_used(
    admin_client: AsyncClient,
) -> None:
    """审查记录列表/详情返回 project_name 与去重后的 rules_used。"""

    seeded = await seed_review_with_finding(admin_client)
    # 追加一条同 rule_id（验证去重）与一条不同 rule_id（验证聚合）的 finding。
    for rule_id in ("PY001", "SEC002"):
        extra = await admin_client.post(
            "/api/findings",
            json={
                "review_id": seeded["review_id"],
                "file_path": "src/extra.py",
                "line_number": 1,
                "rule_id": rule_id,
                "severity": "WARNING",
                "title": "extra finding",
            },
        )
        assert extra.status_code == 201

    list_response = await admin_client.get("/api/reviews/records")
    assert list_response.status_code == 200
    item = list_response.json()["items"][0]
    assert item["id"] == seeded["review_id"]
    assert item["project_name"] == "demo"
    assert len(item["rules_used"]) == 2
    assert set(item["rules_used"]) == {"PY001", "SEC002"}

    detail_response = await admin_client.get(f"/api/reviews/{seeded['review_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["project_name"] == "demo"
    assert len(detail["rules_used"]) == 2
    assert set(detail["rules_used"]) == {"PY001", "SEC002"}
