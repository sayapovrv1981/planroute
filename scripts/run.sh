#!/usr/bin/env bash
# Сборка фронтенда и запуск сервера на http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/.."
(cd frontend && npm run build)
cd backend && exec .venv/bin/python -m uvicorn app.main:app --reload --port 8000
