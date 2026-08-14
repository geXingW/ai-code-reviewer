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
├── alembic/           # Database migrations
├── scripts/           # seed.py / seed_rules.py / generate_release_sql.py
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

## Architecture docs

- [Review Engine Architecture](docs/engine_architecture.md)
- [GitLab Webhook MVP](docs/gitlab_webhook_mvp.md)
- [LLM Provider Abstraction](docs/llm_providers.md)
