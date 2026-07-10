"""Async SQLAlchemy model CRUD and relationship tests."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base
from app.models.engine import Engine
from app.models.finding import Finding
from app.models.project import Project
from app.models.project_block_policy import ProjectBlockPolicy
from app.models.project_rule import ProjectRule
from app.models.provider import Provider
from app.models.review import Review
from app.models.rule import Rule

TEST_SECRET_KEY = Fernet.generate_key().decode("utf-8")
os.environ["SECRET_KEY"] = TEST_SECRET_KEY
get_settings.cache_clear()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an isolated database schema and async session for one test."""

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://ai_reviewer:ai_reviewer@localhost:5432/ai_code_reviewer",
    )
    test_engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        # pgcrypto 扩展仅 PostgreSQL 需要，跨方言时跳过
        if database_url.startswith("postgresql"):
            await connection.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


async def scalar_one(session: AsyncSession, statement: Select[tuple[object]]) -> object:
    """Return one scalar value from a typed SQLAlchemy select statement."""

    return (await session.execute(statement)).scalar_one()


@pytest.mark.asyncio
async def test_create_engine_reads_all_fields(db_session: AsyncSession) -> None:
    """Create an engine and verify persisted fields and defaults."""

    engine = Engine(name="llm-direct-test", engine_type="builtin", config={"max": 128000})
    db_session.add(engine)
    await db_session.commit()

    loaded = await db_session.scalar(select(Engine).where(Engine.name == "llm-direct-test"))

    assert loaded is not None
    assert loaded.id is not None
    assert loaded.name == "llm-direct-test"
    assert loaded.engine_type == "builtin"
    assert loaded.config == {"max": 128000}
    assert loaded.enabled is True
    assert loaded.created_at is not None
    assert loaded.updated_at is not None


@pytest.mark.asyncio
async def test_provider_api_key_encrypts_and_decrypts(db_session: AsyncSession) -> None:
    """Create a provider and verify transparent API key decryption."""

    provider = Provider(
        name="provider-test",
        protocol="openai_compatible",
        base_url="https://llm.example.com/v1",
        api_key="plain-api-key",
        model="code-model",
    )
    db_session.add(provider)
    await db_session.commit()
    await db_session.refresh(provider)

    loaded = await db_session.scalar(select(Provider).where(Provider.name == "provider-test"))
    raw_api_key = await db_session.scalar(
        text("SELECT api_key FROM providers WHERE name = 'provider-test'")
    )

    assert loaded is not None
    assert loaded.api_key == "plain-api-key"
    assert raw_api_key != "plain-api-key"
    assert isinstance(raw_api_key, str)


@pytest.mark.asyncio
async def test_project_tokens_encrypt_and_decrypt(db_session: AsyncSession) -> None:
    """Create a project and verify encrypted GitLab token and webhook secret."""

    project = Project(
        name="project-test",
        gitlab_project_id="123",
        gitlab_access_token="plain-token",
        webhook_secret="plain-secret",
    )
    db_session.add(project)
    await db_session.commit()

    loaded = await db_session.scalar(select(Project).where(Project.gitlab_project_id == "123"))
    raw_values = (
        await db_session.execute(
            text(
                "SELECT gitlab_access_token, webhook_secret "
                "FROM projects WHERE gitlab_project_id = '123'"
            )
        )
    ).one()

    assert loaded is not None
    assert loaded.gitlab_access_token == "plain-token"
    assert loaded.webhook_secret == "plain-secret"
    assert raw_values.gitlab_access_token != "plain-token"
    assert raw_values.webhook_secret != "plain-secret"


@pytest.mark.asyncio
async def test_project_relates_to_engine_and_provider(db_session: AsyncSession) -> None:
    """Create project, engine, and provider and verify FK relationships."""

    engine = Engine(name="engine-rel", engine_type="builtin")
    provider = Provider(
        name="provider-rel",
        protocol="anthropic",
        base_url="https://api.example.com",
        api_key="key",
        model="model",
    )
    project = Project(
        name="project-rel",
        gitlab_project_id="456",
        gitlab_access_token="token",
        webhook_secret="secret",
        engine=engine,
        provider=provider,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    assert project.engine is not None
    assert project.engine.name == "engine-rel"
    assert project.provider is not None
    assert project.provider.name == "provider-rel"
    assert engine.projects[0].name == "project-rel"
    assert provider.projects[0].name == "project-rel"


@pytest.mark.asyncio
async def test_project_rules_many_to_many_association(db_session: AsyncSession) -> None:
    """Create a project-rule association and verify both sides load it."""

    project = Project(
        name="project-rule",
        gitlab_project_id="789",
        gitlab_access_token="token",
        webhook_secret="secret",
    )
    rule = Rule(rule_id="java.npe-check", title="NPE", prompt_snippet="Check NPE")
    db_session.add_all([project, rule])
    await db_session.flush()

    association = ProjectRule(project_id=project.id, rule_id=rule.id, severity_override="BLOCKER")
    db_session.add(association)
    await db_session.commit()
    await db_session.refresh(project)
    await db_session.refresh(rule)

    assert len(project.project_rules) == 1
    assert project.project_rules[0].severity_override == "BLOCKER"
    assert len(rule.project_rules) == 1
    assert rule.project_rules[0].project_id == project.id


@pytest.mark.asyncio
async def test_block_policies_query_by_priority(db_session: AsyncSession) -> None:
    """Create policies and verify priority ordering."""

    project = Project(
        name="project-policy",
        gitlab_project_id="100",
        gitlab_access_token="token",
        webhook_secret="secret",
    )
    db_session.add(project)
    await db_session.flush()
    db_session.add_all(
        [
            ProjectBlockPolicy(
                project_id=project.id,
                branch_pattern="*",
                block_severity="NONE",
                priority=99,
            ),
            ProjectBlockPolicy(
                project_id=project.id,
                branch_pattern="master",
                block_severity="BLOCKER",
                require_all_resolved=True,
                priority=1,
            ),
        ]
    )
    await db_session.commit()

    policies = (
        await db_session.scalars(
            select(ProjectBlockPolicy)
            .where(ProjectBlockPolicy.project_id == project.id)
            .order_by(ProjectBlockPolicy.priority)
        )
    ).all()

    assert [policy.priority for policy in policies] == [1, 99]
    assert policies[0].branch_pattern == "master"
    assert policies[0].require_all_resolved is True


@pytest.mark.asyncio
async def test_review_findings_one_to_many_relationship(db_session: AsyncSession) -> None:
    """Create a review with findings and verify one-to-many relationship loading."""

    project = Project(
        name="project-review",
        gitlab_project_id="101",
        gitlab_access_token="token",
        webhook_secret="secret",
    )
    review = Review(
        project=project,
        mr_iid="1",
        source_branch="feature/a",
        target_branch="master",
        commit_sha="abc123",
        status="done",
        finding_count=2,
    )
    review.findings.extend(
        [
            Finding(
                file_path="app/a.py",
                line_number=10,
                rule_id="python.exception-handling",
                severity="WARNING",
                title="Broad exception",
            ),
            Finding(
                file_path="app/b.py",
                line_number=20,
                rule_id="general.hardcoded-secret",
                severity="BLOCKER",
                title="Secret",
            ),
        ]
    )
    db_session.add(review)
    await db_session.commit()
    await db_session.refresh(review)

    assert review.project.name == "project-review"
    assert len(review.findings) == 2
    assert {finding.severity for finding in review.findings} == {"WARNING", "BLOCKER"}


@pytest.mark.asyncio
async def test_finding_false_positive_marking_flow(db_session: AsyncSession) -> None:
    """Update finding false-positive state from NONE to PENDING to CONFIRMED."""

    project = Project(
        name="project-fp",
        gitlab_project_id="102",
        gitlab_access_token="token",
        webhook_secret="secret",
    )
    review = Review(
        project=project,
        mr_iid="2",
        source_branch="feature/fp",
        target_branch="master",
        commit_sha="def456",
    )
    finding = Finding(
        review=review,
        file_path="app/fp.py",
        rule_id="general.sql-injection",
        severity="BLOCKER",
        title="SQL injection",
    )
    db_session.add(finding)
    await db_session.commit()
    await db_session.refresh(finding)

    assert finding.fp_status == "NONE"

    finding.fp_status = "PENDING"
    finding.fp_marked_by = "developer"
    finding.fp_marked_at = datetime.now(UTC)
    finding.fp_marked_reason = "Query is parameterized upstream"
    await db_session.commit()
    await db_session.refresh(finding)

    assert finding.fp_status == "PENDING"
    assert finding.fp_marked_by == "developer"

    finding.fp_status = "CONFIRMED"
    finding.fp_reviewed_by = "admin"
    finding.fp_reviewed_at = datetime.now(UTC)
    finding.fp_review_note = "Confirmed false positive"
    await db_session.commit()
    await db_session.refresh(finding)

    assert finding.fp_status == "CONFIRMED"
    assert finding.fp_reviewed_by == "admin"
