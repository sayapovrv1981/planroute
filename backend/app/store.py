"""Хранилище поездок и файлов документов.

Прототип: SQLite + папка uploads/. В MVP — PostgreSQL + S3-хранилище в России.
"""
from __future__ import annotations

import json
import re
import os
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
        yield con
        con.commit()
    finally:
        con.close()


def create(trip: dict) -> str:
    tid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _db() as con:
        con.execute("insert into trips values (?, ?, ?, ?)", (tid, json.dumps(trip, ensure_ascii=False), now, now))
    return tid


def get(tid: str) -> dict | None:
    with _lock, _db() as con:
        row = con.execute("select data from trips where id = ?", (tid,)).fetchone()
    return json.loads(row[0]) if row else None


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


def adopt_pending(token: str, name: str, tid: str) -> str | None:
    """Файл, загруженный в форме до создания поездки (папка pending-<token>), — в файлы поездки."""
    if not re.fullmatch(r"[0-9a-f]{12,32}", token or ""):
        return None
    src = file_path(f"pending-{token}", name)
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
