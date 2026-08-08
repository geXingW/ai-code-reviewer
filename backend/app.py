"""Startup entrypoint for ai-code-reviewer backend.

Loads ``.env`` from the current working directory (if present) and starts
the FastAPI application with uvicorn.

Usage::

    python app.py                       # start on 0.0.0.0:8000, load ./.env
    python app.py --port 9000           # custom port
    python app.py --host 127.0.0.1      # bind to localhost only
    python app.py --env-file /path/.env
    python app.py --reload              # development auto-reload
    python app.py --migrate             # run alembic upgrade head before starting
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_env_file(env_file: Path) -> None:
    """Load KEY=VALUE pairs from a dotenv file into os.environ."""
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python app.py",
        description="Start the ai-code-reviewer FastAPI server.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0, or $HOST if set)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Port to bind to (default: 8000, or $PORT if set)",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: ./.env; set to empty string to disable)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development only)",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Run 'alembic upgrade head' before starting the server",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of uvicorn workers (default: 1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.env_file:
        env_path = Path(args.env_file)
        if env_path.is_file():
            _load_env_file(env_path)
            print(f"Loaded environment from {env_path.resolve()}")
        elif args.env_file != ".env":
            print(f"Warning: env file not found: {env_path}", file=sys.stderr)

    if args.migrate:
        try:
            from alembic.config import Config as AlembicConfig

            from alembic import command

            alembic_ini = Path("alembic.ini")
            if not alembic_ini.is_file():
                print("Warning: alembic.ini not found, skipping migration", file=sys.stderr)
            else:
                print("Running database migrations...")
                alembic_cfg = AlembicConfig(str(alembic_ini.resolve()))
                command.upgrade(alembic_cfg, "head")
                print("Migrations complete")
        except ImportError:
            print("Warning: alembic not installed, skipping migration", file=sys.stderr)

    import uvicorn

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
