"""Каталог мест.

В прототипе — черновые JSON-файлы по городам (data/<город>_places.json). В MVP этот модуль
заменяется клиентом API поиска по организациям Яндекса (с кешем до 30 дней),
интерфейс (get / all / by_category) остаётся тем же.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _key(name: str) -> str:
    return (name or "").strip().lower().replace("ё", "е")


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """Название города и его синонимы (поле aliases) -> файл каталога data/*_places.json."""
    out = {}
    for path in sorted(DATA_DIR.glob("*_places.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for name in [data["city"], *data.get("aliases", [])]:
            out[_key(name)] = path.name
    return out


def normalize_city(name: str) -> str | None:
    return _index().get(_key(name))


def cities() -> list[str]:
    return sorted({load(fn)["city"] for fn in _index().values()})


@lru_cache(maxsize=8)
def load(filename: str) -> dict:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        data = json.load(f)
    data["by_id"] = {p["id"]: p for p in data["places"]}
    return data


def maps_link(name: str, city: str) -> str:
    return "https://yandex.ru/maps/?text=" + quote(f"{name}, {city}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
