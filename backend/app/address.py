"""Подсказки адресов при вводе.

Два источника:
- DaData (лучше всего для российских адресов) — если задан ключ DADATA_API_KEY;
- Photon на данных OpenStreetMap — без ключа, для разработки (публичный сервер
  разрешает «разумное» число запросов и может ограничить при нагрузке).

По координатам адреса оцениваем дорогу до вокзала/аэропорта: расстояние по прямой × 1,3,
средняя скорость 25 км/ч и 15 минут на выход и ожидание. В MVP — Матрица расстояний Яндекса.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from . import catalog, net, planner

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
PHOTON_URL = os.environ.get("PHOTON_URL", "https://photon.komoot.io/api/")
TIMEOUT = 4


def provider() -> str:
    return "dadata" if os.environ.get("DADATA_API_KEY") else "photon"


def _http_json(req: urllib.request.Request) -> dict:
    return net.get_json(req, TIMEOUT)


def parse_dadata(payload: dict) -> list[dict]:
    out = []
    for s in payload.get("suggestions", []):
        d = s.get("data") or {}
        lat, lon = d.get("geo_lat"), d.get("geo_lon")
        out.append({
            "value": s.get("value") or s.get("unrestricted_value") or "",
            "lat": float(lat) if lat else None,
            "lon": float(lon) if lon else None,
        })
    return [x for x in out if x["value"]]


def parse_photon(payload: dict) -> list[dict]:
    out, seen = [], set()
    for f in payload.get("features", []):
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        street = p.get("street") or (p.get("name") if p.get("type") == "street" else None)
        parts = []
        city = p.get("city") or p.get("town") or p.get("village") or p.get("state")
        if city:
            parts.append(city)
        if street:
            parts.append(street + (f", {p['housenumber']}" if p.get("housenumber") else ""))
        if p.get("name") and p.get("name") != street and p.get("type") not in ("city", "street"):
            parts.append(p["name"])
        value = ", ".join(parts) or p.get("name") or ""
        if not value or value in seen:
            continue
        seen.add(value)
        out.append({"value": value, "lat": coords[1], "lon": coords[0]})
    return out


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е").strip()


def street_part(value: str, city: str) -> str:
    """Убираем город из начала адреса: «г Москва, ул Тверская, д 7» -> «ул Тверская, д 7»."""
    if not city:
        return value
    parts = [p.strip() for p in value.split(",")]
    c = _norm(city)
    while parts and _norm(parts[0]).removeprefix("г ").removeprefix("город ").strip() == c:
        parts.pop(0)
    return ", ".join(parts)


def fetch(query: str, near: tuple[float, float] | None, city: str = "", count: int = 7) -> list[dict]:
    query = query.strip()
    if len(query) < 3:
        return []
    if provider() == "dadata":
        payload = {"query": query, "count": count}
        if city:
            # ищем только в городе отправления; restrict_value убирает город из подсказки
            payload.update({"locations": [{"city": city}], "restrict_value": True})
        body = json.dumps(payload).encode()
        req = urllib.request.Request(DADATA_URL, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Authorization": "Token " + os.environ["DADATA_API_KEY"]})
        return parse_dadata(_http_json(req))
    params = {"q": f"{city}, {query}" if city and _norm(city) not in _norm(query) else query, "limit": count}
    if near:
        params["lat"], params["lon"] = near
    req = urllib.request.Request(PHOTON_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "svoim-hodom-prototype/0.1"})
    return parse_photon(_http_json(req))


def departure_point(origin: str, transport: str, city: str = "") -> tuple[str, tuple[float, float]] | None:
    """Вокзал или аэропорт отправления: из Москвы в Петербург и в Казань поезда идут с разных вокзалов."""
    fn = catalog.normalize_city(city or "Санкт-Петербург")
    if not fn:
        return None
    city = catalog.load(fn)["city"]
    route = planner.route_for(city, origin, planner.auto_transport(city, origin) if transport == "auto" else transport)
    if not route or not route.get("from_pt"):
        return None
    return route["station"], route["from_pt"]


def estimate_minutes(lat: float, lon: float, to: tuple[float, float]) -> int:
    km = catalog.haversine_km(lat, lon, to[0], to[1]) * 1.3
    return planner.round5(15 + km / 25 * 60)


def suggest(query: str, origin: str, transport: str, city: str = "") -> dict:
    """Подсказки в городе отправления. value — улица и дом, full — полный адрес с городом."""
    dep = departure_point(origin, transport, city)
    city = origin.strip()
    raw = fetch(query, dep[1] if dep else None, city)
    items, seen = [], set()
    for it in raw:
        street = street_part(it["value"], city)
        if city and street == it["value"] and _norm(city) not in _norm(it["value"]) and provider() == "photon":
            continue  # Photon вернул адрес из другого города
        if not street or street in seen:
            continue
        seen.add(street)
        it["value"] = street
        it["full"] = f"{city}, {street}" if city else street
        items.append(it)
    for it in items:
        if dep and it["lat"] is not None:
            it["to_station_min"] = estimate_minutes(it["lat"], it["lon"], dep[1])
            it["station"] = dep[0]
    return {"provider": provider(), "suggestions": items}
