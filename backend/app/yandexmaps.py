"""Кафе или ресторан по ссылке с Яндекс Карт — вместо обеда или ужина в программе.

Открытого API у страниц организаций нет (API поиска по организациям платный), но страница
yandex.ru/maps/org/<имя>/<id>/ отдаёт данные карточки в <script class="state-view">: название, адрес,
координаты, рейтинг, категории, «средний счёт» (features: average_bill2), часы работы по дням и статус.
Ссылку «Поделиться» вида yandex.ru/maps/-/… открываем и берём id организации из адреса, куда она ведёт.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import parse_qs, urlsplit

from .net import ssl_context

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
ORG_RE = re.compile(r"/maps/org/(?:[^/]+/)?(\d{5,})")
MAX_BYTES = 6_000_000


def _yandex(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return bool(re.fullmatch(r"(?:[\w-]+\.)*yandex\.(?:ru|com|by|kz|uz|com\.tr)|ya\.ru", host))


def find_url(text: str) -> str:
    """Ссылка из того, что вставил пользователь: в «Поделиться» к ней бывает приписан текст."""
    for u in URL_RE.findall(text or ""):
        if _yandex(u) and ("/maps" in u or "/profile/" in u):
            return u
    raise ValueError("Вставьте ссылку на заведение с Яндекс Карт: в карточке места «Поделиться» → «Скопировать»")


def org_id(url: str) -> str | None:
    m = ORG_RE.search(url)
    if m:
        return m.group(1)
    oid = parse_qs(urlsplit(url).query).get("oid")
    return oid[0] if oid and oid[0].isdigit() else None


def fetch(url: str, timeout: float = 20) -> tuple[str, str]:
    """(страница, адрес после переадресаций)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            if not _yandex(r.geturl()):
                raise ValueError("Ссылка ведёт не на Яндекс Карты")
            return r.read(MAX_BYTES).decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        raise ValueError(f"Яндекс Карты не отдали страницу (ошибка {e.code}), попробуйте позже") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Не удалось открыть Яндекс Карты, попробуйте позже") from None


def _state(page: str) -> dict:
    m = re.search(r'<script type="application/json" class="state-view">(.*?)</script>', page, re.S)
    if not m:
        raise ValueError("Яндекс Карты не показали карточку заведения — попробуйте ещё раз чуть позже")
    return json.loads(html.unescape(m.group(1)))


def _card(state: dict, oid: str | None) -> dict | None:
    """Карточка организации в данных страницы: у неё есть title, coordinates и categories."""
    stack = [state]
    first = None
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "title" in o and "coordinates" in o and "categories" in o:
                if oid is None or str(o.get("id")) == oid:
                    return o
                first = first or o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return first if oid is None else None


def _hm(t: dict) -> str:
    h, m = int(t.get("hours", 0)), int(t.get("minutes", 0))
    return "23:59" if h >= 24 else f"{h:02d}:{m:02d}"


def hours(working: list | None) -> dict | None:
    """workingTime Яндекса (7 дней с воскресенья, null — выходной) -> {open, close, closed, late} каталога
    (дни с понедельника = 0). Разное время закрытия по дням — в late."""
    if not working or len(working) != 7 or not any(working):
        return None
    days = {}
    for i, spans in enumerate(working):
        wd = (i - 1) % 7
        if spans:
            days[wd] = (_hm(spans[0]["from"]), _hm(spans[-1]["to"]))
    opens = Counter(o for o, _ in days.values()).most_common(1)[0][0]
    close = Counter(c for _, c in days.values()).most_common(1)[0][0]
    out = {"open": opens, "close": close, "closed": sorted(set(range(7)) - set(days))}
    late = {str(wd): c for wd, (_, c) in days.items() if c != close}
    if late:
        out["late"] = late
    if opens == close == "00:00" or (opens == "00:00" and close == "23:59"):
        out = {"open": "00:00", "close": "23:59", "closed": out["closed"]}  # круглосуточно
    return out


def avg_bill(card: dict) -> int | None:
    """«1200–1400 ₽» -> 1300."""
    for f in card.get("features") or []:
        if f.get("id", "").startswith("average_bill") and isinstance(f.get("value"), str):
            nums = [int(n) for n in re.findall(r"\d+", f["value"].replace(" ", "").replace(" ", ""))]
            if nums:
                return int(sum(nums[:2]) / len(nums[:2]))
    return None


def parse(page: str, oid: str | None = None) -> dict:
    title = (re.search(r"<title>([^<]*)", page) or [None, ""])[1]
    card = _card(_state(page), oid)
    if not card:
        raise ValueError("На странице нет карточки заведения — скопируйте ссылку из карточки места")
    status = str(card.get("status") or "")
    if "closed" in status or title.startswith("Больше не работает"):
        raise ValueError(f"По данным Яндекс Карт «{card['title']}» больше не работает")
    lon, lat = card["coordinates"][:2]
    rating = card.get("ratingData") or {}
    bill = avg_bill(card)
    cats = [c.get("name", "") for c in card.get("categories") or []]
    kind = ", ".join(cats[:2])
    # рейтинг и часы в описание пункта добавляет planner.food_item из полей ниже — здесь их нет
    note = [kind[:1].upper() + kind[1:], card.get("address", "")]
    if not bill:
        note.append("средний чек не указан — оценили сами")
    og = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    oid = str(card.get("id") or oid or "")
    return {
        "id": f"ymaps-{oid}", "name": card["title"], "category": "food", "source": "yandex",
        "lat": float(lat), "lon": float(lon), "avg_check": bill or 1500, "kids": True,
        "hours": hours(card.get("workingTime")), "meal": ["breakfast", "lunch", "dinner"],
        "note": " · ".join(n for n in note if n), "address": card.get("address", ""),
        "rating": round(float(rating["ratingValue"]), 1) if rating.get("ratingValue") else None,
        "reviews": int(rating.get("reviewCount") or 0),
        "url": f"https://yandex.ru/maps/org/{card.get('seoname') or 'org'}/{oid}/",
        "photos": [html.unescape(og.group(1))] if og else [],
    }


def load(text: str) -> dict:
    url = find_url(text)
    oid = org_id(url)
    if oid:  # страница организации без лишнего: карта, поиск и т. п. отдают другой набор данных
        url = f"https://yandex.ru/maps/org/{oid}/"
    page, final = fetch(url)
    if not oid and (oid := org_id(final)):  # короткая ссылка «Поделиться» — узнали id после переадресации
        page, _ = fetch(f"https://yandex.ru/maps/org/{oid}/")
    return parse(page, oid)
