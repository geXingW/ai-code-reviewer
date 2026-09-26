"""GET /api/reviews/{review_id}/agent-trace 端点测试（需要真实数据库，CI 运行）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.admin import _sign_token
from models.agent_trace_event import AgentTraceEvent
from models.project import Project
from models.review import Review


async def _seed_review_with_trace(
    db_session_factory: async_sessionmaker,
    *,
    review_id: object = None,
) -> object:
    """落一个 Project + Review + 乱序 seq 的三条 trace 事件，返回 review_id。"""

    async with db_session_factory() as session:
        project = Project(
            name="group/demo",
            gitlab_project_id="123",
            gitlab_base_url="https://gitlab.example.com",
            gitlab_access_token="glpat-test",
            webhook_secret="test-secret",
        )
        session.add(project)
        await session.flush()

        rid = review_id or uuid4()
        review = Review(
            id=rid,
            project_id=project.id,
            mr_iid="42",
            source_branch="feature/login",
            target_branch="master",
            commit_sha="abc123",
            status="done",
            engine_used="llm-agent",
        )
        session.add(review)

        # seq 乱序插入，验证端点按 seq 升序返回。
        session.add(
            AgentTraceEvent(
                review_id=rid,
                seq=2,
                turn=0,
                event_type="tool_executed",
                tool_name="read_file",
                status="ok",
                duration_ms=10,
                payload={"output": "file content"},
            )
        )
        session.add(
            AgentTraceEvent(
                review_id=rid,
                seq=1,
                event_type="run_started",
                payload={"model": "reviewer-1", "max_turns": 8},
            )
        )
        session.add(
            AgentTraceEvent(
                review_id=rid,
                seq=3,
                event_type="run_finished",
                duration_ms=900,
                payload={"findings_count": 1},
            )
        )
        await session.commit()
        return rid


def _auth_headers() -> dict[str, str]:
    token = _sign_token("admin", datetime.now(UTC) + timedelta(hours=1))
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_agent_trace_endpoint_returns_events_in_seq_order(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    review_id = await _seed_review_with_trace(db_session_factory)

    response = await db_client.get(
        f"/api/reviews/{review_id}/agent-trace",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    events = response.json()
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert [event["event_type"] for event in events] == [
        "run_started",
        "tool_executed",
        "run_finished",
    ]
    tool_event = events[1]
    assert tool_event["tool_name"] == "read_file"
    assert tool_event["status"] == "ok"
    assert tool_event["payload"] == {"output": "file content"}
    assert tool_event["review_id"] == str(review_id)


@pytest.mark.asyncio
async def test_agent_trace_endpoint_returns_empty_for_review_without_trace(
    db_client: AsyncClient,
    db_session_factory: async_sessionmaker,
) -> None:
    """非 agent 引擎的 review 没有轨迹：200 + 空数组，而不是 404。"""

    async with db_session_factory() as session:
        project = Project(
            name="group/direct",
            gitlab_project_id="456",
            gitlab_base_url="https://gitlab.example.com",
            gitlab_access_token="glpat-test",
            webhook_secret="test-secret",
        )
        session.add(project)
        await session.flush()
        review = Review(
            project_id=project.id,
            mr_iid="7",
            source_branch="feature/x",
            target_branch="master",
            commit_sha="def456",
            status="done",
            engine_used="llm-direct",
        )
        session.add(review)
        await session.commit()
        review_id = review.id

    response = await db_client.get(
        f"/api/reviews/{review_id}/agent-trace",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_agent_trace_endpoint_404_for_unknown_review(db_client: AsyncClient) -> None:
    response = await db_client.get(
        f"/api/reviews/{uuid4()}/agent-trace",
        headers=_auth_headers(),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_agent_trace_endpoint_requires_auth(db_client: AsyncClient) -> None:
    response = await db_client.get(f"/api/reviews/{uuid4()}/agent-trace")

    assert response.status_code == 401
