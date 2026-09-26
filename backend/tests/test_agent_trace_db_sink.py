"""DbSink 落库行为单测：注入假 session factory，不依赖真实数据库。

覆盖两条关键路径：
- 正常写入：事件 dict 被映射成 AgentTraceEvent 行并提交；
- 失败闩锁：写库抛异常后只告警一次，本进程后续 emit 静默跳过（fail-open）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from core import db
from engines.llm_agent.trace import DbSink
from models.agent_trace_event import AgentTraceEvent


@dataclass
class _FakeSession:
    """极简 async session 替身：记录 add 的行，commit 可配置失败。"""

    rows: list[Any] = field(default_factory=list)
    fail_on_commit: bool = False
    committed: int = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def add(self, row: Any) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        if self.fail_on_commit:
            raise RuntimeError("db write failed")
        self.committed += 1


def _sample_event(seq: int = 1) -> dict[str, Any]:
    return {
        "review_id": str(uuid4()),
        "seq": seq,
        "event_type": "tool_executed",
        "turn": 0,
        "tool_name": "read_file",
        "status": "ok",
        "duration_ms": 12,
        "payload": {"call_id": "call-1", "output": "content"},
    }


@pytest.mark.asyncio
async def test_db_sink_writes_row(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()

    def factory() -> _FakeSession:
        return session

    monkeypatch.setattr(db, "AsyncSessionLocal", factory, raising=False)
    sink = DbSink()

    await sink.emit(_sample_event())

    assert len(session.rows) == 1
    row = session.rows[0]
    assert isinstance(row, AgentTraceEvent)
    assert isinstance(row.review_id, UUID)
    assert row.seq == 1
    assert row.event_type == "tool_executed"
    assert row.turn == 0
    assert row.tool_name == "read_file"
    assert row.status == "ok"
    assert row.duration_ms == 12
    assert row.payload == {"call_id": "call-1", "output": "content"}
    assert session.committed == 1


@pytest.mark.asyncio
async def test_db_sink_latches_after_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """写库失败 -> 告警一次并闩锁；后续 emit 静默跳过，不再抛异常。"""

    def factory() -> _FakeSession:
        return _FakeSession(fail_on_commit=True)

    monkeypatch.setattr(db, "AsyncSessionLocal", factory, raising=False)
    sink = DbSink()

    with caplog.at_level("WARNING"):
        await sink.emit(_sample_event(seq=1))
        await sink.emit(_sample_event(seq=2))
        await sink.emit(_sample_event(seq=3))

    assert sink._disabled is True
    warnings = [record for record in caplog.records if "db sink disabled" in record.message]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_db_sink_disabled_when_session_factory_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """db.AsyncSessionLocal 不存在（如被测试移除）时闩锁关闭而非抛错。"""

    monkeypatch.delattr(db, "AsyncSessionLocal", raising=False)
    sink = DbSink()

    with caplog.at_level("WARNING"):
        await sink.emit(_sample_event())

    assert sink._disabled is True
