#!/usr/bin/env bash
# Сборка фронтенда и запуск сервера на http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/.."
(cd frontend && npm run build)
cd backend
# без DATABASE_URL — локальный PostgreSQL (manage.py devdb); схема и каталог мест — до старта сервера
if ! grep -qs '^DATABASE_URL=.' .env && [ -z "${DATABASE_URL:-}" ]; then
  .venv/bin/python manage.py devdb start
fi
.venv/bin/python manage.py setup
exec .venv/bin/python -m uvicorn app.main:app --reload --port 8000
