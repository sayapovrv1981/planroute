"""Рейтинг и часы работы кафе и ресторанов каталога по Яндекс Картам.

Работа в два шага, потому что поиск по названию находит и соседние заведения:

1. `.venv/bin/python -I tools/yandex_facts.py data/spb_places.json` — печатает кандидатов рядом с
   координатами места (id, название, адрес, рейтинг, часы). Глазами выбираем нужный id и пишем его
   в каталог полем `ymaps_id`.
2. `.venv/bin/python -I tools/yandex_facts.py data/spb_places.json --write` — по `ymaps_id` берёт
   страницу организации и обновляет в каталоге `rating`, `reviews` и `hours`. Места без `ymaps_id`
   и заглушки (`placeholder`) пропускает.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import yandexmaps  # noqa: E402
from app.catalog import haversine_km  # noqa: E402


def cards(state: dict) -> list[dict]:
    """Все карточки организаций со страницы поиска: в выдаче их десяток, нужная — не первая."""
    out, stack = [], [state]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "title" in o and "coordinates" in o and "categories" in o:
                out.append(o)
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return out


def facts(card: dict, p: dict) -> dict:
    lon, lat = card["coordinates"][:2]
    r = card.get("ratingData") or {}
    return {"title": card["title"], "address": card.get("address", ""), "id": str(card.get("id") or ""),
            "km": round(haversine_km(float(lat), float(lon), p["lat"], p["lon"]), 2),
            "rating": round(float(r["ratingValue"]), 1) if r.get("ratingValue") else None,
            "reviews": int(r.get("reviewCount") or 0), "hours": yandexmaps.hours(card.get("workingTime")),
            "closed": card.get("status") in ("permanent-closed", "temporary-closed")}


def candidates(p: dict, city: str) -> list[dict]:
    """Заведения Яндекс Карт рядом с координатами места: поиск по названию, карту центрируем на точке."""
    query = urllib.parse.quote(f"{re.sub(r'[«»]', '', p['name'])} {city}")
    page, _ = yandexmaps.fetch(f"https://yandex.ru/maps/?ll={p['lon']}%2C{p['lat']}&z=17&text={query}")
    try:
        found = [facts(c, p) for c in cards(yandexmaps._state(page))]
    except ValueError:
        return []
    seen, out = set(), []
    for f in sorted(found, key=lambda f: f["km"]):
        if f["km"] <= 0.4 and f["id"] and f["id"] not in seen:
            seen.add(f["id"])
            out.append(f)
    return out


def by_id(p: dict) -> dict | None:
    """Страница организации по ymaps_id из каталога: там рейтинг и часы точно того заведения."""
    for pause in (3, 10, 30):  # на частые запросы Карты отвечают ошибкой — ждём и пробуем снова
        time.sleep(pause)
        try:
            page, _ = yandexmaps.fetch(f"https://yandex.ru/maps/org/{p['ymaps_id']}/")
            card = yandexmaps._card(yandexmaps._state(page), str(p["ymaps_id"]))
        except ValueError:
            continue
        return facts(card, p) if card else None
    return None


def main(path: str, write: bool) -> None:
    file = Path(path)
    data = json.loads(file.read_text(encoding="utf-8"))
    city = data["city"]
    for p in data["places"]:
        if p.get("category") != "food" or p.get("placeholder"):
            continue
        if not write:
            print(f"\n{p['id']} · {p['name']} · часы в каталоге {p.get('hours')} · ymaps_id {p.get('ymaps_id')}")
            for f in candidates(p, city):
                print(f"   {f['id']} · {f['title']} · {f['address']} · {f['km']} км · "
                      f"★ {f['rating']} ({f['reviews']}) · часы {f['hours']}" + (" · ЗАКРЫТО" if f["closed"] else ""))
            continue
        if not p.get("ymaps_id"):
            print(f"— {p['id']}: нет ymaps_id, пропускаем")
            continue
        found = by_id(p)
        if not found:
            print(f"— {p['id']}: Карты не отдали карточку {p['ymaps_id']}")
            continue
        if found["closed"]:
            print(f"! {p['id']}: заведение {found['title']} закрыто, замените его в каталоге")
            continue
        print(f"{p['id']}: {found['title']} · ★ {found['rating']} ({found['reviews']}) · "
              f"часы {found['hours']} (было {p.get('hours')})")
        if found["rating"]:
            p["rating"], p["reviews"] = found["rating"], found["reviews"]
        if found["hours"]:
            p["hours"] = found["hours"]
    if write:
        # каталоги храним с отступом в 1 пробел и без перевода строки в конце (см. CLAUDE.md)
        file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"записано в {file}")


if __name__ == "__main__":
    main(sys.argv[1], "--write" in sys.argv)
