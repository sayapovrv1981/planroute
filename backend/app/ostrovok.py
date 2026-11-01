"""Отель или квартира с Островка по ссылке: заполнить «свою бронь» в форме.

Страница ostrovok.ru/hotel/<страна>/<город>/mid<id>/<slug>/ отдаёт в разметке название (og:title), фото (og:image),
координаты, адрес, вид жилья и время заезда и выезда. Цены на ней без дат нет — её человек вписывает из своей брони.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .net import ssl_context

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_BYTES = 3_000_000
URL_RE = re.compile(r"https?://(?:[\w-]+\.)*ostrovok\.ru/hotel/[^\s\"'<>]+", re.I)
APARTMENT_KINDS = {"APARTMENT", "APARTMENTS", "APART_HOTEL", "APARTHOTEL", "VILLA", "COTTAGE", "GUESTHOUSE"}


def find_url(text: str) -> str | None:
    """Ссылка на страницу отеля Островка в тексте; другой сайт — None."""
    m = URL_RE.search(text or "")
    return m.group(0).split("?")[0] if m else None


def fetch(url: str, timeout: float = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            host = (urlsplit(r.geturl()).hostname or "").lower()
            if not (host == "ostrovok.ru" or host.endswith(".ostrovok.ru")):
                raise ValueError("Ссылка ведёт не на Островок")
            if "/hotel/" not in r.geturl():
                raise ValueError("Отель не найден — откройте его страницу на Островке и скопируйте ссылку")
            return r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError("Отель не найден на Островке — проверьте ссылку") from None
        raise ValueError(f"Островок не отдал страницу (ошибка {e.code}), попробуйте позже") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Не удалось открыть Островок, попробуйте позже") from None


def _hm(page: str, key: str) -> str:
    m = re.search(rf'"{key}":\{{"hours":(\d+),"minutes":(\d+)', page)
    return f"{int(m[1]):02d}:{int(m[2]):02d}" if m else ""


def parse(page: str, url: str) -> dict:
    """Поля формы «своя бронь» (ManualStay): name, address, lat, lon, check_in, check_out, kind, url, photo."""
    meta = lambda prop: (re.search(rf'<meta property="og:{prop}" content="([^"]*)"', page) or [None, ""])[1]  # noqa: E731
    title = html.unescape(meta("title"))
    # «Отель Helen 4* в Санкт-Петербурге 678 отзывов об отеле…» -> «Отель Helen»
    name = re.split(r"\s+(?:\d\*\s+)?в\s+[А-ЯЁ]", title, maxsplit=1)[0].strip()
    coords = re.search(r'"latitude":(-?[\d.]+),"longitude":(-?[\d.]+)', page)
    if not name or not coords:
        raise ValueError("На странице Островка нет данных отеля — скопируйте ссылку со страницы самого отеля")
    # публичный адрес («Большая Морская улица, д. 28/ 13, Санкт-Петербург»), а не юридический с индексом
    addresses = [html.unescape(a) for a in re.findall(r'"address":"([^"]{5,200})"', page)]
    address = next((a for a in addresses if not re.match(r"\d{6},", a)), addresses[0] if addresses else "")
    parts = [x.strip() for x in address.split(",")]
    if len(parts) > 1 and not re.search(r"\d", parts[-1]):  # город в конце — в форме он уже стоит приставкой
        address = ", ".join(parts[:-1])
    kind = (re.search(r'"facts":\{.*?"kind":"([A-Z_]+)"', page, re.S) or [None, "HOTEL"])[1]
    return {
        "name": name[:120], "address": address, "lat": round(float(coords[1]), 6), "lon": round(float(coords[2]), 6),
        "check_in": _hm(page, "checkInTimeStart"), "check_out": _hm(page, "checkOutTimeEnd"),
        "kind": "apartment" if kind in APARTMENT_KINDS else "hotel", "url": url, "price_night": "",
        "photo": html.unescape(meta("image")) or None,
    }


def load(text: str) -> dict:
    url = find_url(text)
    if not url:
        raise ValueError("Вставьте ссылку на страницу отеля с сайта ostrovok.ru")
    return parse(fetch(url), url)
