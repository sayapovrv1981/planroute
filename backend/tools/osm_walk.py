"""Пешие расстояния между местами каталога по улицам OpenStreetMap.

Для каждого города — одна таблица «каждое место с каждым» (сервис table бесплатного OSRM-сервера
routing.openstreetmap.de, профиль foot): учитывает реки, мосты, ж/д пути и обходы, которых нет в расстоянии
по прямой. Результат — data/walk/<город>.json, его читает planner.transfer. Ключ точки — координаты
с 5 знаками («55.10400,38.75400»), поэтому таблица подходит и к местам, и к пунктам программы, и к вокзалам.

    .venv/bin/python -I tools/osm_walk.py                       # все города
    .venv/bin/python -I tools/osm_walk.py data/kolomna_places.json

Запускать после правки каталога (новые места или координаты); места без таблицы считаются по-старому.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.net import ssl_context  # noqa: E402

OSRM = "https://routing.openstreetmap.de/routed-foot/table/v1/foot/"
DATA = Path(__file__).resolve().parents[1] / "data"
MAX_POINTS = 100  # предел таблицы публичного сервера


def key(lat: float, lon: float) -> str:
    return f"{lat:.5f},{lon:.5f}"


def points(catalog: dict) -> list[tuple[float, float]]:
    pts = [(p["lat"], p["lon"]) for p in catalog["places"] if p.get("lat") is not None]
    pts += [(a["lat"], a["lon"]) for a in (catalog.get("arrival_points") or {}).values() if a.get("lat") is not None]
    return list(dict.fromkeys((round(a, 5), round(b, 5)) for a, b in pts))


def table(pts: list[tuple[float, float]]) -> list[list[float | None]]:
    coords = ";".join(f"{lon},{lat}" for lat, lon in pts)
    req = urllib.request.Request(f"{OSRM}{coords}?annotations=distance",
                                 headers={"User-Agent": "planroute.ru (trip planner; catalog prep)"})
    for pause in (0, 10, 30):
        time.sleep(pause)
        try:
            with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as r:
                data = json.loads(r.read())
            if data.get("code") == "Ok":
                return data["distances"]
            print(f"  OSRM: {data.get('code')} {data.get('message')}")
        except OSError as e:
            print(f"  OSRM: {type(e).__name__}: {e}")
    raise SystemExit("OSRM не ответил — попробуйте позже")


def build(path: Path) -> None:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    pts = points(catalog)
    if len(pts) > MAX_POINTS:
        raise SystemExit(f"{path.name}: {len(pts)} точек, больше {MAX_POINTS} — разбейте запрос")
    dist = table(pts)
    out = {"source": "OpenStreetMap, routing.openstreetmap.de (foot)", "points": [key(*p) for p in pts],
           "m": [[None if d is None else round(d) for d in row] for row in dist]}
    dest = DATA / "walk" / path.name.replace("_places.json", ".json")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.name}: {len(pts)} точек -> {dest.relative_to(DATA.parent)}")


if __name__ == "__main__":
    files = [Path(a) for a in sys.argv[1:]] or sorted(DATA.glob("*_places.json"))
    for f in files:
        build(f)
        time.sleep(2)  # бесплатный сервер — не частим
