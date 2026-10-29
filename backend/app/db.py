"""PostgreSQL: подключение и схема таблиц.

SQLAlchemy Core без ORM: запросы — SQL-текстом с именованными параметрами (`:name`). Схему меняют только
миграции Alembic (backend/migrations), описание таблиц здесь — для них и для тестов.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import (JSON, BigInteger, Column, DateTime, ForeignKey, Identity, Index, MetaData, Table, Text,
                        create_engine, text)
from sqlalchemy.engine import Connection, Engine

from . import config

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("password", Text, nullable=False),
    Column("created", DateTime(timezone=True), nullable=False),
)

sessions = Table(  # token — sha256 от значения cookie, само значение не храним
    "sessions", metadata,
    Column("token", Text, primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created", DateTime(timezone=True), nullable=False),
    Column("expires", DateTime(timezone=True), nullable=False, index=True),
)

resets = Table(
    "resets", metadata,
    Column("token", Text, primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created", DateTime(timezone=True), nullable=False),
    Column("expires", DateTime(timezone=True), nullable=False),
    Column("used", DateTime(timezone=True)),
)

trips = Table(  # user_id пустой — поездка из прототипа до аккаунтов, достаётся первому пользователю
    "trips", metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE")),
    Column("data", JSON, nullable=False),
    Column("created", DateTime(timezone=True), nullable=False),
    Column("updated", DateTime(timezone=True), nullable=False),
    Index("trips_user_updated", "user_id", "updated"),
)

cities = Table(  # file — прежнее имя каталога (spb_places.json): по нему город записан в поездках
    "cities", metadata,
    Column("file", Text, primary_key=True),
    Column("data", JSON, nullable=False),  # всё, кроме мест: city, aliases, center, arrival_points…
)

places = Table(
    "places", metadata,
    Column("city_file", Text, ForeignKey("cities.file", ondelete="CASCADE"), primary_key=True),
    Column("id", Text, primary_key=True),
    Column("pos", BigInteger, nullable=False),  # порядок в JSON: от него зависит выбор планировщика
    Column("data", JSON, nullable=False),
)

_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()


def engine(url: str | None = None) -> Engine:
    url = url or config.get().database_url
    with _engines_lock:
        if url not in _engines:
            _engines[url] = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10,
                                          json_serializer=lambda o: json.dumps(o, ensure_ascii=False))
        return _engines[url]


@contextmanager
def tx() -> Iterator[Connection]:
    """Соединение в транзакции: commit при выходе, rollback при исключении."""
    with engine().begin() as con:
        yield con


def q(con: Connection, sql: str, **params: Any):
    return con.execute(text(sql), params)
