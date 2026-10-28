"""Экскурсии Трипстера к местам программы (партнёрский API 2.0).

Документация: https://tripster.atlassian.net/wiki/spaces/affiliates/pages/3688235009/Tripster+API+2.0
Ключи в backend/.env: TRIPSTER_PARTNER (идентификатор партнёра) и TRIPSTER_SECRET (ключ для токена).

Каталог экскурсий города загружается целиком (страницами по 100) и хранится на сутки в памяти
и в backend/var/tripster/ — лимит API 3000 запросов в час, а страница поездки не должна ждать Трипстер.
Устаревший каталог обновляется в фоне; пока его нет, экскурсий у пунктов просто не показываем.
Координат у экскурсий нет, поэтому к месту подбираем по названию: «Кремль» -> экскурсии по Кремлю.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import catalog, net, store

BASE = "https://experience.tripster.ru"
TTL = 24 * 3600
TIMEOUT = 30
MAX_PAGES = 30  # 3000 экскурсий — больше в одном городе не бывает, а запросов к API не больше 30 в сутки
PER_PLACE = 3

_lock = threading.Lock()
_token: str | None = None
_cache: dict[str, tuple[float, object]] = {}  # ключ -> (когда загружено, данные)
_loading: set[str] = set()

# Слова из названий мест, по которым экскурсию не подобрать: «Прогулка по улице …», «Музей …»
STOP = {"прогулка", "прогулки", "улица", "улице", "улицы", "проспект", "проспекту", "набережная", "площадь",
        "музей", "музея", "собор", "церковь", "дворец", "мечеть", "главное", "здание", "центр", "семьи",
        "город", "остров", "озера", "озеро", "театр", "храм", "башня", "лестница", "ворота", "парк", "сад",
        "имени", "старая", "старый", "новая", "нижне", "верхне", "нижний", "верхний", "смотровая", "деревня",
        "республики", "татарстан", "татарстана", "области", "государственный"}


def enabled() -> bool:
    return bool(os.environ.get("TRIPSTER_PARTNER", "").strip() and os.environ.get("TRIPSTER_SECRET", "").strip())


def partner() -> str:
    return os.environ.get("TRIPSTER_PARTNER", "").strip()


# ------------------------------------------------------------------ API

def _http(req: urllib.request.Request) -> dict:
    return net.get_json(req, TIMEOUT)


def _obtain_token() -> str:
    body = json.dumps({"partner": partner(), "secret": os.environ["TRIPSTER_SECRET"].strip()}).encode()
    req = urllib.request.Request(BASE + "/api/auth/obtain_token/partner/", data=body,
                                 headers={"Content-Type": "application/json"})
    return _http(req)["token"]


def api_get(url: str) -> dict:
    """GET с токеном; истёк — получаем новый и повторяем один раз."""
    global _token
    for attempt in (1, 2):
        if not _token:
            _token = _obtain_token()
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _token})
        try:
            return _http(req)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and attempt == 1:
                _token = None
                continue
            raise
    raise RuntimeError("unreachable")


def _url(method: str, **params) -> str:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return f"{BASE}/api/partners/{partner()}/{method}/" + (f"?{q}" if q else "")


def _load_city(city: str) -> dict:
    """Каталог экскурсий города в коротком виде + маркировка рекламы."""
    found = api_get(_url("cities", name_ru=city)).get("results") or []
    if not found:
        return {"city_id": None, "items": [], "ad": ad_info()}
    city_id = found[0]["id"]
    items, url, pages = [], _url("experiences", city=city_id, price_format="detailed", page_size=100), 0
    while url and pages < MAX_PAGES:
        page = api_get(url)
        items += [short(x) for x in page.get("results", []) if x.get("is_visible", True) and x.get("status", "active") == "active"]
        url, pages = page.get("next"), pages + 1
    return {"city_id": city_id, "items": items, "ad": ad_info()}


def ad_info() -> dict:
    """Маркировка: erir_token -> erid в ссылках, about — рядом с пометкой «Реклама»."""
    info = api_get(f"{BASE}/api/partners/{partner()}/").get("ad_info") or {}
    return {"erid": (info.get("erir_token") or "").strip(), "about": (info.get("about") or "").strip()}


def short(x: dict) -> dict:
    price = x.get("price") or {}
    photos = x.get("photos") or []
    return {
        "id": x["id"], "title": x.get("title", "").replace("\xa0", " "), "tagline": (x.get("tagline") or "").replace("\xa0", " "),
        "url": x.get("url") or f"{BASE}/experience/{x['id']}/",
        "minutes": int(round(float(x.get("duration") or 0) * 60)),
        "rating": x.get("rating"), "reviews": x.get("review_count") or 0,
        "price": price.get("value"), "unit": (price.get("unit_string") or "").strip(),
        "per_group": bool(price.get("per_group")) and not (price.get("unit_string") or "").strip(),
        "format": x.get("format"), "kids": bool(x.get("child_friendly")),
        "photo": photos[0].get("thumbnail") if photos else None,
    }


# --------------------------------------------------------------- cache

def _disk(key: str) -> Path:
    return store.BASE / "tripster" / (re.sub(r"[^\w-]+", "_", key.lower()) + ".json")


def _refresh(key: str, city: str) -> None:
    try:
        data = _load_city(city)
        path = _disk(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
        with _lock:
            _cache[key] = (time.time(), data)
    except Exception as e:  # сеть, ключ, лимит — останемся на прежнем каталоге
        print(f"[tripster] {city}: {type(e).__name__}: {e}", flush=True)
    finally:
        with _lock:
            _loading.discard(key)


def city_data(city: str, wait: bool = False) -> dict | None:
    """Каталог города из кеша; устарел или нет — обновляем в фоне (wait=True — дождаться, для тестов и прогрева)."""
    if not enabled():
        return None
    key = f"{partner()}-{city}"
    with _lock:
        hit = _cache.get(key)
    if hit is None and _disk(key).is_file():
        try:
            saved = json.loads(_disk(key).read_text(encoding="utf-8"))
            hit = (saved["at"], saved["data"])
            with _lock:
                _cache[key] = hit
        except (OSError, ValueError, KeyError):
            hit = None
    if hit is None or time.time() - hit[0] > TTL:
        with _lock:
            start = key not in _loading
            _loading.add(key)
        if start:
            if wait:
                _refresh(key, city)
                with _lock:
                    hit = _cache.get(key, hit)
            else:
                threading.Thread(target=_refresh, args=(key, city), daemon=True).start()
    return hit[1] if hit else None


def warm_up() -> None:
    """При запуске сервера — каталоги всех городов приложения в фоне."""
    if enabled():
        for city in catalog.cities():
            city_data(city)


# --------------------------------------------------------------- matching

def keywords(name: str, city: str) -> list[str]:
    """Основы значимых слов названия: «Казанский кремль: мечеть Кул-Шариф» -> ["кремл", "кул-ш"]."""
    city_stem = city.lower()[:5]
    out = []
    for w in re.findall(r"[а-яё][а-яё-]+", name.lower().replace("ё", "е")):
        if len(w) < 5 or w in STOP or w.split("-")[0] in STOP or w.startswith(city_stem):  # «остров-град»
            continue
        out.append(w[:5])  # основа без окончания: «Невскому» и «Невский» -> «невск»
    return list(dict.fromkeys(out))


def link(x: dict, ad: dict) -> str:
    """Партнёрская ссылка: exp_partner и utm по правилам Трипстера, erid — если выдан."""
    q = {"exp_partner": partner(), "utm_source": partner(), "utm_campaign": "affiliates", "utm_medium": "api",
         "utm_term": "tripplan"}
    if ad.get("erid"):
        q["erid"] = ad["erid"]
    sep = "&" if "?" in x["url"] else "?"
    return x["url"] + sep + urllib.parse.urlencode(q)


def for_place(place: dict, city: str, kids: bool = False, n: int = PER_PLACE) -> list[dict]:
    """До n экскурсий к месту программы: больше совпавших слов — выше, дальше рейтинг с учётом числа отзывов."""
    data = city_data(city)
    if not data or not data["items"]:
        return []
    words = keywords(place.get("name", ""), city) + [w for w in place.get("tripster_words", [])]
    if not words:
        return []
    daytrip = place.get("category") == "daytrip"
    scored = []
    for x in data["items"]:
        if x["format"] != "experience" or (x["minutes"] > 12 * 60 and not daytrip) or (kids and not x["kids"]):
            continue
        title, tag = x["title"].lower().replace("ё", "е"), x["tagline"].lower().replace("ё", "е")
        hits = sum(2 if w in title else 1 if w in tag else 0 for w in words)
        if hits and place.get("category") == "museum" and "музе" in title:
            hits += 1  # к музею — экскурсия по музею, а не по городу с похожим словом в названии
        if hits:
            scored.append((hits, (x["rating"] or 0) * math.log1p(x["reviews"]), x))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return [view(x, data["ad"]) for _, _, x in scored[:n]]


def find(city: str, eid: int) -> tuple[dict, dict] | None:
    """Экскурсия из каталога города (кеш) и маркировка — чтобы поставить её в программу."""
    data = city_data(city)
    x = next((x for x in (data or {}).get("items", []) if x["id"] == eid), None)
    return (x, data["ad"]) if x else None


def ad_label(ad: dict) -> str:
    return ("Реклама · " + ad["about"] + (f" · erid: {ad['erid']}" if ad.get("erid") else "")) if ad.get("about") else "Реклама"


def as_place(x: dict, ad: dict, lat: float | None, lon: float | None) -> dict:
    """Экскурсия как место программы; хранится в поездке (custom_places), как квартира по ссылке.
    Координат у экскурсий нет — берём точку места, вместо которого её ставим."""
    group = x["per_group"]
    return {"id": f"tripster-{x['id']}", "name": f"Экскурсия: {x['title']}", "category": "excursion", "source": "tripster",
            "lat": lat, "lon": lon, "duration": x["minutes"] or 120, "kids": x["kids"],
            "price_adult": 0 if group else int(x["price"] or 0), "price_child": 0 if group else int(x["price"] or 0),
            "price_group": int(x["price"] or 0) if group else 0,
            "note": (x["tagline"] + " · " if x["tagline"] else "") + "Время и место встречи — после бронирования",
            "url": link(x, ad), "ad": ad_label(ad), "photos": [x["photo"]] if x.get("photo") else []}


def view(x: dict, ad: dict) -> dict:
    from .planner import fmt_dur, plural, rub  # planner импортирует catalog, а не наоборот — без циклов

    price = ""
    if x["price"]:
        price = rub(x["price"]) + (f" {x['unit']}" if x["unit"] else " за экскурсию" if x["per_group"] else "")
    meta = [fmt_dur(x["minutes"]) if x["minutes"] else "", price,
            f"★ {x['rating']:.1f}".replace(".", ",")
            + f" · {x['reviews']} {plural(x['reviews'], 'отзыв', 'отзыва', 'отзывов')}" if x["rating"] else ""]
    return {"id": x["id"], "title": x["title"], "meta": " · ".join(m for m in meta if m), "url": link(x, ad),
            "photo": x["photo"], "ad": ad_label(ad)}
