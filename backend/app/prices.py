"""Цены билетов для списка рейсов — как на Яндекс Путешествиях, «от … ₽».

API Яндекс.Расписаний цены почти не отдаёт, поэтому дополняем их:
- поезда — API Туту.ру через Travelpayouts (без ключа): цены по типам вагонов для каждого поезда.
  Цены справочные, не на конкретную дату: Туту.ру предупреждает, что данные не в реальном времени;
- самолёты — Data API Авиасейлс (нужен токен TP_TOKEN из кабинета Travelpayouts). Это кэш поисков
  пользователей: на дату он хранит только самый дешёвый билет в одну сторону (проверено 25.10.2026 —
  1 запись на дату). Цены каждого рейса, как на Яндекс Путешествиях, даёт только поисковый API Авиасейлс
  в реальном времени — его открывают проектам с большой аудиторией.
Любая ошибка источника цен не мешает показать расписание — просто без цен.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

from . import net

TIMEOUT = 8
TUTU_URL = os.environ.get("TUTU_API_URL", "https://suggest.travelpayouts.com/search")
AVIA_URL = os.environ.get("AVIA_API_URL", "https://api.travelpayouts.com/aviasales/v3/prices_for_dates")

# Коды станций «Экспресс» для городов (все вокзалы города; где такого нет — главный вокзал).
# Найти код: https://www.tutu.ru/suggest/railway_simple/?name=<город>. Калининград Туту.ру не продаёт (поезд через Литву)
EXPRESS = {"москва": 2000000, "санкт-петербург": 2004000, "казань": 2060500, "нижний новгород": 2060000,
           "выборг": 2004682, "краснодар": 2064788, "сочи": 2064130, "владимир": 2060340, "ярославль": 2010000}
IATA = {"москва": "MOW", "санкт-петербург": "LED", "казань": "KZN", "нижний новгород": "GOJ",
        "екатеринбург": "SVX", "новосибирск": "OVB", "сочи": "AER", "калининград": "KGD", "краснодар": "KRR"}
CAR_CLASS = {"sedentary": "сидячий", "plazcard": "плацкарт", "coupe": "купе", "lux": "СВ", "soft": "люкс"}
CLASS_ORDER = ["сидячий", "плацкарт", "купе", "СВ", "люкс"]

_LOOKALIKE = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def _key(n: str) -> str:
    return (n or "").strip().lower().replace("ё", "е")


def train_key(n: str) -> str:
    """«752А», «752A», «752» → «752А»/«752»; ведущие нули не важны: «054А» = «54А»."""
    s = "".join(ch for ch in (n or "").upper().translate(_LOOKALIKE) if ch.isalnum())
    return s.lstrip("0")


def flight_key(n: str) -> str:
    """«SU 6», «SU6», «SU-0006» → «SU6»."""
    m = re.match(r"^\s*([A-ZА-Я0-9]{2})\s*-?\s*0*(\d+)", (n or "").upper())
    return f"{m.group(1)}{m.group(2)}" if m else ""


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "svoim-hodom-prototype/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=net.ssl_context()) as r:
        text = r.read().decode("utf-8").strip()
    m = re.match(r"^[\w$.]+\((.*)\)\s*;?\s*$", text, re.S)  # JSONP: n({...})
    return json.loads(m.group(1) if m else text)


def parse_tutu(payload: dict) -> dict[str, list[dict]]:
    """Номер поезда → цены по типам вагонов, от дешёвого."""
    out: dict[str, list[dict]] = {}
    for t in payload.get("trips", []):
        num = train_key(t.get("trainNumber", ""))
        if not num:
            continue
        best: dict[str, int] = {}
        for c in t.get("categories", []):
            name = CAR_CLASS.get(c.get("type"), c.get("type") or "")
            try:
                price = int(float(c.get("price")))
            except (TypeError, ValueError):
                continue
            if price > 0 and (name not in best or price < best[name]):
                best[name] = price
        if best:
            out[num] = sorted(({"name": k, "price": v} for k, v in best.items()),
                              key=lambda x: (x["price"], CLASS_ORDER.index(x["name"]) if x["name"] in CLASS_ORDER else 9))
    return out


def tutu_prices(origin: str, dest: str) -> dict[str, list[dict]]:
    a, b = EXPRESS.get(_key(origin)), EXPRESS.get(_key(dest))
    if not a or not b:
        return {}
    q = urllib.parse.urlencode({"service": "tutu_trains", "term": a, "term2": b})
    return parse_tutu(_fetch(f"{TUTU_URL}?{q}"))


def parse_avia(payload: dict, date: str) -> dict[str, int]:
    """Номер рейса → самая низкая найденная цена на эту дату (только прямые, в одну сторону)."""
    out: dict[str, int] = {}
    for t in payload.get("data", []):
        if t.get("return_at") or t.get("transfers"):
            continue
        if not str(t.get("departure_at", "")).startswith(date):
            continue
        key = flight_key(f"{t.get('airline', '')} {t.get('flight_number', '')}")
        price = t.get("price")
        if key and isinstance(price, (int, float)) and price > 0:
            out[key] = min(int(price), out.get(key, 10**9))
    return out


def avia_prices(origin: str, dest: str, date: str) -> dict[str, int]:
    token = os.environ.get("TP_TOKEN", "").strip()
    a, b = IATA.get(_key(origin)), IATA.get(_key(dest))
    if not token or not a or not b:
        return {}
    q = urllib.parse.urlencode({"origin": a, "destination": b, "departure_at": date, "one_way": "true",
                                "direct": "true", "sorting": "price", "limit": 30, "currency": "rub", "token": token})
    return parse_avia(_fetch(f"{AVIA_URL}?{q}"), date)


def enrich(items: list[dict], origin: str, dest: str, date: str, transport: str) -> str | None:
    """Дописывает рейсам цены «от» и классы вагонов. Возвращает подпись источника или None."""
    try:
        if transport == "train":
            table = tutu_prices(origin, dest)
            hit = False
            for it in items:
                classes = table.get(train_key(it["number"]))
                if classes:
                    it["classes"] = classes
                    it["price_from"] = it.get("price_from") or classes[0]["price"]
                    it["price_source"] = "tutu"
                    hit = True
            return "Цены — справочные Туту.ру, на выбранную дату могут отличаться" if hit else None
        if transport == "plane":
            table = avia_prices(origin, dest, date)
            if not table:
                return None
            best_key = min(table, key=table.get)
            for it in items:
                price = table.get(flight_key(it["number"]))
                if price:
                    it["price_from"] = min(price, it.get("price_from") or price)
                    it["price_source"] = "aviasales"
                    it["cheapest"] = flight_key(it["number"]) == best_key
            best = next((it for it in items if it.get("cheapest")), None)
            name = best["number"] if best else f"{best_key[:2]} {best_key[2:]}"
            return (f"Самый дешёвый билет на эту дату по данным Авиасейлс — от {table[best_key]:,} ₽ за взрослого, рейс {name}. "
                    "Цены остальных рейсов смотрите по ссылкам «Купить билет»").replace(",", "\u202f", 1)
    except Exception as e:  # цены — не главное: расписание покажем и без них
        print(f"[prices] {transport} {origin} -> {dest} {date}: {type(e).__name__}: {e}", flush=True)
    return None
