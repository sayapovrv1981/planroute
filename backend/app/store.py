"""Хранилище поездок, пользователей и файлов документов.

Прототип: SQLite + папка uploads/. В MVP — PostgreSQL + S3-хранилище в России.
У поездки есть владелец (user_id): чужую поездку не отдаём, даже по ссылке.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(os.environ.get("SH_DATA_DIR", Path(__file__).resolve().parent.parent / "var"))
_lock = threading.Lock()


@contextmanager
def _db():
    BASE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BASE / "trips.sqlite3")
    try:
        con.execute("create table if not exists trips (id text primary key, data text not null, created text, updated text)")
        if "user_id" not in {r[1] for r in con.execute("pragma table_info(trips)")}:
            con.execute("alter table trips add column user_id integer")  # поездки до аккаунтов — без владельца
        con.execute("create table if not exists users (id integer primary key, email text unique not null, "
                    "name text not null, password text not null, created text not null)")
        con.execute("create table if not exists sessions (token text primary key, user_id integer not null, "
                    "created text not null, expires text not null)")
        yield con
        con.commit()
    finally:
        con.close()


def create(trip: dict, user_id: int) -> str:
    tid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _db() as con:
        con.execute("insert into trips (id, data, created, updated, user_id) values (?, ?, ?, ?, ?)",
                    (tid, json.dumps(trip, ensure_ascii=False), now, now, user_id))
    return tid


def get(tid: str, user_id: int) -> dict | None:
    """Поездка владельца; чужая или несуществующая — None (для пользователя это одно и то же: «не найдена»)."""
    with _lock, _db() as con:
        row = con.execute("select data from trips where id = ? and user_id = ?", (tid, user_id)).fetchone()
    return json.loads(row[0]) if row else None


def owns(tid: str, user_id: int) -> bool:
    with _lock, _db() as con:
        return con.execute("select 1 from trips where id = ? and user_id = ?", (tid, user_id)).fetchone() is not None


def list_trips(user_id: int) -> list[dict]:
    """Поездки пользователя для «Мои поездки»: свежие сверху, без программы целиком."""
    with _lock, _db() as con:
        rows = con.execute("select id, data, created, updated from trips where user_id = ? order by updated desc",
                           (user_id,)).fetchall()
    out = []
    for tid, data, created, updated in rows:
        t = json.loads(data)
        s, i = t.get("summary", {}), t.get("input", {})
        out.append({"id": tid, "title": s.get("title", ""), "city": s.get("city", ""), "dates": s.get("dates", ""),
                    "people": s.get("people", ""), "start_date": i.get("start_date"), "created": created, "updated": updated})
    return out


def delete(tid: str, user_id: int) -> bool:
    with _lock, _db() as con:
        gone = con.execute("delete from trips where id = ? and user_id = ?", (tid, user_id)).rowcount > 0
    if gone:
        shutil.rmtree(BASE / "uploads" / tid, ignore_errors=True)
    return gone


def save(tid: str, trip: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _db() as con:
        con.execute("update trips set data = ?, updated = ? where id = ?", (json.dumps(trip, ensure_ascii=False), now, tid))


def files_dir(tid: str) -> Path:
    d = BASE / "uploads" / tid
    d.mkdir(parents=True, exist_ok=True)
    return d


ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic"}
MAX_BYTES = 15 * 1024 * 1024


def save_file(tid: str, filename: str, content: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise ValueError("Подходят PDF и изображения (JPG, PNG, WEBP, HEIC)")
    if len(content) > MAX_BYTES:
        raise ValueError("Файл больше 15 МБ")
    name = uuid.uuid4().hex[:12] + ext
    (files_dir(tid) / name).write_bytes(content)
    return name


def file_path(tid: str, name: str) -> Path | None:
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    p = files_dir(tid) / name
    return p if p.is_file() else None


def pending_dir(user_id: int, token: str) -> str | None:
    """Папка билетов, загруженных в форме до создания поездки: своя у каждого пользователя."""
    return f"pending-{int(user_id)}-{token}" if re.fullmatch(r"[0-9a-f]{12,32}", token or "") else None


def adopt_pending(token: str, name: str, tid: str, user_id: int) -> str | None:
    """Файл, загруженный в форме до создания поездки, — в файлы поездки."""
    folder = pending_dir(user_id, token)
    if not folder:
        return None
    src = file_path(folder, name)
    if not src:
        return None
    src.rename(files_dir(tid) / name)
    return name


def delete_file(tid: str, name: str | None) -> None:
    if not name:
        return
    p = file_path(tid, name)
    if p:
        p.unlink(missing_ok=True)
