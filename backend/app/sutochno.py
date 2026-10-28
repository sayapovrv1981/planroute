"""Квартира с Суточно.ру по ссылке «Поделиться».

Открытого API у Суточно.ру нет, но страница объявления отдаёт разметку schema.org (VacationRental):
название, адрес, координаты, фото, вместимость, цену «от», рейтинг. Время заезда и выезда берём
из блока «Правила дома» в тексте страницы.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .net import ssl_context

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
ID_RE = re.compile(r"sutochno\.ru/(?:front/searchapp/detail/)?(\d{4,})(?:\D|$)")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
MAX_BYTES = 4_000_000


def find_url(text: str) -> str:
    """Ссылка из того, что вставил пользователь: в «Поделиться» к ней бывает приписан текст."""
    for u in URL_RE.findall(text or ""):
        host = (urlsplit(u).hostname or "").lower()
        if host == "sutochno.ru" or host.endswith(".sutochno.ru"):
            return u
    raise ValueError("Вставьте ссылку на объявление с сайта sutochno.ru")


def page_url(url: str) -> str:
    m = ID_RE.search(url)
    return f"https://sutochno.ru/{m.group(1)}" if m else url


def fetch(url: str, timeout: float = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            host = (urlsplit(r.geturl()).hostname or "").lower()
            if not (host == "sutochno.ru" or host.endswith(".sutochno.ru")):
                raise ValueError("Ссылка ведёт не на Суточно.ру")
            return r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError("Объявление не найдено — возможно, его сняли с сайта") from None
        raise ValueError(f"Суточно.ру не отдал страницу (ошибка {e.code}), попробуйте позже") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Не удалось открыть Суточно.ру, попробуйте позже") from None


def _ld(page: str) -> dict:
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if d.get("@type") in ("VacationRental", "Accommodation", "Apartment", "House", "Hotel"):
            return d
    return {}


def _map(page: str) -> dict:
    """Объект для карты на странице: есть и там, где нет разметки schema.org."""
    m = re.search(r"var OBJECT_MAP_DATA = '(.*?)';", page)
    try:
        d = json.loads(re.sub(r"\\(.)", r"\1", m.group(1))) if m else {}  # строка JS: снять одно экранирование
        return (d.get("objects") or [{}])[0] | {"location": d.get("location", "")}
    except (ValueError, AttributeError):
        return {}


def _og(page: str, prop: str) -> str:
    m = re.search(rf'<meta property="og:{prop}" content="([^"]*)"', page)
    return html.unescape(m.group(1)) if m else ""


def _int(s) -> int:
    digits = re.sub(r"\D", "", str(s or "").split("<")[0].split(".")[0])
    return int(digits) if digits else 0


def _photo(u: str) -> str:
    """Фото в исходном размере: в данных карты ссылки вида //static…/medium/x.jpg."""
    u = "https:" + u if u.startswith("//") else u
    return re.sub(r"/(?:medium|small|big|\d+x\d+)/([0-9a-f]+\.\w+)$", r"/\1", u)


def _text(page: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def _hm(s: str) -> str:
    h, m = s.split(":")
    return f"{int(h):02d}:{m}"


def short_name(name: str, city: str) -> str:
    """«2-комнатная квартира посуточно, Санкт-Петербург, Невский проспект, 131, объявление 361449»
    → «2-комнатная квартира, Невский проспект, 131»."""
    parts = [p.strip() for p in name.split(",")]
    parts = [p for p in parts if p and p != city and not p.startswith("объявление")]
    if parts:
        parts[0] = re.sub(r"\s+посуточно$", "", parts[0])
    return ", ".join(parts) or name


def parse(page: str) -> dict:
    """Страница объявления → место в формате каталога (category hotel, stay apartment)."""
    d, mp = _ld(page), _map(page)
    if not d and not mp:
        raise ValueError("На странице нет данных объявления — это точно ссылка на квартиру?")
    oid = str(d.get("identifier") or mp.get("id") or "")
    if not oid.isdigit():
        raise ValueError("Не нашли номер объявления на странице")
    geo, addr = d.get("geo") or {}, d.get("address") or {}
    try:
        lat = float(geo.get("latitude") or mp["lat"])
        lon = float(geo.get("longitude") or mp["lng"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("В объявлении нет координат") from None
    city = addr.get("addressLocality") or re.sub(r"^.*?г\.\s*", "", mp.get("location", ""))
    room = d.get("containsPlace") or {}
    capacity = _int((room.get("occupancy") or {}).get("value") or mp.get("max_guests")) or 4
    price = _int((d.get("offers") or {}).get("price") or mp.get("price"))
    photos = [_photo(u) for u in (d.get("image") or mp.get("media") or []) if isinstance(u, str)]
    text = _text(page)
    cin = re.search(r"Заезд\s+(?:после|с)\s+(\d{1,2}:\d{2})", text)
    cout = re.search(r"Отъезд\s+до\s+(\d{1,2}:\d{2})", text)
    rating = d.get("aggregateRating") or {}

    facts = []
    if room.get("numberOfBedrooms"):
        facts.append(f"спален: {room['numberOfBedrooms']}")
    elif mp.get("beds"):
        facts.append(mp["beds"])
    if (room.get("bed") or {}).get("numberOfBeds"):
        facts.append(f"кроватей: {room['bed']['numberOfBeds']}")
    facts.append(f"до {capacity} гостей")
    if rating.get("ratingValue"):
        r = str(rating["ratingValue"]).replace(".", ",")
        facts.append(f"★ {r}" + (f" ({rating['reviewCount']} отзывов)" if rating.get("reviewCount") else ""))
    facts.append("Суточно.ру")

    place = {
        "id": f"sutochno-{oid}",
        "source": "sutochno",
        "source_id": oid,
        "name": short_name(d.get("name") or _og(page, "title") or mp.get("title") or f"Квартира {oid}", city),
        "category": "hotel",
        "stay": "apartment",
        "lat": lat,
        "lon": lon,
        "city": city,
        "address": addr.get("streetAddress") or mp.get("address", ""),
        "price_night": price,
        "tier": 1,
        "capacity": capacity,
        "note": " · ".join(facts),
        "url": f"https://{_subdomain(page)}sutochno.ru/{oid}",
        "photos": list(dict.fromkeys(photos))[:12],
    }
    if cin:
        place["check_in"] = _hm(cin.group(1))
    if cout:
        place["check_out"] = _hm(cout.group(1))
    return place


def _subdomain(page: str) -> str:
    m = re.search(r'<meta property="og:url" content="https://([a-z0-9-]+\.)?sutochno\.ru/', page)
    return (m.group(1) or "") if m else ""


def thumb(url: str) -> str:
    """Уменьшенная версия фото: у Суточно.ру рядом с оригиналом лежит папка small/."""
    return re.sub(r"/([0-9a-f]+\.\w+)$", r"/small/\1", url) if "static.sutochno.ru/doc/files/objects/" in url else url


def load(text: str) -> dict:
    return parse(fetch(page_url(find_url(text))))
