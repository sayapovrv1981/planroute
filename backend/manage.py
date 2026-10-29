"""Команды обслуживания: запуск из папки backend, `.venv/bin/python manage.py <команда>`.

  devdb start|stop|status   локальный PostgreSQL для разработки (backend/var/postgres, порт 54329)
  migrate                   схема базы до последней миграции Alembic
  import-catalog            data/*_places.json -> таблицы cities и places
  setup                     migrate + import-catalog: всё, что нужно базе перед запуском сервера
  sqlite-to-pg [файл]       поездки и аккаунты из прежней SQLite (по умолчанию var/trips.sqlite3) в PostgreSQL
  files-to-s3 [папка]       файлы документов из прежней папки (по умолчанию var/uploads) в S3 по настройкам из env

Адрес базы — DATABASE_URL (без него — локальная база из devdb), хранилище — STORAGE и S3_* (см. .env.example).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from app import config

BACKEND = Path(__file__).resolve().parent
DEV_DIR = BACKEND / "var" / "postgres"


def devdb(action: str) -> None:
    sys.path.insert(0, str(BACKEND / "tools"))
    import devpg

    if action == "start":
        devpg.start(DEV_DIR, config.DEV_DB_PORT)
        devpg.create_db(config.DEV_DB_PORT, "tripplan")
        print(f"PostgreSQL запущен: {devpg.url(config.DEV_DB_PORT, 'tripplan')}")
    elif action == "stop":
        devpg.stop(DEV_DIR)
        print("PostgreSQL остановлен")
    elif action == "status":
        print("работает" if (DEV_DIR / "PG_VERSION").is_file() and devpg.running(DEV_DIR) else "не запущен")
    else:
        raise SystemExit("devdb start | stop | status")


def migrate(url: str | None = None) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def import_catalog() -> None:
    from app import catalog

    for name, n in catalog.import_json().items():
        print(f"{name}: {n} мест")


def _ts(value: str | None) -> datetime:
    """Время из SQLite — ISO-строка; у поездок из самого первого прототипа его может не быть."""
    try:
        t = datetime.fromisoformat(value or "")
    except ValueError:
        return datetime.now(timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def sqlite_to_pg(path: str | None = None) -> None:
    """Перенос один раз при переезде: в PostgreSQL ещё не должно быть аккаунтов, иначе id пользователей разойдутся."""
    from sqlalchemy import JSON, bindparam, text

    from app import db

    src = Path(path) if path else config.get().data_dir / "trips.sqlite3"
    if not src.is_file():
        raise SystemExit(f"Нет файла {src}")
    old = sqlite3.connect(src)
    tables = {r[0] for r in old.execute("select name from sqlite_master where type = 'table'")}
    users = old.execute("select id, email, name, password, created from users").fetchall() if "users" in tables else []
    sessions = (old.execute("select token, user_id, created, expires from sessions").fetchall()
                if "sessions" in tables else [])
    cols = {r[1] for r in old.execute("pragma table_info(trips)")}
    trips = old.execute("select id, data, created, updated, " + ("user_id" if "user_id" in cols else "null")
                        + " from trips").fetchall()
    with db.tx() as con:
        if db.q(con, "select count(*) from users").scalar() or db.q(con, "select count(*) from trips").scalar():
            raise SystemExit("В PostgreSQL уже есть аккаунты или поездки — перенос делается в пустую базу")
        for uid, email, name, password, created in users:
            db.q(con, "insert into users (id, email, name, password, created) overriding system value "
                      "values (:id, :email, :name, :pw, :created)",
                 id=uid, email=email, name=name, pw=password, created=_ts(created))
        if users:  # следующий зарегистрированный получит id после перенесённых
            db.q(con, "select setval(pg_get_serial_sequence('users', 'id'), (select max(id) from users))")
        now = datetime.now(timezone.utc)
        live = [s for s in sessions if _ts(s[3]) > now]  # вошедшие остаются в аккаунте
        for token, uid, created, expires in live:
            db.q(con, "insert into sessions (token, user_id, created, expires) values (:t, :uid, :c, :e)",
                 t=token, uid=uid, c=_ts(created), e=_ts(expires))
        stmt = text("insert into trips (id, user_id, data, created, updated) values (:id, :uid, :data, :c, :u)"
                    ).bindparams(bindparam("data", type_=JSON))
        for tid, data, created, updated, uid in trips:
            con.execute(stmt, {"id": tid, "uid": uid, "data": json.loads(data), "c": _ts(created), "u": _ts(updated)})
    print(f"Перенесено: {len(users)} аккаунтов, {len(live)} действующих сессий, {len(trips)} поездок")


def files_to_s3(path: str | None = None) -> None:
    from app import storage

    s = config.get()
    if s.storage != "s3":
        raise SystemExit("Задайте STORAGE=s3 и S3_* — файлы переносятся в S3 по этим настройкам")
    src = Path(path) if path else s.data_dir / "uploads"
    target = storage.get()
    n = 0
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        target.put(f.relative_to(src).as_posix(), f.read_bytes())
        n += 1
    print(f"В S3 загружено файлов: {n}. Папку {src} можно удалить после проверки.")


def main(argv: list[str]) -> None:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "devdb":
        devdb(args[0] if args else "")
    elif cmd == "migrate":
        migrate()
    elif cmd == "import-catalog":
        import_catalog()
    elif cmd == "setup":
        migrate()
        import_catalog()
    elif cmd == "sqlite-to-pg":
        sqlite_to_pg(*args[:1])
    elif cmd == "files-to-s3":
        files_to_s3(*args[:1])
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
