"""Pydantic schemas for llm-agent execution trace events."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from schemas._datetime import AwareDatetime

# 与 models/agent_trace_event.py 的取值保持一致：
# run_started / llm_response / tool_executed / run_finished / run_failed
AgentTraceEventType = Literal[
    "run_started",
    "llm_response",
    "tool_executed",
    "run_finished",
    "run_failed",
]


class AgentTraceEventRead(BaseModel):
    """One agent execution trace event returned by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    seq: int
    turn: int | None
    event_type: AgentTraceEventType
    tool_name: str | None
    # ok / timeout / error / malformed_arguments / unknown_tool / budget_exhausted
    status: str | None
    duration_ms: int | None
    payload: dict[str, Any] | None
    created_at: AwareDatetime
