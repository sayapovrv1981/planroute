"""Расписание поездов, самолётов и автобусов — API Яндекс.Расписаний.

Ключ: YANDEX_RASP_API_KEY (бесплатно, Кабинет разработчика Яндекса). Без ключа функция отвечает
available=False, и в форме время вводится вручную.
Документация метода: https://yandex.ru/dev/rasp/doc/ru/reference/schedule-point-point
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from . import net, prices

API_URL = os.environ.get("RASP_API_URL", "https://api.rasp.yandex-net.ru/v3.0/search/")
TIMEOUT = 15  # поиск самолётов у Яндекса бывает медленным
CACHE_SEC = 30 * 60  # расписание меняется редко — не спрашиваем Яндекс заново полчаса
_CACHE: dict[tuple, tuple[float, dict]] = {}

# Коды городов Яндекса (c + id региона). Дополняется по мере появления городов.
CITY_CODES = {
    "москва": "c213",
    "санкт-петербург": "c2",
    "казань": "c43",
    "нижний новгород": "c47",
    "калининград": "c22",
    "екатеринбург": "c54",
    "новосибирск": "c65",
    "сочи": "c239",
}
TRANSPORT_TYPES = {"train": "train", "plane": "plane", "bus": "bus"}

# Адреса городов на Яндекс Путешествиях (проверены для Москвы и Петербурга)
TRAVEL_SLUGS = {"москва": "moscow", "санкт-петербург": "saint-petersburg", "казань": "kazan",
                "нижний новгород": "nizhny-novgorod", "калининград": "kaliningrad"}
TRAVEL_SECTION = {"train": "trains/{a}--{b}/", "bus": "buses/{a}--{b}/", "plane": "avia/routes/{a}--{b}/"}


# Туту.ру — адреса направлений поездов (проверены для Москвы и Петербурга)
TUTU_SLUGS = {"москва": "Moskva", "санкт-петербург": "Sankt-Peterburg"}
# Коды городов IATA для поиска на Авиасейлс
IATA = {"москва": "MOW", "санкт-петербург": "LED", "казань": "KZN", "нижний новгород": "GOJ",
        "екатеринбург": "SVX", "новосибирск": "OVB", "сочи": "AER", "калининград": "KGD"}
# Партнёрские ссылки Travelpayouts: маркер и проект из личного кабинета, номер программы — у каждого оффера свой
TP_PROGRAMS = {p: f"TP_PROGRAM_{p.upper()}" for p in ("aviasales", "tutu", "ostrovok", "sutochno", "tripster")}
TP_DEFAULT_PROGRAM = {"aviasales": "4114", "ostrovok": "7038"}
# Для ссылок с рекламной маркировкой: campaign_id и erid из ссылки, выданной в кабинете для этой программы
TP_EXTRA = {"campaign_id": "TP_CAMPAIGN_{}", "erid": "TP_ERID_{}"}
TP_HOSTS = {"ostrovok.ru": "ostrovok", "aviasales.ru": "aviasales", "tutu.ru": "tutu", "sutochno.ru": "sutochno",
            "tripster.ru": "tripster"}


def _key(n: str) -> str:
    return (n or "").strip().lower().replace("ё", "е")


def partner(url: str, program: str) -> str:
    """Оборачивает ссылку в партнёрскую Travelpayouts, если в .env заданы маркер и номер программы."""
    marker = os.environ.get("TP_MARKER", "").strip()
    p = os.environ.get(TP_PROGRAMS.get(program, ""), "").strip() or TP_DEFAULT_PROGRAM.get(program, "")
    if not marker or not p:
        return url
    q = {"marker": marker, "p": p, "u": url}
    if os.environ.get("TP_TRS"):
        q["trs"] = os.environ["TP_TRS"].strip()
    for key, env in TP_EXTRA.items():
        if os.environ.get(env.format(program.upper()), "").strip():
            q[key] = os.environ[env.format(program.upper())].strip()
    return "https://tp.media/r?" + urllib.parse.urlencode(q)


def program_of(url: str) -> str | None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return next((v for h, v in TP_HOSTS.items() if host == h or host.endswith("." + h)), None)


def ad_label(url: str) -> str | None:
    """Маркировка рекламы для ссылки с erid: «Реклама · рекламодатель · erid …» (рекламодатель — TP_AD_<ПРОГРАММА>)."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    if not q.get("erid"):
        return None
    program = program_of(q.get("u", [""])[0]) or ""
    who = os.environ.get(f"TP_AD_{program.upper()}", "").strip()
    return " · ".join(x for x in ("Реклама", who, f"erid: {q['erid'][0]}") if x)


def partner_any(url: str) -> str:
    """Партнёрская ссылка для любого сайта из программ Travelpayouts; уже обёрнутые и чужие — как есть."""
    program = program_of(url)
    return partner(url, program) if program else url


def tutu_url(origin: str, dest: str) -> str | None:
    a, b = TUTU_SLUGS.get(_key(origin)), TUTU_SLUGS.get(_key(dest))
    return f"https://www.tutu.ru/poezda/{a}/{b}/" if a and b else None


def aviasales_url(origin: str, dest: str, date: str, adults: int = 1) -> str | None:
    a, b = IATA.get(_key(origin)), IATA.get(_key(dest))
    if not a or not b or len(date) != 10:
        return None
    ddmm = date[8:10] + date[5:7]
    return f"https://www.aviasales.ru/search/{a}{ddmm}{b}{max(1, min(adults, 9))}"


def buy_links(origin: str, dest: str, transport: str, date: str = "", adults: int = 1) -> list[dict]:
    """Где купить билет: Яндекс Путешествия, для поездов — Туту.ру, для самолётов — Авиасейлс."""
    out = []
    y = travel_url(origin, dest, transport, date)
    if y:
        out.append({"label": "Яндекс Путешествия", "url": y})
    if transport == "train" and (t := tutu_url(origin, dest)):
        out.append({"label": "Туту.ру", "url": partner(t, "tutu")})
    if transport == "plane" and (av := aviasales_url(origin, dest, date, adults)):
        out.append({"label": "Авиасейлс", "url": partner(av, "aviasales")})
    for x in out:
        x["ad"] = ad_label(x["url"])
    return out


def travel_url(origin: str, dest: str, transport: str, date: str = "") -> str | None:
    """Страница покупки билетов на Яндекс Путешествиях для направления и даты."""
    key = lambda n: (n or "").strip().lower().replace("ё", "е")  # noqa: E731
    a, b = TRAVEL_SLUGS.get(key(origin)), TRAVEL_SLUGS.get(key(dest))
    section = TRAVEL_SECTION.get(transport)
    if not a or not b or not section:
        return None
    url = "https://travel.yandex.ru/" + section.format(a=a, b=b)
    return url + (f"?when={date}" if date else "")

# латинские буквы, похожие на кириллические, — чтобы «752A» и «752А» совпадали
_LOOKALIKE = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def api_key() -> str:
    return os.environ.get("YANDEX_RASP_API_KEY", "").strip()


def city_code(name: str) -> str | None:
    return CITY_CODES.get((name or "").strip().lower().replace("ё", "е"))


def norm_number(n: str) -> str:
    return "".join(ch for ch in (n or "").upper().translate(_LOOKALIKE) if ch.isalnum())


def _hm(iso: str) -> tuple[str, str]:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%H:%M"), dt.date().isoformat()


def _station(p: dict | None) -> str:
    p = p or {}
    return p.get("popular_title") or p.get("title") or ""


def parse(payload: dict) -> list[dict]:
    out, seen = [], set()
    for s in payload.get("segments", []):
        if not s.get("departure") or not s.get("arrival"):
            continue
        thread = s.get("thread") or {}
        dep, dep_date = _hm(s["departure"])
        arr, arr_date = _hm(s["arrival"])
        # Яндекс иногда отдаёт один рейс дважды (например, с разных вокзалов одного города)
        key = (thread.get("number") or (thread.get("carrier") or {}).get("title"), dep, arr)
        if key in seen:
            continue
        seen.add(key)
        prices = [p.get("price", {}).get("whole") for p in (s.get("tickets_info") or {}).get("places", [])]
        prices = [p for p in prices if isinstance(p, (int, float))]
        out.append({
            "number": thread.get("number") or "",
            "title": thread.get("title") or "",
            "carrier": ((thread.get("carrier") or {}).get("title")) or "",
            "departure": dep,
            "arrival": arr,
            "next_day": arr_date != dep_date,
            "duration_min": int((s.get("duration") or 0) // 60),
            "from": _station(s.get("from")),
            "to": _station(s.get("to")),
            "price_from": int(min(prices)) if prices else None,
        })
    out.sort(key=lambda x: x["departure"])
    return out


def _get(params: dict) -> dict:
    req = urllib.request.Request(API_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"Authorization": api_key(), "Accept": "application/json"})
    try:
        return net.get_json(req, TIMEOUT)
    except urllib.error.HTTPError:
        raise
    except OSError:  # таймаут или обрыв — пробуем ещё раз
        return net.get_json(req, TIMEOUT)


def search(origin: str, dest: str, date: str, transport: str) -> dict:
    if not api_key():
        return {"available": False, "items": [],
                "message": "Подключите ключ API Яндекс.Расписаний — тогда рейс можно будет выбрать из списка, "
                           "а время подставится по номеру"}
    a, b = city_code(origin), city_code(dest)
    kind = TRANSPORT_TYPES.get(transport)
    if not a or not b or not kind:
        return {"available": False, "items": [], "message": "Для этого города расписание пока не подключено"}
    ck = (a, b, date, kind)
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1]
    payload = _get({"from": a, "to": b, "date": date, "transport_types": kind, "format": "json",
                    "lang": "ru_RU", "limit": 100})
    items = parse(payload)
    note = prices.enrich(items, origin, dest, date, kind)
    res = {"available": True, "items": items, "buy_links": buy_links(origin, dest, transport, date),
           "price_note": note}
    _CACHE[ck] = (time.time(), res)
    return res


def find(items: list[dict], number: str) -> dict | None:
    n = norm_number(number)
    if not n:
        return None
    return next((x for x in items if norm_number(x["number"]) == n), None)
