"""SQLAlchemy model for llm-agent execution trace events."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base, TimestampMixin


class AgentTraceEvent(Base, TimestampMixin):
    """One intermediate step of an llm-agent review run（工具调用/模型响应等）。

    review_id 刻意不设外键：reviews 行要到审查结束才由 _persist_review 写入，
    未注册项目甚至不写行；trace 事件在执行过程中即时插入，外键会必然失败。
    查询访问模式是"按 review_id 取一次完整轨迹"，用 (review_id, seq) 复合索引。
    """

    __tablename__ = "agent_trace_events"
    __table_args__ = (
        Index("ix_agent_trace_events_review_seq", "review_id", "seq"),
    )

    # 主键 UUID 由 Python 层生成，不依赖 PG 的 gen_random_uuid()，保证 MySQL 也可用。
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    review_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    # run 内递增序号，查询侧按它恢复事件顺序。
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # agent 循环轮次（0 起）；run_started/run_finished 等整 run 级事件为 NULL。
    turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # run_started / llm_response / tool_executed / run_finished / run_failed
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # ok / timeout / error / malformed_arguments / unknown_tool / budget_exhausted
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
