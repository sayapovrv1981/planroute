"""Хранилище поездок и файлов документов.

Поездки — в PostgreSQL (app/db.py), файлы — в app/storage.py (диск или S3).
У поездки есть владелец (user_id): чужую поездку не отдаём, даже по ссылке.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, bindparam, text

from . import config, db, storage

_DATA = bindparam("data", type_=JSON)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(trip: dict, user_id: int) -> str:
    tid = uuid.uuid4().hex[:12]
    now = _now()
    with db.tx() as con:
        con.execute(text("insert into trips (id, data, created, updated, user_id) values (:tid, :data, :now, :now, :uid)")
                    .bindparams(_DATA), {"tid": tid, "data": trip, "now": now, "uid": user_id})
    return tid


def get(tid: str, user_id: int) -> dict | None:
    """Поездка владельца; чужая или несуществующая — None (для пользователя это одно и то же: «не найдена»)."""
    with db.tx() as con:
        row = db.q(con, "select data from trips where id = :tid and user_id = :uid", tid=tid, uid=user_id).first()
    return row[0] if row else None


def owns(tid: str, user_id: int) -> bool:
    with db.tx() as con:
        return db.q(con, "select 1 from trips where id = :tid and user_id = :uid", tid=tid, uid=user_id).first() is not None


def list_trips(user_id: int) -> list[dict]:
    """Поездки пользователя для «Мои поездки»: свежие сверху, без программы целиком."""
    with db.tx() as con:
        rows = db.q(con, "select id, data->'summary', data->'input'->'start_date', created, updated from trips "
                         "where user_id = :uid order by updated desc", uid=user_id).all()
    out = []
    for tid, s, start, created, updated in rows:
        s = s or {}
        out.append({"id": tid, "title": s.get("title", ""), "city": s.get("city", ""), "dates": s.get("dates", ""),
                    "people": s.get("people", ""), "start_date": start,
                    "created": created.isoformat(), "updated": updated.isoformat()})
    return out


def delete(tid: str, user_id: int) -> bool:
    with db.tx() as con:
        gone = db.q(con, "delete from trips where id = :tid and user_id = :uid", tid=tid, uid=user_id).rowcount > 0
    if gone:
        storage.get().delete_folder(tid)
    return gone


def save(tid: str, trip: dict) -> None:
    with db.tx() as con:
        con.execute(text("update trips set data = :data, updated = :now where id = :tid").bindparams(_DATA),
                    {"data": trip, "now": _now(), "tid": tid})


ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic"}


def max_bytes() -> int:
    return config.get().max_file_mb * 1024 * 1024


def _safe(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and not name.startswith(".")


def save_file(folder: str, filename: str, content: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise ValueError("Подходят PDF и изображения (JPG, PNG, WEBP, HEIC)")
    if len(content) > max_bytes():
        raise ValueError(f"Файл больше {config.get().max_file_mb} МБ")
    name = uuid.uuid4().hex[:12] + ext
    storage.get().put(f"{folder}/{name}", content)
    return name


def read_file(folder: str, name: str) -> bytes | None:
    return storage.get().get(f"{folder}/{name}") if _safe(folder) and _safe(name) else None


def pending_dir(user_id: int, token: str) -> str | None:
    """Папка билетов, загруженных в форме до создания поездки: своя у каждого пользователя."""
    return f"pending-{int(user_id)}-{token}" if re.fullmatch(r"[0-9a-f]{12,32}", token or "") else None


def adopt_pending(token: str, name: str, tid: str, user_id: int) -> str | None:
    """Файл, загруженный в форме до создания поездки, — в файлы поездки."""
    folder = pending_dir(user_id, token)
    if not folder or not _safe(name):
        return None
    return name if storage.get().move(f"{folder}/{name}", f"{tid}/{name}") else None


def delete_file(tid: str, name: str | None) -> None:
    if name and _safe(name):
        storage.get().delete(f"{tid}/{name}")
