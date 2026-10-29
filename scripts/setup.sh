#!/usr/bin/env bash
# Первичная установка: Python-окружение, зависимости, сборка фронтенда.
# Запуск из корня проекта: bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# На Mac часто стоят два Python: системный 3.9 (/usr/bin/python3) и новый с python.org.
# Ищем самый новый 3.11+ сами, не полагаясь на PATH.
find_python() {
  local c
  for c in python3.15 python3.14 python3.13 python3.12 python3.11 \
           /Library/Frameworks/Python.framework/Versions/3.15/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

PY="$(find_python)" || { echo "Не найден Python 3.11+. Установите его с python.org (Downloads → macOS)."; exit 1; }
echo "→ Python: $PY ($("$PY" --version))"

if ! command -v npm >/dev/null 2>&1; then
  for d in /usr/local/bin /opt/homebrew/bin; do [ -x "$d/npm" ] && export PATH="$d:$PATH"; done
fi
command -v npm >/dev/null || { echo "Не найден Node.js 20+. Установите LTS-версию с nodejs.org."; exit 1; }
echo "→ Node.js: $(node -v)"

echo "→ Python-окружение backend/.venv"
if [ -x backend/.venv/bin/python ] && ! backend/.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "  старое окружение на Python < 3.11 — пересоздаю"
  rm -rf backend/.venv
fi
"$PY" -m venv backend/.venv
backend/.venv/bin/python -m pip install -q --upgrade pip
backend/.venv/bin/python -m pip install -q -r backend/requirements.txt
# Инструменты разработки ставим отдельно: если у какого-то нет сборки под новый Python, приложение всё равно заработает
backend/.venv/bin/python -m pip install -q ruff || echo "  ruff не установился — форматирование Python будет недоступно"
backend/.venv/bin/python -m pip install -q "moto[s3]>=5" httpx2 || echo "  moto не установился — тесты хранилища S3 не запустятся"
if backend/.venv/bin/python -m pip install -q playwright; then
  backend/.venv/bin/python -m playwright install chromium || echo "  браузер для проверок не скачался — это нужно только для e2e-теста"
else
  echo "  playwright не установился — это нужно только для e2e-теста"
fi

echo "→ PostgreSQL для разработки (backend/var/postgres, порт 54329) и каталог мест"
(cd backend && .venv/bin/python manage.py devdb start && .venv/bin/python manage.py setup)

echo "→ Фронтенд"
(cd frontend && npm install --no-audit --no-fund && npm run build)

echo "→ Тесты"
(cd backend && .venv/bin/python -m unittest discover -s tests)

echo
echo "Готово. В VS Code выберите интерпретатор backend/.venv (он уже указан в настройках) и нажмите F5,"
echo "или запустите: bash scripts/run.sh — затем откройте http://localhost:8000"
