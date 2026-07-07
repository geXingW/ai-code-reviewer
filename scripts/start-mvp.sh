#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but was not found in PATH." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required but was not found." >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "Python 3 is required to initialize .env secrets (install python3 or python-is-python3)." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import base64
import os
import secrets

path = Path('.env')
content = path.read_text(encoding='utf-8')
fernet_key = base64.urlsafe_b64encode(os.urandom(32)).decode('ascii')
content = content.replace('CHANGE_ME_GENERATE_A_REAL_FERNET_KEY', fernet_key)
content = content.replace('CHANGE_ME_RANDOM_WEBHOOK_SECRET', secrets.token_urlsafe(32))
content = content.replace('CHANGE_ME_RANDOM_INTERNAL_TOKEN', secrets.token_urlsafe(32))
path.write_text(content, encoding='utf-8')
PY
  chmod 600 .env
  echo "Created .env from .env.example. Please review GitLab token/base URL before real MR review."
fi

docker compose up -d --build

echo "MVP stack is starting."
echo "Dashboard: http://localhost:5173"
echo "Health:    http://localhost:8000/health"
