# Backend

FastAPI backend service for `ai-code-reviewer`.

## Directory structure

```text
backend/
├── main.py            # FastAPI application factory + ASGI entrypoint
├── app.py             # Startup script (python app.py)
├── __main__.py        # Module entrypoint (python -m ...)
├── api/               # HTTP routers
├── core/              # config, logging, database
├── engines/           # ReviewEngine abstraction + LLMEngine
├── integrations/      # GitLab / DingTalk clients
├── llm/               # LLM provider abstraction
├── models/            # SQLAlchemy ORM models
├── repositories/      # Data access layer (async)
├── schemas/           # Pydantic v2 schemas
├── services/          # Review orchestrator + notifications
├── sql/               # 建库 SQL（由 scripts/export_schema_sql.py 生成，手动执行）
├── scripts/           # seed.py / seed_rules.py / export_schema_sql.py
├── tests/             # pytest test suite
├── requirements.txt        # Production dependencies
├── requirements-dev.txt    # Dev dependencies (test + lint + type-check)
├── pytest.ini              # pytest configuration
├── ruff.toml               # ruff linter configuration
└── mypy.ini                # mypy type-checker configuration
```

## Local development

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python app.py --reload
```

The service exposes `GET /health`, which checks database connectivity.

## Configuration

Review runtime options are read from environment variables (see the
root `.env.example` for the full list):

- `DEFAULT_REVIEW_ENGINE` — `llm-direct` (single-shot diff review,
  default) or `llm-agent` (multi-turn tool-calling agent that can
  investigate the impact scope via read-only GitLab API tools; higher
  token cost).
- `AGENT_MAX_TURNS` (default `8`) — max tool-calling rounds for
  `llm-agent`.
- `AGENT_TOOL_RESULT_MAX_CHARS` (default `8000`) — per-tool-result
  truncation limit.
- `AGENT_TOTAL_CONTEXT_MAX_CHARS` (default `120000`) — total
  observation budget for one agent session.
- `AGENT_TOOL_TIMEOUT_SECONDS` (default `15`) — per-tool execution
  timeout.

## Architecture docs

- [Review Engine Architecture](docs/engine_architecture.md)
- [GitLab Webhook MVP](docs/gitlab_webhook_mvp.md)
- [LLM Provider Abstraction](docs/llm_providers.md)
