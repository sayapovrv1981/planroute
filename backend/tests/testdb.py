"""Временная база для тестов: импортировать первым, до модулей app.

Поднимает свой PostgreSQL (tools/devpg.py) на свободном порту, накатывает миграции и загружает каталог мест.
Живую базу и базу разработки не трогает. Уже запущенный PostgreSQL можно задать в TEST_DATABASE_URL —
тогда база в нём очищается перед тестами.
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))

import devpg  # noqa: E402

# до app.config: значения из backend/.env не перекрывают заданное здесь
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or devpg.temp_cluster()
os.environ["SH_DATA_DIR"] = tempfile.mkdtemp(prefix="sh-test-")
os.environ["STORAGE"] = "local"

import manage  # noqa: E402
from app import catalog, db  # noqa: E402

if os.environ.get("TEST_DATABASE_URL"):
    with db.tx() as con:
        db.q(con, "drop schema public cascade")
        db.q(con, "create schema public")
manage.migrate()
catalog.import_json()


def reset() -> None:
    """Пустые аккаунты и поездки; каталог остаётся."""
    from app import db

    with db.tx() as con:
        db.q(con, "truncate users, sessions, resets, trips restart identity cascade")
