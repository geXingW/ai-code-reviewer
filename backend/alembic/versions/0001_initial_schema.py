"""Initial schema for data model layer."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamp_columns() -> list[sa.Column[sa.DateTime]]:
    """Return standard timestamp columns shared by all tables."""

    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    """Create all application data model tables."""

    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "engines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("engine_type", sa.String(length=50), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "providers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("protocol", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("temperature", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_tokens", sa.Integer(), server_default=sa.text("4096"), nullable=False),
        sa.Column("extra_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("prompt_snippet", sa.Text(), nullable=False),
        sa.Column("severity_default", sa.String(length=20), server_default=sa.text("'WARNING'"), nullable=False),
        sa.Column("languages", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("path_patterns", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("grace_period_until", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id"),
    )
    op.create_index(op.f("ix_rules_rule_id"), "rules", ["rule_id"], unique=False)

    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("gitlab_project_id", sa.String(length=255), nullable=False),
        sa.Column("gitlab_access_token", sa.Text(), nullable=False),
        sa.Column("webhook_secret", sa.Text(), nullable=False),
        sa.Column("engine_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
        sa.Column("max_files", sa.Integer(), server_default=sa.text("50"), nullable=False),
        sa.Column("ignore_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("default_block_severity", sa.String(length=30), server_default=sa.text("'BLOCKER'"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["engine_id"], ["engines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=255), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "project_rules",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("severity_override", sa.String(length=20), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "rule_id"),
    )

    op.create_table(
        "project_block_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("branch_pattern", sa.String(length=255), nullable=False),
        sa.Column("block_severity", sa.String(length=30), nullable=False),
        sa.Column("block_on_engine_error", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("require_all_resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mr_iid", sa.String(length=255), nullable=False),
        sa.Column("source_branch", sa.String(length=255), nullable=False),
        sa.Column("target_branch", sa.String(length=255), nullable=False),
        sa.Column("commit_sha", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("engine_used", sa.String(length=255), nullable=True),
        sa.Column("provider_used", sa.String(length=255), nullable=True),
        sa.Column("policy_applied", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("has_blocker", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("finding_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("raw_llm_output", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["policy_applied"], ["project_block_policies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "review_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.String(length=2048), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("rule_id", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("existing_code", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("gitlab_discussion_id", sa.String(length=255), nullable=True),
        sa.Column("fp_status", sa.String(length=20), server_default=sa.text("'NONE'"), nullable=False),
        sa.Column("fp_marked_by", sa.String(length=255), nullable=True),
        sa.Column("fp_marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fp_marked_reason", sa.Text(), nullable=True),
        sa.Column("fp_reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("fp_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fp_review_note", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "negative_examples",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code_snippet", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("source_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_finding_id"], ["review_findings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Drop all application data model tables."""

    op.drop_table("negative_examples")
    op.drop_table("review_findings")
    op.drop_table("reviews")
    op.drop_table("project_block_policies")
    op.drop_table("project_rules")
    op.drop_table("audit_logs")
    op.drop_table("projects")
    op.drop_index(op.f("ix_rules_rule_id"), table_name="rules")
    op.drop_table("rules")
    op.drop_table("providers")
    op.drop_table("engines")
