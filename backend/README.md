# Backend

FastAPI backend service for `ai-code-reviewer`.

## Directory structure

```text
backend/
├── app/
│   ├── api/          # HTTP routers
│   ├── core/         # config, logging, database, Redis
│   └── main.py       # FastAPI application entrypoint
└── tests/            # pytest test suite
```

## Local development

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The service exposes `GET /health`, which checks PostgreSQL and Redis connectivity.
