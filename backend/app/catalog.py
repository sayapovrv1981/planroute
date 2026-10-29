"""Каталог мест.

Главная копия — JSON-файлы по городам в git (data/<город>_places.json): их правят руками и скриптами
(tools/yandex_facts.py). В базу их загружает `manage.py import-catalog` (таблицы cities и places),
сервер читает каталог из базы и держит в памяти до перезапуска.
Город называем по имени файла (spb_places.json) — так он записан в поездках.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import JSON, bindparam, text

from . import db

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _key(name: str) -> str:
    return (name or "").strip().lower().replace("ё", "е")


@lru_cache(maxsize=1)
def _cities() -> dict[str, dict]:
    with db.tx() as con:
        rows = db.q(con, "select file, data from cities order by file").all()
    if not rows:
        raise RuntimeError("Каталог мест в базе пуст: выполните manage.py import-catalog")
    return {file: data for file, data in rows}


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """Название города и его синонимы (поле aliases) -> имя каталога (spb_places.json)."""
    out = {}
    for file, data in _cities().items():
        for name in [data["city"], *data.get("aliases", [])]:
            out[_key(name)] = file
    return out


def normalize_city(name: str) -> str | None:
    return _index().get(_key(name))


def cities() -> list[str]:
    return sorted({data["city"] for data in _cities().values()})


@lru_cache(maxsize=8)
def load(filename: str) -> dict:
    with db.tx() as con:
        rows = db.q(con, "select data from places where city_file = :f order by pos", f=filename).scalars().all()
    data = dict(_cities()[filename], places=list(rows))
    data["by_id"] = {p["id"]: p for p in data["places"]}
    return data


def reset_cache() -> None:
    for f in (_cities, _index, load):
        f.cache_clear()


def import_json(data_dir: Path = DATA_DIR) -> dict[str, int]:
    """data/*_places.json -> база. Города и места, которых больше нет в файлах, удаляются. Всё в одной транзакции:
    сервер, читающий каталог в этот момент, видит его целиком старым или целиком новым."""
    counts = {}
    with db.tx() as con:
        files = sorted(data_dir.glob("*_places.json"))
        db.q(con, "delete from cities where not (file = any(:files))", files=[p.name for p in files])
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            places = data.pop("places")
            ids = [p["id"] for p in places]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{path.name}: повторяются id мест")
            con.execute(text("insert into cities (file, data) values (:f, :data) "
                             "on conflict (file) do update set data = excluded.data")
                        .bindparams(bindparam("data", type_=JSON)), {"f": path.name, "data": data})
            db.q(con, "delete from places where city_file = :f", f=path.name)
            con.execute(text("insert into places (city_file, id, pos, data) values (:f, :id, :pos, :data)")
                        .bindparams(bindparam("data", type_=JSON)),
                        [{"f": path.name, "id": p["id"], "pos": i, "data": p} for i, p in enumerate(places)])
            counts[path.name] = len(places)
    reset_cache()
    return counts


def maps_link(name: str, city: str) -> str:
    return "https://yandex.ru/maps/?text=" + quote(f"{name}, {city}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
