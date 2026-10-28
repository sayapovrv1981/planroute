"""Билеты и брони, загруженные в форму: распознать и встроить в поездку.

PDF из писем (РЖД, авиакомпании, театры, музеи, Трипстер, отели) — текстовые: текст достаём pypdf,
а поля раскладывает языковая модель (YandexGPT или Claude, см. wishes_ai). Фото (JPEG, PNG) и сканы PDF
без текстового слоя читает Yandex Vision OCR — ключ YANDEX_VISION_API_KEY в backend/.env.

Роль брони в поездке (role): out / back — дорога туда и обратно (заполняют блоки «Туда» и «Обратно»),
stay — жильё, event — театр, музей, экскурсия и прочее с временем: жёстко встаёт в программу дня.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.request
from datetime import date

from . import address, catalog, net, planner, wishes_ai

OCR_URL = os.environ.get("YANDEX_OCR_URL", "https://ai.api.cloud.yandex.net/ocr/v1/recognizeText")
OCR_MIME = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".pdf": "PDF"}

MAX_TEXT = 12000
KINDS = ["train", "plane", "bus", "theatre", "museum", "excursion", "hotel", "other"]
TRANSPORT = ("train", "plane", "bus")
KIND_LABEL = {"train": "Поезд", "plane": "Самолёт", "bus": "Автобус", "theatre": "Театр или концерт",
              "museum": "Музей", "excursion": "Экскурсия", "hotel": "Жильё", "other": "Другое"}

SYSTEM = """Ты разбираешь билеты и брони для планировщика поездки. Во входе — текст одного документа (PDF из письма \
или письмо). Найди в нём все брони и ответь только JSON по схеме. Ничего не выдумывай: нет данных — пустая строка или 0.

Для каждой брони:
- kind: train (поезд), plane (самолёт), bus (автобус), theatre (театр, концерт, цирк), museum (музей, выставка, \
смотровая), excursion (экскурсия с гидом), hotel (отель, квартира), other (другое с датой и временем).
- title — коротко по-русски: «Поезд 752А Москва → Санкт-Петербург», «Мариинский театр: Лебединое озеро», \
«Эрмитаж», «Экскурсия: Тайны Казанского кремля», «Отель Азимут».
- date — дата в формате ГГГГ-ММ-ДД: отправления, сеанса, входа или заезда.
- start — время ЧЧ:ММ: отправления, начала сеанса, входа или заезда (местное время, как в документе).
- end — время ЧЧ:ММ: прибытия, окончания спектакля или экскурсии, выезда — только если указано. Интервал входа \
в музей («сеанс 11:00–11:30», «вход с 11:00 до 11:30») — это не окончание: start = 11:00, end пустой.
- end_date — дата прибытия или выезда, если отличается от date.
- number — номер поезда или рейса (например 752А, SU 1276); для остального пусто.
- from_city, to_city — города отправления и прибытия для поезда, самолёта и автобуса.
- venue — название площадки, музея, отеля; address — адрес, если есть.
- price — итоговая сумма по документу в рублях (числом); persons — сколько билетов, мест или гостей.

Один билет туда и обратно — это две брони. Несколько пассажиров в одном билете — одна бронь с persons."""


def _schema() -> dict:
    s = {"type": "string"}
    item = {"type": "object", "properties": {
        "kind": {"type": "string", "enum": KINDS}, "title": s, "date": s, "start": s, "end": s, "end_date": s,
        "number": s, "from_city": s, "to_city": s, "venue": s, "address": s,
        "price": {"type": "number"}, "persons": {"type": "integer"}},
        "required": ["kind", "title", "date", "start", "end", "end_date", "number", "from_city", "to_city", "venue",
                     "address", "price", "persons"], "additionalProperties": False}
    return {"type": "object", "properties": {"bookings": {"type": "array", "items": item}},
            "required": ["bookings"], "additionalProperties": False}


# ------------------------------------------------------------------ text

def ocr_enabled() -> bool:
    return bool(os.environ.get("YANDEX_VISION_API_KEY", "").strip()
                and (os.environ.get("YANDEX_VISION_FOLDER_ID") or os.environ.get("YANDEX_FOLDER_ID") or "").strip())


def _post_json(req: urllib.request.Request) -> dict:
    return net.get_json(req, 40)


def ocr(content: bytes, mime: str) -> str:
    """Yandex Vision OCR: текст с фото или скана (синхронно — одна страница PDF)."""
    folder = (os.environ.get("YANDEX_VISION_FOLDER_ID") or os.environ["YANDEX_FOLDER_ID"]).strip()
    body = {"mimeType": mime, "languageCodes": ["ru", "en"], "model": "page",
            "content": base64.b64encode(content).decode("ascii")}
    req = urllib.request.Request(OCR_URL, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "x-folder-id": folder,
        "Authorization": "Api-Key " + os.environ["YANDEX_VISION_API_KEY"].strip()})
    data = _post_json(req)
    data = data.get("result", data)
    return ((data.get("textAnnotation") or {}).get("fullText") or "").strip()


def _first_page(content: bytes) -> bytes:
    """Скан на несколько страниц — OCR синхронно читает только одну: берём первую."""
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter()
    w.add_page(PdfReader(io.BytesIO(content)).pages[0])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def text_of(filename: str, content: bytes) -> str:
    """Текст документа: PDF — через pypdf (скан без текста — OCR), письмо или текст — как есть, фото — OCR.
    Пусто — прочитать нечем (фото без ключа Vision или формат, который OCR не принимает)."""
    name = (filename or "").lower()
    ext = os.path.splitext(name)[1]
    if ext == ".pdf" or content[:5] == b"%PDF-":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:10]).strip()
            if len(text) < 20 and ocr_enabled():  # скан без текстового слоя
                text = ocr(_first_page(content) if len(reader.pages) > 1 else content, "PDF")
        except ValueError:
            raise
        except Exception as e:  # повреждённый или зашифрованный PDF
            raise ValueError(f"Не удалось прочитать PDF: {type(e).__name__}") from None
        return text
    if name.endswith((".txt", ".eml", ".html", ".htm")):
        raw = content.decode("utf-8", "replace")
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
    if ext in OCR_MIME and ocr_enabled():
        return ocr(content, OCR_MIME[ext])
    return ""


# ------------------------------------------------------------------ parsing

def _hm(v) -> str:
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", str(v or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else ""


def _date(v) -> str:
    try:
        return date.fromisoformat(str(v or "").strip()).isoformat()
    except ValueError:
        return ""


def clean(raw: dict) -> dict | None:
    """Одна бронь от модели -> проверенные поля; без даты бронь в программу не поставить."""
    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    b = {"kind": kind, "title": str(raw.get("title") or KIND_LABEL[kind]).strip()[:160],
         "date": _date(raw.get("date")), "start": _hm(raw.get("start")), "end": _hm(raw.get("end")),
         "end_date": _date(raw.get("end_date")), "number": str(raw.get("number") or "").strip()[:20],
         "from_city": str(raw.get("from_city") or "").strip()[:60], "to_city": str(raw.get("to_city") or "").strip()[:60],
         "venue": str(raw.get("venue") or "").strip()[:160], "address": str(raw.get("address") or "").strip()[:200]}
    try:
        b["price"] = max(0, int(round(float(raw.get("price") or 0))))
        b["persons"] = max(0, min(50, int(raw.get("persons") or 0)))
    except (TypeError, ValueError):
        b["price"], b["persons"] = 0, 0
    return b if b["date"] else None


def _same_city(a: str, b: str) -> bool:
    fa, fb = catalog.normalize_city(a), catalog.normalize_city(b)
    if fa and fb:
        return fa == fb
    a, b = a.lower().replace("ё", "е").strip(), b.lower().replace("ё", "е").strip()
    return bool(a and b) and a[:5] == b[:5]


def role(b: dict, origin: str, city: str) -> str:
    if b["kind"] in TRANSPORT:
        if _same_city(b["from_city"], city) or _same_city(b["to_city"], origin):
            return "back"
        return "out"
    return "stay" if b["kind"] == "hotel" else "event"


def locate(b: dict, city: str) -> dict:
    """Координаты места брони: место из каталога по названию, иначе адрес через подсказки (Photon/DaData)."""
    fn = catalog.normalize_city(city)
    if not fn or b["kind"] in TRANSPORT:
        return b
    data = catalog.load(fn)
    from . import tripster  # там же — разбор названий мест на значимые слова

    text = f"{b['title']} {b['venue']}".lower().replace("ё", "е")
    best, hits = None, 0
    for p in data["places"]:
        n = sum(w in text for w in tripster.keywords(p["name"], data["city"]))
        if n > hits:
            best, hits = p, n
    if best and b["kind"] != "hotel":
        return dict(b, lat=best["lat"], lon=best["lon"], place_id=best["id"])
    query = b["address"] or b["venue"]
    if query:
        try:
            found = [s for s in address.fetch(query, tuple(data["center"]), data["city"], 3) if s.get("lat") is not None]
        except Exception as e:  # подсказки адресов недоступны — место без координат, встанет в центр
            print(f"[bookings] адрес «{query}»: {type(e).__name__}: {e}", flush=True)
            found = []
        if found:
            return dict(b, lat=found[0]["lat"], lon=found[0]["lon"])
    return b


def parse(text: str, origin: str, city: str) -> list[dict]:
    """Текст документа -> брони с ролью в поездке и координатами."""
    text = text.strip()[:MAX_TEXT]
    if not text:
        return []
    if not wishes_ai.available():
        raise ValueError("Распознавание билетов работает с ключом YandexGPT в backend/.env — пока заполните вручную")
    raw = wishes_ai.ask(SYSTEM, f"Поездка: {origin} → {city}\n\nДокумент:\n{text}", _schema(), max_tokens=3000)
    if not isinstance(raw, dict):
        raise ValueError("Модель не смогла разобрать документ — заполните вручную")
    out = []
    for r in raw.get("bookings") or []:
        b = clean(r) if isinstance(r, dict) else None
        if b:
            b["role"] = role(b, origin, city)
            out.append(locate(b, city))
    return out


# ------------------------------------------------------------------ events in program

EVENT_MIN = {"theatre": 180, "museum": 120, "excursion": 120, "other": 90}
EVENT_CATEGORY = {"theatre": "theatre", "museum": "museum", "excursion": "excursion", "other": "event"}


def event_place(b: dict, i: int, center: list[float]) -> dict | None:
    """Бронь с временем -> место программы с жёстким началом (start) — планировщик строит день вокруг него."""
    start = _hm(b.get("start"))
    if not start or not _date(b.get("date")):
        return None
    end = _hm(b.get("end"))
    minutes = planner.parse_hm(end) - planner.parse_hm(start) if end else 0
    if minutes <= 0 or (b.get("kind") == "museum" and minutes < 60):  # «11:00–11:30» у музея — окно входа
        minutes = EVENT_MIN.get(b.get("kind"), 90)
    lat, lon = (b.get("lat"), b.get("lon")) if b.get("lat") is not None else center
    price = int(b.get("price") or 0)
    return {"id": f"event-{i}", "name": b.get("title") or "Бронь", "category": EVENT_CATEGORY.get(b.get("kind"), "event"),
            "source": "booking", "lat": lat, "lon": lon, "duration": minutes, "start": start, "date": b["date"],
            "price_group": price, "kids": True, "booked": True, "same_as": b.get("place_id"),
            "note": " · ".join(x for x in ("По вашему билету", b.get("venue"), b.get("address")) if x)}
