"""Планировщик поездки: вводные -> программа по часам.

Детерминированный алгоритм (без LLM), как в архитектуре:
1. Жёсткие слоты: дорога туда и обратно, заселение, выезд.
2. Каркас дня по темпу: подъём, завтрак, обед, отдых, ужин, вечерняя прогулка.
3. Отбор мест из каталога (приоритет, дети, сезон, бюджет, пожелания).
4. Раскладка по дням кластерами по координатам.
5. Порядок внутри дня — перебор перестановок с учётом часов работы и переходов.
6. Альтернативы для замены и оценка стоимости каждого пункта.

Время везде — минуты от полуночи текущего дня.
"""
from __future__ import annotations

import itertools
import math
import re
import uuid
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import catalog
from .schedule import buy_links

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]

PACE_CAPACITY = {"max": 4.0, "balanced": 3.0, "calm": 2.0, "relaxed": 1.5}
PACE_LABEL = {"max": "Увидеть побольше", "balanced": "2–3 места в день",
              "calm": "1–2 места в день", "relaxed": "На расслабоне"}
WEIGHT = {"museum": 1.0, "landmark": 0.5, "walk": 0.5, "daytrip": 2.0}
CITY_SIGHTS = ("museum", "landmark", "walk")      # места в городе
SIGHTS = (*CITY_SIGHTS, "daytrip")                 # и выезды за город (полдня, своя дорога — поле far)
DAYTRIP_MIN_SCORE = 5.0  # выезд за город ставим сам, только если он так важен; иначе — если о нём попросили

# Интересы: ключ совпадает с тегами мест в каталоге (tags); local_food — у заведений с местной кухней
INTERESTS = {"history": "История", "art": "Искусство", "architecture": "Архитектура",
             "nature": "Природа и парки", "views": "Виды на город", "local_food": "Местная кухня"}

# Оценки дороги из Москвы (прототип). В MVP — Яндекс.Расписания + цены партнёров.
ROUTES = {
    "Санкт-Петербург": {
        ("москва", "train"): {"minutes": 240, "price": 4900, "label": "Поезд «Сапсан»", "arrive": "train_moscow",
                              "station": "Ленинградский вокзал", "from_pt": (55.7765, 37.6553),
                              "link": ("Купить билет", "https://www.rzd.ru")},
        ("москва", "plane"): {"minutes": 90, "price": 6500, "label": "Самолёт", "arrive": "airport",
                              "station": "Аэропорт Шереметьево", "from_pt": (55.9726, 37.4146),
                              "link": ("Найти билеты", "https://travel.yandex.ru/avia/")},
        ("москва", "bus"): {"minutes": 660, "price": 2500, "label": "Автобус", "arrive": "bus",
                            "station": "Автовокзал", "from_pt": None, "link": None},
        ("москва", "car"): {"minutes": 540, "price": 6000, "label": "На автомобиле", "arrive": None,
                            "station": None, "link": None},
    },
    "Казань": {
        ("москва", "train"): {"minutes": 720, "price": 4500, "label": "Поезд", "arrive": "train",
                              "station": "Казанский вокзал", "from_pt": (55.7738, 37.6571),
                              "link": ("Купить билет", "https://www.rzd.ru")},
        ("москва", "plane"): {"minutes": 100, "price": 6000, "label": "Самолёт", "arrive": "airport",
                              "station": "Аэропорт Шереметьево", "from_pt": (55.9726, 37.4146),
                              "link": ("Найти билеты", "https://travel.yandex.ru/avia/")},
        ("москва", "bus"): {"minutes": 780, "price": 2800, "label": "Автобус", "arrive": "bus",
                            "station": "Автовокзал", "from_pt": None, "link": None},
        ("москва", "car"): {"minutes": 600, "price": 8000, "label": "На автомобиле по М-12", "arrive": None,
                            "station": None, "link": None},
    },
    "Нижний Новгород": {
        ("москва", "train"): {"minutes": 240, "price": 2200, "label": "Поезд «Ласточка»", "arrive": "train",
                              "station": "Курский вокзал", "from_pt": (55.7578, 37.6612),
                              "link": ("Купить билет", "https://www.rzd.ru")},
        ("москва", "plane"): {"minutes": 75, "price": 5000, "label": "Самолёт", "arrive": "airport",
                              "station": "Аэропорт Шереметьево", "from_pt": (55.9726, 37.4146),
                              "link": ("Найти билеты", "https://travel.yandex.ru/avia/")},
        ("москва", "bus"): {"minutes": 420, "price": 1500, "label": "Автобус", "arrive": "bus",
                            "station": "Автовокзал", "from_pt": None, "link": None},
        ("москва", "car"): {"minutes": 330, "price": 4500, "label": "На автомобиле по М-12", "arrive": None,
                            "station": None, "link": None},
    },
    "Калининград": {
        # поезд идёт через Литву (нужен транзитный документ) — при «Подобрать сами» выбираем самолёт
        ("москва", "train"): {"minutes": 1260, "price": 7000, "label": "Поезд через Литву", "arrive": "train",
                              "station": "Белорусский вокзал", "from_pt": (55.7766, 37.5811),
                              "link": ("Купить билет", "https://www.rzd.ru")},
        ("москва", "plane"): {"minutes": 140, "price": 7500, "label": "Самолёт", "arrive": "airport",
                              "station": "Аэропорт Шереметьево", "from_pt": (55.9726, 37.4146),
                              "link": ("Найти билеты", "https://travel.yandex.ru/avia/")},
    },
}
AUTO_TRAIN_MAX_MIN = 12 * 60  # дольше — при «Подобрать сами» летим самолётом
DEFAULT_ROUTE = {"minutes": 300, "price": 5000, "label": "Дорога", "arrive": "train",
                 "station": "Вокзал", "link": None}


def auto_transport(city: str, origin: str) -> str:
    """«Подобрать сами»: поезд, если он идёт не дольше 12 часов, иначе самолёт."""
    train = route_for(city, origin, "train")
    return "plane" if train and train["minutes"] > AUTO_TRAIN_MAX_MIN and route_for(city, origin, "plane") else "train"


def route_for(city: str, origin: str, transport: str) -> dict | None:
    """Оценка дороги origin -> city; city — название из каталога, origin — как ввёл пользователь."""
    return ROUTES.get(city, {}).get((origin.strip().lower().replace("ё", "е"), transport))

BOARDING_DEFAULT = {"train": 30, "plane": 120, "bus": 20, "car": 0}
NIGHT = 22 * 60  # с этого времени — только дорога, заселение и отдых: ни еды, ни мест
STEP_CHOICES = (5, 10, 15)

STAY_LABEL = {"any": "Неважно", "hotel": "Отель", "apartment": "Квартира или апартаменты"}
# Обычное время заселения и выезда, если у варианта жилья своё не указано
STAY_TIMES = {"hotel": ("14:00", "12:00"), "apartment": ("15:00", "11:00")}
# «в отель», «из отеля», «в отеле»
STAY_WORDS = {"hotel": ("отель", "отеля", "отеле"), "apartment": ("квартиру", "квартиры", "квартире")}

TRANSPORT_LABEL = {"auto": "Подобрать сами", "train": "Поезд", "plane": "Самолёт", "car": "Автомобиль", "bus": "Автобус"}


@dataclass
class TripInput:
    origin: str = "Москва"
    city: str = "Санкт-Петербург"
    start_date: str = "2026-10-27"
    start_time: str = "09:00"
    days: int = 3
    transport: str = "auto"
    adults: int = 2
    kids_ages: list[int] = field(default_factory=list)
    budget: int = 100000
    pace: str = "balanced"
    wishes: str = ""
    tags: list[str] = field(default_factory=list)
    return_time: str = "18:00"
    end_date: str | None = None          # дата возвращения; если задана, days считается из дат
    home_address: str = ""               # адрес, откуда выходим из дома
    to_station_min: int = 40             # дорога от дома до вокзала/аэропорта, мин
    boarding_min: int | None = None      # запас на посадку; None — по виду транспорта
    sight_buffer_min: int = 0            # запас к каждой достопримечательности, мин
    step_min: int = 5                    # шаг кнопок −/+ в маршруте
    min_rating: float = 4.5              # порог рейтинга мест (заработает с данными Яндекса)
    budget_tolerance_pct: int = 10       # допустимое превышение бюджета, %
    arrive_time: str = ""                # прибытие туда по расписанию (ЧЧ:ММ); пусто — оценка
    return_arrive_time: str = ""         # прибытие обратно по расписанию
    out_number: str = ""                 # номер поезда/рейса туда
    back_number: str = ""                # номер поезда/рейса обратно
    out_price: int = 0                   # билет туда «от», ₽ на взрослого (из расписания); 0 — оценка
    back_price: int = 0                  # билет обратно «от», ₽ на взрослого
    stay: str = "any"                    # где жить: any / hotel / apartment
    return_transport: str = ""           # на чём обратно; пусто — как туда
    stay_id: str = ""                    # выбранное в форме жильё из каталога; пусто — подберём по бюджету
    stay_url: str = ""                   # своя бронь: ссылка на объявление Суточно.ру (жильё — в custom_places)
    stay_manual: dict | None = None      # своя бронь на другом сайте, введённая вручную (manual_stay_place)
    bookings: list = field(default_factory=list)  # загруженные в форме билеты и брони (bookings.py)
    wishes_ai: dict | None = None        # разбор пожеланий моделью (wishes_ai.py); None — по ключевым словам

    @classmethod
    def from_dict(cls, d: dict) -> "TripInput":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and d[k] is not None}
        ti = cls(**known)
        ti.validate()
        return ti

    def validate(self) -> None:
        if catalog.normalize_city(self.city) is None:
            raise ValueError(f"Город «{self.city}» пока не поддерживается, есть: {', '.join(catalog.cities())}")
        if not 1 <= int(self.days) <= 7:
            raise ValueError("Длительность поездки — от 1 до 7 дней")
        if int(self.adults) < 1:
            raise ValueError("Нужен хотя бы один взрослый")
        if self.pace not in PACE_CAPACITY:
            raise ValueError("Неизвестный темп поездки")
        if self.transport not in TRANSPORT_LABEL:
            raise ValueError("Неизвестный вид транспорта")
        if not self.return_transport:
            self.return_transport = self.transport
        if self.return_transport not in TRANSPORT_LABEL:
            raise ValueError("Неизвестный вид транспорта обратно")
        if self.stay not in STAY_LABEL:
            raise ValueError("Неизвестный вид жилья")
        parse_hm(self.start_time)
        parse_hm(self.return_time)
        start = date.fromisoformat(self.start_date)
        if self.end_date:
            end = date.fromisoformat(self.end_date)
            if end < start:
                raise ValueError("Дата возвращения раньше даты выезда")
            self.days = (end - start).days + 1
            if self.days > 7:
                raise ValueError("В прототипе поездка — до 7 дней")
        self.days = int(self.days)
        self.end_date = (start + timedelta(days=self.days - 1)).isoformat()
        self.to_station_min = max(0, min(240, int(self.to_station_min)))
        if self.boarding_min not in (None, ""):
            self.boarding_min = max(0, min(240, int(self.boarding_min)))
        else:
            self.boarding_min = None
        self.sight_buffer_min = max(0, min(120, int(self.sight_buffer_min)))
        self.step_min = int(self.step_min) if int(self.step_min) in STEP_CHOICES else 5
        self.min_rating = round(min(5.0, max(0.0, float(self.min_rating))), 1)
        self.budget_tolerance_pct = max(0, min(50, int(self.budget_tolerance_pct)))
        for f in ("arrive_time", "return_arrive_time"):
            v = (getattr(self, f) or "").strip()
            if v:
                parse_hm(v)
            setattr(self, f, v)
        self.out_number = (self.out_number or "").strip()[:20]
        self.back_number = (self.back_number or "").strip()[:20]
        for f in ("out_price", "back_price"):
            try:
                v = int(float(getattr(self, f) or 0))
            except (TypeError, ValueError):
                v = 0
            setattr(self, f, min(max(v, 0), 500_000))
        if not isinstance(self.wishes_ai, dict):
            self.wishes_ai = None
        if not isinstance(self.stay_manual, dict) or not str(self.stay_manual.get("name") or "").strip():
            self.stay_manual = None
        self.bookings = [b for b in (self.bookings if isinstance(self.bookings, list) else []) if isinstance(b, dict)][:30]
        self.adults = int(self.adults)
        self.kids_ages = [int(a) for a in self.kids_ages]
        self.budget = int(self.budget)


# ---------------------------------------------------------------- utilities

def parse_hm(s: str) -> int:
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", (s or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Неверное время «{s}», нужно ЧЧ:ММ")
    return int(m.group(1)) * 60 + int(m.group(2))


def fmt_hm(minutes: int) -> str:
    minutes = int(round(minutes))
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def fmt_dur(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h} ч {m} мин" if h and m else f"{h} ч" if h else f"{m} мин"


def round5(x: float) -> int:
    return int(math.ceil(x / 5.0) * 5)


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def parse_wishes(text: str, tags: list[str]) -> set[str]:
    """Простые правила вместо LLM: «театр недорого» -> {theatre, cheap}."""
    t = (text or "").lower()
    found = set(tags or [])
    rules = {
        "theatre": r"театр|спектакл",
        "opera": r"балет|опер",
        "circus": r"цирк",
        "cheap": r"недорог|дешев|бюджетн|эконом",
        "no_long_walks": r"без долгой ходьбы|мало ходить|не ходить много",
        "boat": r"теплоход|кораблик|катер",
        "vegetarian": r"вегетариан|веган",
        "history": r"истори",
        "art": r"искусств|живопис|картин|галере",
        "architecture": r"архитектур|зодчеств",
        "nature": r"природ|парк|на свежем воздухе",
        "views": r"вид на город|видовы|смотров|панорам",
        "local_food": r"местн\w* кухн|национальн\w* кухн|татарск\w* кухн|(?:попробовать|местн\w*) блюд",
    }
    for key, pattern in rules.items():
        if re.search(pattern, t):
            found.add(key)
    if found & {"opera", "circus"}:
        found.add("theatre")
    return found


def ai_wishes(ai: dict, tags: list[str]) -> set[str]:
    """Флаги из разбора моделью; отмеченные в форме чипы важнее слов «без театра»."""
    found = set(ai.get("interests", [])) | set(ai.get("wishes", []))
    if found & {"opera", "circus"}:
        found.add("theatre")
    if "theatre" in ai.get("avoid", []):
        found -= {"theatre", "opera", "circus"}
    return found | set(tags or [])


def price_note(price: int) -> str:
    if not price:
        return ""
    return f" · билет от {price:,} ₽ за взрослого по Яндекс.Расписаниям".replace(",", "\u202f")


def leg_minutes(dep: str, arr: str, default: int, shift: int = 0) -> int:
    """Длительность в пути по расписанию; прибытие раньше отправления — значит, на следующие сутки.
    Время в расписании местное: shift — на сколько минут часы в месте прибытия отличаются от места отправления."""
    if not arr:
        return default
    m = parse_hm(arr) - parse_hm(dep) - shift
    return m if m > 0 else m + 24 * 60


NUMBER_LABEL = {"train": "поезд", "plane": "рейс", "bus": "рейс"}


def leg_note(transport: str, dep: str, arr: str, number: str, minutes: int) -> str:
    parts = [f"Отправление {dep}"]
    if arr:
        parts.append(f"прибытие {arr}" + (" (на следующий день)" if parse_hm(arr) <= parse_hm(dep) else ""))
    else:
        parts.append(f"в пути ≈ {fmt_dur(minutes)} — укажите прибытие по расписанию")
    if number:
        parts.append(f"{NUMBER_LABEL.get(transport, 'рейс')} № {number}")
    return " · ".join(parts)


def tz_note(ctx: "Ctx", outbound: bool) -> str:
    """Часы в городе поездки другие: в расписании время местное — у отправления своё, у прибытия своё."""
    if not ctx.shift:
        return ""
    h = abs(ctx.shift) // 60
    diff = f"на {h} ч {'меньше' if ctx.shift < 0 else 'больше'}, чем в Москве"
    return f" · время прибытия местное: {diff}" if outbound else f" · время отправления местное: {diff}"


class Ctx:
    """Всё, что нужно планировщику для одной поездки."""

    def __init__(self, ti: TripInput):
        self.ti = ti
        self.data = catalog.load(catalog.normalize_city(ti.city))
        self.city = self.data["city"]
        self.kids = len(ti.kids_ages)
        self.small_kids = any(a < 7 for a in ti.kids_ages)
        ai = ti.wishes_ai if ti.wishes_ai and ti.wishes_ai.get("text") == ti.wishes.strip() else None
        self.ai = ai  # разбор моделью относится только к тому тексту, по которому получен
        self.wishes = ai_wishes(ai, ti.tags) if ai else parse_wishes(ti.wishes, ti.tags)
        self.avoid: set[str] = set(ai.get("avoid", [])) if ai else set()   # категории мест
        self.must: set[str] = set(ai.get("must_visit", [])) if ai else set()
        self.skip_ids: set[str] = set(ai.get("skip_places", [])) if ai else set()
        def pick(t: str) -> str:
            return auto_transport(self.city, ti.origin) if t == "auto" else t

        # туда и обратно — каждое на своём: например, туда поездом, обратно самолётом
        transport, back = pick(ti.transport), pick(ti.return_transport or ti.transport)
        self.transport, self.back_transport = transport, back
        route, back_route = route_for(self.city, ti.origin, transport), route_for(self.city, ti.origin, back)
        self.route, self.back_route = route or DEFAULT_ROUTE, back_route or DEFAULT_ROUTE
        self.route_known, self.back_known = route is not None, back_route is not None
        self.people_price = ti.adults + 0.5 * self.kids
        self.start_date = date.fromisoformat(ti.start_date)
        # Уровень бюджета по сумме на человека в день после дороги
        # билет на взрослого: цена «от» из расписания, иначе оценка по маршруту
        self.out_price = ti.out_price or self.route["price"]
        self.back_price = ti.back_price or self.back_route["price"]
        travel = (self.out_price + self.back_price) * self.people_price
        per_day = max(0.0, ti.budget - travel) / max(1.0, ti.adults + 0.6 * self.kids) / ti.days
        self.tier = 1 if per_day < 6000 else 2 if per_day < 12000 else 3
        # «подешевле» — и по разбору модели, и по ключевым словам: как в подборе жилья в форме (/api/stays)
        if "cheap" in self.wishes or "cheap" in parse_wishes(ti.wishes, ti.tags):
            self.tier = min(self.tier, 2)
        self.walk_limit_km = 1.0 if "no_long_walks" in self.wishes else 1.3 if self.small_kids else 2.0
        self.walk_speed = 3.5 if self.kids else 4.5
        self.used_places: set[str] = set()
        self.skipped: set[str] = set()
        self.boarding = ti.boarding_min if ti.boarding_min is not None else BOARDING_DEFAULT.get(transport, 30)
        self.back_boarding = ti.boarding_min if ti.boarding_min is not None else BOARDING_DEFAULT.get(back, 30)
        # разница во времени с Москвой (поле tz каталога, в часах: Калининград −1); программа в городе — по местному
        self.shift = int(self.data.get("tz", 0)) * 60
        self.out_minutes = leg_minutes(ti.start_time, ti.arrive_time, self.route["minutes"], self.shift)
        self.back_minutes = leg_minutes(ti.return_time, ti.return_arrive_time, self.back_route["minutes"], -self.shift)
        arrive = parse_hm(ti.start_time) + self.out_minutes + self.shift  # местное время прибытия
        # ночной поезд: уезжаем вечером, приезжаем утром — первый день только дорога, жильё со второго
        self.night_out = ti.days >= 2 and transport != "car" and arrive >= 24 * 60 + 4 * 60
        self.arrive_at = arrive - 24 * 60 if self.night_out else arrive
        self.stay_from = self.start_date + timedelta(days=int(self.night_out))
        self.nights = max(1, ti.days - 1 - int(self.night_out))

        self.extra: dict[str, dict] = {}  # места этой поездки не из каталога (квартира с Суточно.ру)

    def place(self, pid: str) -> dict:
        return self.extra.get(pid) or self.data["by_id"][pid]

    def find(self, pid: str) -> dict | None:
        return self.extra.get(pid) or self.data["by_id"].get(pid)

    def places(self, *cats: str) -> list[dict]:
        return [p for p in [*self.data["places"], *self.extra.values()] if p["category"] in cats]


def trip_ctx(trip: dict) -> Ctx:
    ctx = Ctx(TripInput.from_dict(trip["input"]))
    ctx.extra = trip.get("custom_places") or {}
    return ctx


# --------------------------------------------------------------- transfers

def transfer(ctx: Ctx, a: dict | None, b: dict | None) -> dict | None:
    """Переход между точками: пешком, если близко, иначе метро/такси (с вещами — всегда транспорт)."""
    if not a or not b or a.get("lat") is None or b.get("lat") is None:
        return None
    km = catalog.haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) * 1.3
    if km < 0.05:
        return {"minutes": 0, "km": 0.0, "mode": "рядом"}
    far = b.get("far") or a.get("far")  # выезд за город: время и способ дороги — из каталога
    if far and km >= 5:
        return {"minutes": far["minutes"], "km": round(km, 1), "mode": far["mode"]}
    with_luggage = a.get("kind") == "travel" or b.get("kind") == "travel"
    if km <= ctx.walk_limit_km and not with_luggage:
        return {"minutes": max(5, round5(km / ctx.walk_speed * 60)), "km": round(km, 1), "mode": "пешком"}
    # по городу ≈ 22 км/ч с ожиданием, дальше 10 км (аэропорт, окраины) — ≈ 40 км/ч
    minutes = 12 + min(km, 10) / 22 * 60 + max(0.0, km - 10) / 40 * 60
    return {"minutes": round5(minutes), "km": round(km, 1), "mode": "метро или такси"}


# ---------------------------------------------------------------- opening

def opening(place: dict, day: date) -> tuple[int, int] | None:
    """(открытие, закрытие) в этот день или None, если закрыто. Без часов — весь день."""
    h = place.get("hours")
    months = place.get("months")
    if months and day.month not in months:
        return None
    if not h:
        return (0, 24 * 60)
    wd = day.weekday()
    if wd in h.get("closed", []):
        return None
    close = h.get("late", {}).get(str(wd), h["close"])
    opens, shuts = parse_hm(h["open"]), parse_hm(close)
    if shuts <= opens:  # закрывается после полуночи («09:00–01:00») — время дня считаем до 23:59
        shuts = 24 * 60 - 1
    return (opens, shuts)


def days_text(days: list[int]) -> str:
    """[4, 5] -> «пт и сб»"""
    names = [WEEKDAYS[d] for d in sorted(days)]
    return " и ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)


def hours_text(place: dict) -> str:
    """Часы работы места из каталога словами: «Работает 09:00–21:00, пт и сб до 01:00, выходной пн»."""
    h = place.get("hours")
    if not h or place.get("placeholder"):  # у заглушки часы — ориентир для подбора, не настоящие
        return ""
    if h["open"] == "00:00" and h["close"] in ("00:00", "23:59"):
        out = "Работает круглосуточно"
    else:
        out = f"Работает {h['open']}–{h['close']}"
    late = h.get("late") or {}
    for close in sorted(set(late.values())):
        out += f", {days_text([int(d) for d, c in late.items() if c == close])} до {close}"
    if h.get("closed"):
        word = "выходные" if len(h["closed"]) > 1 else "выходной"
        out += f", {word} {days_text(h['closed'])}"
    return out


def rating_text(place: dict) -> str:
    """«★ 4,5 на Яндекс Картах (211)» — рейтинг с Яндекс Карт, если он есть в данных места."""
    if not place.get("rating"):
        return ""
    out = f"★ {place['rating']:.1f}".replace(".", ",") + " на Яндекс Картах"
    return out + (f" ({place['reviews']})" if place.get("reviews") else "")


def place_cost(ctx: Ctx, p: dict) -> int:
    # price_group — цена за экскурсию на всю группу (Трипстер)
    return int(p.get("price_adult", 0) * ctx.ti.adults + p.get("price_child", 0) * ctx.kids + p.get("price_group", 0))


def food_cost(ctx: Ctx, p: dict, light: bool = False) -> int:
    k = 0.6 if light else 1.0
    return int(round(p["avg_check"] * (ctx.ti.adults + 0.6 * ctx.kids) * k, -1))


# ------------------------------------------------------------------ items

def nearby_link(p: dict, what: str) -> str:
    return f"https://yandex.ru/maps/?ll={p['lon']},{p['lat']}&z=16&text={quote(what)}"


YANDEX_GEO_ID = {"Санкт-Петербург": 2, "Москва": 213, "Казань": 43, "Нижний Новгород": 47, "Калининград": 22}


def hotels_link(ctx: Ctx) -> str:
    """Поиск отелей на Яндекс Путешествиях на даты поездки и состав группы."""
    ti = ctx.ti
    checkout = ctx.stay_from + timedelta(days=ctx.nights)
    q = {"adults": ti.adults, "checkinDate": ctx.start_date.isoformat(), "checkoutDate": checkout.isoformat()}
    if ti.kids_ages:
        q["childrenAges"] = ",".join(str(a) for a in ti.kids_ages)
    geo = YANDEX_GEO_ID.get(ctx.city)
    if geo:
        q["geoId"] = geo
    return "https://travel.yandex.ru/hotels/search/?" + "&".join(f"{k}={quote(str(v), safe=',')}" for k, v in q.items())


def stay_dates(ctx: Ctx) -> tuple[date, date]:
    return ctx.stay_from, ctx.stay_from + timedelta(days=ctx.nights)


def sutochno_link(ctx: Ctx) -> str:
    a, b = stay_dates(ctx)
    q = {"occupied": f"{a.isoformat()};{b.isoformat()}", "guests_adults": ctx.ti.adults, "term": ctx.city}
    return "https://sutochno.ru/front/searchapp/search?" + "&".join(f"{k}={quote(str(v))}" for k, v in q.items())


OSTROVOK_CITY = {"Санкт-Петербург": "russia/st._petersburg", "Казань": "russia/kazan"}
TRIPSTER_CITY = {"Санкт-Петербург": "Saint_Petersburg", "Казань": "Kazan", "Нижний Новгород": "Nizhny_Novgorod",
                 "Калининград": "Kaliningrad"}
TRIPSTER_KINDS = ("museum", "sight", "walk", "daytrip")  # пункты, к которым предлагаем экскурсии с гидом


AVITO_CITY = {"Санкт-Петербург": "sankt-peterburg", "Казань": "kazan", "Нижний Новгород": "nizhniy_novgorod",
              "Калининград": "kaliningrad"}


def avito_link(ctx: Ctx) -> str:
    """Квартиры посуточно на Авито в городе поездки (даты Авито в ссылке не принимает)."""
    return f"https://www.avito.ru/{AVITO_CITY.get(ctx.city, 'rossiya')}/kvartiry/sdam/posutochno"


def tripcom_link(ctx: Ctx) -> str:
    a, b = stay_dates(ctx)
    q = {"keyword": ctx.city, "checkin": a.isoformat(), "checkout": b.isoformat(), "adult": ctx.ti.adults}
    return "https://ru.trip.com/hotels/list?" + "&".join(f"{k}={quote(str(v))}" for k, v in q.items())


def manual_stay_place(m: dict) -> dict:
    """Своя бронь с любого сайта (Авито, Trip.com, Островок…), введённая в форме: место для custom_places."""
    name = str(m.get("name") or "").strip()[:120]
    try:
        lat, lon = float(m["lat"]), float(m["lon"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Жильё: выберите адрес из подсказок — по нему построим дорогу до мест программы") from None
    try:
        price = max(0, int(float(str(m.get("price_night") or 0).replace(" ", "").replace("\u202f", ""))))
    except ValueError:
        raise ValueError("Жильё: цена за ночь — числом, в рублях") from None
    p = {"id": "manual-stay", "name": name, "category": "hotel", "source": "manual",
         "stay": "apartment" if m.get("kind") == "apartment" else "hotel", "lat": lat, "lon": lon,
         "price_night": price, "tier": 2, "capacity": 4, "address": str(m.get("address") or "").strip()[:200],
         "url": str(m.get("url") or "").strip()[:500], "note": "Своя бронь"}
    for k in ("check_in", "check_out"):
        v = str(m.get(k) or "").strip()
        if v:
            parse_hm(v)  # «Неверное время …» — понятная ошибка формы
            p[k] = v
    if p["address"]:
        p["note"] += " · " + p["address"]
    return p


def tripster_link(ctx: Ctx) -> str | None:
    slug = TRIPSTER_CITY.get(ctx.city)
    return f"https://experience.tripster.ru/experience/{slug}/" if slug else None


def ostrovok_link(ctx: Ctx) -> str:
    a, b = stay_dates(ctx)
    guests = ctx.ti.adults + len(ctx.ti.kids_ages)
    return (f"https://ostrovok.ru/hotel/{OSTROVOK_CITY.get(ctx.city, 'russia')}/"
            f"?dates={a:%d.%m.%Y}-{b:%d.%m.%Y}&guests={guests}")


def links_for(ctx: Ctx, p: dict) -> list[dict]:
    out = []
    if p["category"] == "food" and p.get("placeholder"):
        what = "кафе" if "Кафе" in p["name"] or "Кофейня" in p["name"] else "ресторан"
        return [{"label": f"Выбрать {what} рядом — Яндекс Карты", "url": nearby_link(p, what)}]
    if p["category"] == "hotel" and p.get("placeholder") and stay_kind(p) == "apartment":
        return [{"label": "Квартиры на даты — Суточно.ру", "url": sutochno_link(ctx)},
                {"label": "Квартиры и апартаменты на даты — Островок", "url": ostrovok_link(ctx)},
                {"label": "Квартиры рядом на Яндекс Картах", "url": nearby_link(p, "квартиры посуточно")}]
    if p["category"] == "hotel" and p.get("placeholder"):
        return [{"label": "Подобрать и забронировать — Яндекс Путешествия", "url": hotels_link(ctx)},
                {"label": "Отели рядом на Яндекс Картах", "url": nearby_link(p, "гостиница")}]
    if p.get("source") == "manual":  # своя бронь на другом сайте
        out = [{"label": "Бронь на сайте", "url": p["url"]}] if p.get("url", "").startswith("http") else []
        return out + [{"label": "Яндекс Карты", "url": f"https://yandex.ru/maps/?pt={p['lon']},{p['lat']}&z=17&l=map"}]
    if p.get("source") == "yandex":  # своё кафе по ссылке с Яндекс Карт
        return [{"label": "Яндекс Карты", "url": p["url"]}]
    if p.get("source") == "tripster":  # ссылка уже партнёрская по правилам Трипстера, с его маркировкой
        return [{"label": "Забронировать — Трипстер", "url": p["url"], "ad": p.get("ad"), "direct": True}]
    if p.get("source") == "sutochno":
        return [{"label": "Объявление на даты — Суточно.ру", "url": listing_link(ctx, p)},
                {"label": "Яндекс Карты", "url": f"https://yandex.ru/maps/?pt={p['lon']},{p['lat']}&z=17&l=map"}]
    if p.get("url"):
        label = {"museum": "Сайт и билеты", "theatre": "Афиша и билеты", "food": "Сайт и меню",
                 "hotel": "Сайт отеля"}.get(p["category"], "Официальный сайт")
        out.append({"label": label, "url": p["url"]})
    if p["category"] == "hotel":
        if p.get("booking_url"):
            out.append({"label": "Забронировать — Островок", "url": p["booking_url"]})
        out.append({"label": "Цены на даты — Яндекс Путешествия", "url": hotels_link(ctx)})
    out.append({"label": "Яндекс Карты", "url": catalog.maps_link(p["name"], ctx.city)})
    return out


def make_item(kind: str, title: str, duration: int, *, place: dict | None = None, note: str = "",
              fixed: int | None = None, cost_kind: str = "none", estimate: int = 0,
              links: list | None = None, point: dict | None = None, alternatives: list | None = None,
              shift: int = 0) -> dict:
    coords = point or place
    return {
        "id": new_id(),
        "kind": kind,
        "title": title,
        "note": note,
        "place_id": place["id"] if place else None,
        "lat": coords.get("lat") if coords else None,
        "lon": coords.get("lon") if coords else None,
        "duration": int(duration),
        "default_duration": int(duration),
        "fixed_start": fixed,
        "cost": {"kind": cost_kind, "estimate": int(estimate)},
        "doc": None,
        "links": links or [],
        "alternatives": alternatives or [],
        "user_changed": False,
        "meal": None,
        "far": None,
        "start": None, "end": None, "transfer": None, "wait": 0, "warnings": [],
        "shift": shift,  # дорога в другой часовой пояс: после неё часы переводятся на столько минут
    }


def attraction_item(ctx: Ctx, p: dict) -> dict:
    kind = {"museum": "museum", "theatre": "theatre", "landmark": "sight", "daytrip": "daytrip",
            "excursion": "excursion", "event": "event"}.get(p["category"], "walk")
    cost_kind = "ticket" if place_cost(ctx, p) > 0 else "none"
    fixed = parse_hm(p["start"]) if p.get("start") else None
    dur = p["duration"] + (ctx.ti.sight_buffer_min if kind != "theatre" else 0)
    it = make_item(kind, p["name"], dur, place=p, note=p.get("note", ""),
                   fixed=fixed, cost_kind=cost_kind, estimate=place_cost(ctx, p), links=links_for(ctx, p))
    it["far"] = p.get("far")
    return it


MEAL_TITLE = {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин", "snack": "Перекус"}


def food_item(ctx: Ctx, p: dict, meal: str, light: bool = False) -> dict:
    title = MEAL_TITLE[meal]
    if light and meal == "dinner":
        title = "Лёгкий ужин"
    if meal in ("breakfast", "snack"):
        dur, light = 45, True
    else:
        dur = 60 if meal == "lunch" or light else (90 if ctx.ti.pace in ("max",) else 120)
        if ctx.ti.pace == "relaxed" and meal == "lunch":
            dur = 90
    # хвост описания у всех мест еды одинаковый: чек, своё описание, рейтинг Яндекса, часы работы
    parts = [f"Средний чек ≈ {rub(p['avg_check'])} на человека", p.get("note", ""),
             rating_text(p), hours_text(p)]
    if p.get("checked") and "уточни" not in (p.get("note") or ""):
        parts.append("часы и цены на октябрь 2026, уточните перед визитом")
    it = make_item("food", f"{title} · {p['name']}", dur, place=p, note=" · ".join(n for n in parts if n),
                   cost_kind="receipt", estimate=food_cost(ctx, p, light), links=links_for(ctx, p))
    it["meal"] = "dinner_light" if light and meal == "dinner" else meal
    return it


# ------------------------------------------------------------- selection

def score(ctx: Ctx, p: dict) -> float:
    s = float(p.get("priority", 1))
    if ctx.kids and p.get("kids_bonus"):
        s += 2.5
    if ctx.kids and not p.get("kids", True):
        s -= 10
    if "boat" in ctx.wishes and p["id"] == "boat":
        s += 3
    s += 1.5 * min(2, len(ctx.wishes & INTERESTS.keys() & set(p.get("tags", []))))
    if asked_for(ctx, p):
        s += 4
    if p["category"] in ctx.avoid or p["id"] in ctx.skip_ids:  # «без музеев», «в Раифу не хотим»
        s -= 10
    return s


def asked_for(ctx: Ctx, p: dict) -> bool:
    """Место названо в пожеланиях: «хотим в Свияжск». С разбором моделью — её список must_visit,
    иначе шаблон keywords из каталога (он не понимает «не хотим в Свияжск»)."""
    if ctx.ai:
        return p["id"] in ctx.must
    return bool(p.get("keywords") and re.search(p["keywords"], ctx.ti.wishes.lower()))


def choose_theatre(ctx: Ctx) -> dict | None:
    named = [p for p in ctx.places("theatre") if p["id"] in ctx.must]
    if named:  # театр назван в пожеланиях
        return named[0]
    if "theatre" not in ctx.wishes:
        return None
    cands = ctx.places("theatre")
    if ctx.kids:
        cands = [p for p in cands if p.get("kids")] or cands
    if "circus" in ctx.wishes:
        cands = [p for p in cands if p.get("genre") == "circus"] or cands
    elif "opera" in ctx.wishes:
        cands = [p for p in cands if p.get("genre") == "opera"] or cands
    else:
        cands = [p for p in cands if p.get("genre") != "circus"] or cands
    cands = [p for p in cands if p.get("budget_level", 1) <= ctx.tier] or cands
    if "cheap" in ctx.wishes:
        return min(cands, key=lambda p: (p["price_adult"], -p["priority"]))
    return max(cands, key=lambda p: (p["priority"], -p["price_adult"]))


def stay_kind(p: dict) -> str:
    return p.get("stay", "hotel")


def stay_times(p: dict) -> tuple[int, int, bool]:
    """(заселение, выезд, оценка ли это): своё время варианта или обычное для вида жилья."""
    cin, cout = STAY_TIMES[stay_kind(p)]
    return parse_hm(p.get("check_in") or cin), parse_hm(p.get("check_out") or cout), not (p.get("check_in") and p.get("check_out"))


def stay_units(ctx: Ctx, p: dict) -> int:
    """Сколько номеров (квартир) нужно: в номер по 2 взрослых, в квартиру — по её вместимости."""
    if stay_kind(p) == "apartment":
        return max(1, math.ceil((ctx.ti.adults + ctx.kids) / p.get("capacity", 4)))
    return max(1, math.ceil(ctx.ti.adults / 2))


def stay_estimate(ctx: Ctx, p: dict) -> int:
    return p["price_night"] * ctx.nights * stay_units(ctx, p)


def stay_times_note(p: dict) -> str:
    cin, cout, est = stay_times(p)
    return f"Заселение с {fmt_hm(cin)}, выезд до {fmt_hm(cout)}" + (" — обычное время, уточните в брони" if est else "")


def hotel_for(ctx: Ctx) -> dict:
    want = "hotel" if ctx.ti.stay == "any" else ctx.ti.stay
    pool = [h for h in ctx.places("hotel") if stay_kind(h) == want] or ctx.places("hotel")
    pool.sort(key=lambda h: h["tier"])
    return next((h for h in pool if h["tier"] == ctx.tier), pool[0])


def chosen_stay(ctx: Ctx) -> dict:
    """Жильё, выбранное в форме, иначе — по бюджету и виду жилья."""
    p = ctx.find(ctx.ti.stay_id) if ctx.ti.stay_id else None
    return p if p and p["category"] == "hotel" else hotel_for(ctx)


def stay_options(ctx: Ctx) -> dict:
    """Варианты жилья для формы: цена за ночь и за всю поездку на эту группу; recommended — что подберём сами."""
    nights = ctx.nights
    rows = [{"id": p["id"], "name": p["name"], "kind": stay_kind(p), "price_night": p["price_night"],
             "units": stay_units(ctx, p), "total": stay_estimate(ctx, p), "note": p.get("note", ""),
             "times": "Заезд с {}, выезд до {}".format(*map(fmt_hm, stay_times(p)[:2])), "placeholder": bool(p.get("placeholder")), "tier": p.get("tier", 1),
             "wiki": p.get("wiki"), "wiki_area": bool(p.get("wiki_area"))}
            for p in ctx.places("hotel") if not p.get("source")]  # квартиры по ссылке живут в своих поездках
    rows.sort(key=lambda r: (r["kind"] != "hotel", r["price_night"]))
    return {"nights": nights, "recommended": hotel_for(ctx)["id"], "stays": rows}


def stay_text(it: dict, p: dict) -> None:
    """Подписи пунктов, связанных с жильём (stay_role), — под выбранное жильё: отель или квартира."""
    role, apt = it.get("stay_role"), stay_kind(p) == "apartment"
    w_to, w_from, w_in = STAY_WORDS[stay_kind(p)]
    cin, cout, _ = stay_times(p)
    if role == "checkin" and it.get("checkin_early"):
        it["title"] = f"Оставить вещи · {p['name']}"
        first = (f"Ключи обычно выдают с {fmt_hm(cin)} — договоритесь с хозяином оставить вещи раньше "
                 "или сдайте их в камеру хранения на вокзале") if apt else \
            f"Номер будет готов к {fmt_hm(cin)} — оставим вещи на стойке и заселимся позже"
        it["note"] = f"{first}. {stay_times_note(p)}"
    elif role == "checkin":
        it["title"] = f"Заселение · {p['name']}"
        it["note"] = stay_times_note(p)
    elif role == "checkin_late":
        it["title"] = it["title"].split(" · ")[0] + " · " + p["name"]
        it["note"] = stay_times_note(p)
    elif role == "back":
        it["title"] = f"Возвращение в {w_to}" + (", отдых" if "отдых" in it["title"] else "")
    elif role == "breakfast" and not it.get("place_id"):
        it["title"] = "Завтрак в кафе рядом" if apt else f"Завтрак в {w_in}"
        it["note"] = ("Выберите кафе кнопкой «Заменить» — или позавтракайте дома" if apt
                      else "Если завтрак не включён в бронь — замените на кафе рядом")
    elif role == "checkout":
        it["title"] = f"Выезд из {w_from}"
        it["note"] = (f"Выезд до {fmt_hm(cout)}. "
                      + ("Вещи — договориться с хозяином или сдать в камеру хранения на вокзале" if apt
                         else "Вещи — в камеру хранения отеля"))


def nearest_food(ctx: Ctx, point: dict, day: date, at: int, used: set[str], meal: str, n: int = 1) -> list[dict]:
    cap = {1: 1500, 2: 2600, 3: 10 ** 6}[ctx.tier]
    out = []
    for p in ctx.places("food"):
        if p["id"] in used or meal not in p.get("meal", []):
            continue
        if p["avg_check"] > cap:
            continue
        o = opening(p, day)
        if o is None or not (o[0] <= at <= o[1] - 45):
            continue
        out.append(p)
    local = "local_food" in ctx.wishes  # местная кухня: готовы пройти лишние 1,2 км
    out.sort(key=lambda p: catalog.haversine_km(point["lat"], point["lon"], p["lat"], p["lon"])
             - (1.2 if local and "local_food" in p.get("tags", []) else 0))
    return out[:n]


# --------------------------------------------------------------- the day

@dataclass
class DayPlan:
    index: int
    date: date
    kind: str  # first / middle / last / single
    capacity: float
    places: list[dict] = field(default_factory=list)
    theatre: dict | None = None
    fixed: list[dict] = field(default_factory=list)  # брони с жёстким временем днём (музей, экскурсия по билету)


def day_capacity(ctx: Ctx, kind: str) -> float:
    cap = PACE_CAPACITY[ctx.ti.pace]
    if ctx.kids:
        cap = max(1.0, cap - 0.5)
    arrive = ctx.arrive_at + 60
    leave = parse_hm(ctx.ti.return_time) - 90
    if kind == "night":
        cap = 0.0
    elif kind == "first":
        cap *= max(0.0, min(1.0, (19 * 60 - arrive) / (10 * 60)))
    elif kind == "last":
        cap *= max(0.0, min(1.0, (leave - 10 * 60) / (9 * 60)))
    elif kind == "single":
        cap *= max(0.0, min(1.0, (leave - arrive) / (9 * 60)))
    return round(cap * 2) / 2


def assign_daytrips(ctx: Ctx, days: list[DayPlan]) -> None:
    """Выезд за город — на полный день в середине поездки, по одному в день, лучше в день без театра."""
    order = sorted(days, key=lambda d: d.theatre is not None)
    for p in sorted(ctx.places("daytrip"), key=lambda p: -score(ctx, p)):
        asked = asked_for(ctx, p)
        if score(ctx, p) < DAYTRIP_MIN_SCORE and not asked:
            continue
        d = next((d for d in order if d.kind == "middle" and not d.places and not d.fixed and opening(p, d.date) is not None
                  and (d.capacity >= WEIGHT["daytrip"] or asked)), None)
        if d:
            d.places = [p]


def assign_days(ctx: Ctx, days: list[DayPlan]) -> None:
    """Кластеризация: у каждого дня «семя» — лучшее свободное место, дальше ближайшие."""
    assign_daytrips(ctx, days)
    for d in days:  # брони по билетам — в свой день, остальное раскладываем вокруг них
        d.places = d.fixed + d.places
    booked = {p.get("same_as") for d in days for p in d.fixed + ([d.theatre] if d.theatre else [])}
    cands = [p for p in ctx.places(*CITY_SIGHTS) if score(ctx, p) > -5 and p["id"] not in booked]
    cands.sort(key=lambda p: -score(ctx, p))
    used: set[str] = set()
    order = sorted(days, key=lambda d: -d.capacity)
    for d in order:
        if d.capacity <= 0:
            continue
        load = sum(WEIGHT.get(p["category"], 1.0) for p in d.places)
        chosen = [p for p in d.places if p["category"] != "daytrip"]  # центр дня — по местам в городе и броням
        while load < d.capacity:
            rest = [p for p in cands if p["id"] not in used and opening(p, d.date) is not None
                    and (not d.places or load + WEIGHT.get(p["category"], 1.0) <= d.capacity + 0.01)]
            if not rest:
                break
            if chosen:
                cx = sum(p["lat"] for p in chosen) / len(chosen)
                cy = sum(p["lon"] for p in chosen) / len(chosen)
                best = min(rest, key=lambda p: catalog.haversine_km(cx, cy, p["lat"], p["lon"]) - 0.35 * score(ctx, p))
            else:
                best = rest[0]  # семя дня — самое важное из открытых
            chosen.append(best)
            d.places.append(best)
            used.add(best["id"])
            load += WEIGHT.get(best["category"], 1.0)


CHECKIN_MIN = 30


def checkin_step(ctx: Ctx, point: dict, t: int, hotel: dict, slack: int) -> tuple[int, float] | None:
    """Заехать заселиться: (время после заселения, стоимость) или None, если номер ещё не готов
    (ждать дольше slack минут)."""
    tr = transfer(ctx, point, hotel)
    arrive = t + (tr["minutes"] if tr else 0)
    cin = stay_times(hotel)[0]
    if arrive < cin - slack:
        return None
    wait = max(0, cin - arrive)
    return arrive + wait + CHECKIN_MIN, (tr["minutes"] if tr else 0) + wait * 0.5


def schedule_activities(ctx: Ctx, d: DayPlan, start: int, start_point: dict, places: list[dict],
                        lunch_done: bool, checkin: dict | None = None) -> tuple[float, list[tuple], int, dict, bool]:
    """Лучший порядок мест + место обеда. Возвращает (стоимость, шаги, время, точка, обед_был).

    checkin — жильё, где утром только оставили вещи: заселяемся после обеда (номер готов к тому времени
    или ждать не больше получаса), иначе после первого места, к концу которого номер готов."""
    best = None
    perms = itertools.permutations(places) if len(places) <= 6 else [tuple(places)]
    for perm in perms:
        t, point, cost, steps, had_lunch = start, start_point, 0.0, [], lunch_done
        used_food: set[str] = set()
        pending = checkin
        for p in list(perm) + [None]:
            lunch_there = p is not None and (p.get("far") or {}).get("lunch")  # обед на выезде, в программе выезда
            if not had_lunch and (t >= 12 * 60 + 30 or p is None) and t < 16 * 60 and not lunch_there:
                f = nearest_food(ctx, point, d.date, t, used_food, "lunch")
                if f:
                    tr = transfer(ctx, point, f[0])
                    t += (tr["minutes"] if tr else 0) + (90 if ctx.ti.pace == "relaxed" else 60)  # как в food_item
                    cost += tr["minutes"] if tr else 0
                    steps.append(("lunch", f[0]))
                    used_food.add(f[0]["id"])
                    point = f[0]
                    r = checkin_step(ctx, point, t, pending, 30) if pending else None
                    if r:
                        t, extra = r
                        cost += extra
                        steps.append(("checkin", pending))
                        point, pending = pending, None
                had_lunch = True
            if p is None:
                break
            tr = transfer(ctx, point, p)
            t += tr["minutes"] if tr else 0
            cost += tr["minutes"] if tr else 0
            if p.get("far"):
                cost += max(0, t - 11 * 60)  # выезд за город — с утра, чтобы вернуться засветло
            if p.get("booked"):  # бронь по билету: ровно к её времени, опоздать нельзя
                at = parse_hm(p["start"])
                cost += 5000 if t > at else (at - t) * 0.3
                t = max(t, at)
            o = opening(p, d.date)
            if o:
                if t < o[0]:
                    cost += (o[0] - t) * 0.5
                    t = o[0]
                if t + p["duration"] > o[1]:
                    cost += 500
            t += p["duration"]
            steps.append(("place", p))
            point = p
            had_lunch = had_lunch or bool(lunch_there)
            r = checkin_step(ctx, point, t, pending, 0) if pending and had_lunch else None
            if r:
                t, extra = r
                cost += extra
                steps.append(("checkin", pending))
                point, pending = pending, None
        if best is None or cost < best[0]:
            best = (cost, steps, t, point, had_lunch)
    if best is None:
        return (0.0, [], start, start_point, lunch_done)
    return best


def rasp_link(ctx: Ctx, day: date, outbound: bool) -> list[dict]:
    """Ссылка на расписание Яндекса на нужную дату — сверить время и номер поезда или рейса."""
    kind = {"train": "train", "plane": "plane", "bus": "bus"}.get(ctx.transport if outbound else ctx.back_transport)
    if not kind:
        return []
    a, b = (ctx.ti.origin, ctx.city) if outbound else (ctx.city, ctx.ti.origin)
    q = f"fromName={quote(a)}&toName={quote(b)}&when={day.isoformat()}"
    return [{"label": "Расписание на Яндексе", "url": f"https://rasp.yandex.ru/search/{kind}/?{q}"}]


def buy_link(ctx: Ctx, day: date, outbound: bool) -> list[dict]:
    """Купить билет на Яндекс Путешествиях; для неизвестных направлений — общая ссылка маршрута."""
    a, b = (ctx.ti.origin, ctx.city) if outbound else (ctx.city, ctx.ti.origin)
    links = buy_links(a, b, ctx.transport if outbound else ctx.back_transport, day.isoformat(), ctx.ti.adults)
    if links:
        return [{"label": f"Купить билет: {lk['label']}", "url": lk["url"]} for lk in links]
    route = ctx.route if outbound else ctx.back_route
    return [{"label": route["link"][0], "url": route["link"][1]}] if route.get("link") else []


def arrival_point(ctx: Ctx, route: dict) -> dict | None:
    """Вокзал, аэропорт или автовокзал маршрута в городе поездки; ключ train подходит и к train_moscow."""
    key = route.get("arrive")
    if not key:
        return None
    pts = ctx.data["arrival_points"]
    return pts.get(key) or next((v for k, v in pts.items() if k.split("_")[0] == key.split("_")[0]), None)


def build_day(ctx: Ctx, d: DayPlan, hotel: dict, used_food: set[str]) -> list[dict]:
    ti = ctx.ti
    items: list[dict] = []
    route = ctx.route
    arrive_pt = arrival_point(ctx, ctx.route)       # куда приезжаем
    depart_pt = arrival_point(ctx, ctx.back_route)  # откуда уезжаем обратно
    dep = parse_hm(ti.start_time)
    ret = parse_hm(ti.return_time)

    late_checkin = False

    def add(it: dict) -> dict:
        items.append(it)
        return it

    def stay(it: dict, role: str, **extra) -> dict:
        it["stay_role"] = role
        it.update(extra)
        stay_text(it, hotel)
        return it

    def back(rest: bool, duration: int) -> dict:
        """Возвращение в жильё; в первый день при раннем приезде это и есть заселение."""
        nonlocal late_checkin
        it = make_item("rest", "Возвращение, отдых" if rest else "Возвращение", duration, point=hotel)
        if late_checkin:
            late_checkin = False
            it.update(kind="hotel", place_id=hotel["id"], title="Заселение и отдых" if rest else "Заселение")
            return add(stay(it, "checkin_late"))
        return add(stay(it, "back"))

    # ---- утро / приезд
    if d.kind in ("first", "single", "night"):
        if d.kind != "night" and ctx.night_out:  # приехали ночным поездом: день начинается с вокзала
            add(make_item("travel", f"Прибытие: {(arrive_pt or {}).get('name', ctx.city)}", 15, fixed=ctx.arrive_at,
                          point=arrive_pt, note="Ночь в дороге позади"))
        elif ctx.transport == "car":
            add(make_item("travel", f"Выезд из {ti.origin}", ctx.out_minutes, fixed=dep, shift=ctx.shift,
                          note="Ориентировочно, с остановками" if not ti.arrive_time else f"Выезд {ti.start_time}, прибытие {ti.arrive_time}",
                          cost_kind="receipt", estimate=int(route["price"] / 2)))
        else:
            station = route.get("station") or "вокзал"
            home = ti.home_address.strip()
            go_links = []
            if home and route.get("from_pt"):
                lat, lon = route["from_pt"]
                go_links = [{"label": "Маршрут до вокзала в Яндекс Картах",
                             "url": f"https://yandex.ru/maps/?rtext={quote(home)}~{lat},{lon}&rtt=mt"}]
            go = make_item("travel", "Выход из дома", ti.to_station_min, fixed=dep - ctx.boarding - ti.to_station_min,
                           note=(f"{home} → {station}" if home else f"Дорога до места отправления: {station}")
                           + f", ≈ {fmt_dur(ti.to_station_min)}", links=go_links)
            add(go)
            if ctx.boarding:
                add(make_item("travel", f"{station}: посадка", ctx.boarding, fixed=dep - ctx.boarding,
                              note="Запас времени на посадку — меняется в настройках поездки"))
            link = buy_link(ctx, d.date, True)
            label = TRANSPORT_LABEL[ctx.transport] if ti.out_number else route["label"]
            title = f"{label} {ti.origin} → {ctx.city}" + (f", № {ti.out_number}" if ti.out_number else "")
            add(make_item("travel", title, ctx.out_minutes, fixed=dep, shift=ctx.shift,
                          point=arrive_pt, cost_kind="ticket",
                          estimate=int(ctx.out_price * ctx.people_price), links=link + rasp_link(ctx, d.date, True),
                          note=leg_note(ctx.transport, ti.start_time, ti.arrive_time, ti.out_number, ctx.out_minutes)
                          + tz_note(ctx, True) + price_note(ti.out_price)))
        if d.kind == "night":
            return items  # программа в городе — со следующего утра
        if d.kind == "single":
            point = arrive_pt or hotel
        else:
            # Приезжаем раньше времени заселения — сначала только оставляем вещи, заселяемся при возвращении
            arrive = simulate_end(ctx, items)
            prev = next((i for i in reversed(items) if i.get("lat") is not None), None)
            tr = transfer(ctx, prev, hotel)
            late_checkin = arrive + (tr["minutes"] if tr else 0) < stay_times(hotel)[0]
            add(stay(make_item("hotel", "", 15 if late_checkin else 45, place=hotel, cost_kind="booking",
                               estimate=stay_estimate(ctx, hotel), links=links_for(ctx, hotel)),
                     "checkin", checkin_early=late_checkin))
            point = hotel
        if ctx.night_out and simulate_end(ctx, items) <= 10 * 60 + 30:  # с ночного поезда — сначала завтрак
            f = nearest_food(ctx, point, d.date, simulate_end(ctx, items) + 15, used_food, "breakfast")
            if f:
                add(food_item(ctx, f[0], "breakfast"))
                used_food.add(f[0]["id"])
                point = f[0]
    else:
        wake = parse_hm("09:30") if ti.pace == "relaxed" else parse_hm("08:30")
        add(stay(make_item("rest", "Подъём", 30, fixed=wake, point=hotel), "wake"))
        add(stay(make_item("food", "", 60, point=hotel), "breakfast", meal="breakfast"))
        if d.kind == "last":
            add(stay(make_item("hotel", "", 30, place=hotel), "checkout"))
        point = hotel

    if d.kind == "first" and simulate_end(ctx, items) >= NIGHT:
        return items  # приезд ночью (например, поезд в 01:00) — заселение, программа начнётся утром

    # ---- время начала активностей
    t = simulate_end(ctx, items)
    lunch_done = False

    # ---- конец окна активностей
    def to_station(at: dict) -> int:
        """Дорога до вокзала или аэропорта; аэропорт за городом — дольше обычных 40 минут."""
        tr = transfer(ctx, at, depart_pt or hotel)
        return max(40, tr["minutes"] if tr else 0)

    if d.kind in ("last", "single"):
        window_end = ret - ctx.back_boarding - to_station({"lat": ctx.data["center"][0], "lon": ctx.data["center"][1]})
    elif d.theatre:
        window_end = parse_hm(d.theatre["start"]) - 60 - 30
    else:
        window_end = 18 * 60 + 30

    def fits(t_end: int, end_point: dict) -> bool:
        if d.kind in ("last", "single"):  # к поезду — от последнего места, а не от центра
            return t_end + to_station(end_point) <= ret - ctx.back_boarding
        if d.theatre:  # дорога к театру, лёгкий ужин рядом с ним (60) и переход от кафе (≈ 20)
            tr = transfer(ctx, end_point, d.theatre)
            return t_end + (tr["minutes"] if tr else 0) + 60 + 20 <= parse_hm(d.theatre["start"])
        return t_end <= window_end

    places = list(d.places)
    while True:
        cost, steps, t_end, end_point, lunch_done2 = schedule_activities(ctx, d, t, point, places, lunch_done,
                                                                         hotel if late_checkin else None)
        if fits(t_end, end_point) or not places:
            break
        optional = [p for p in places if not p.get("booked")]  # брони по билетам не выкидываем
        if not optional:
            break
        dropped = min(optional, key=lambda p: score(ctx, p))  # не помещается — убираем наименее важное
        places.remove(dropped)
        ctx.used_places.discard(dropped["id"])
    # Осталось много свободного времени — добираем ближайшие места; при спокойном темпе это время отдыха
    while window_end - t_end > 75 and ti.pace in ("max", "balanced"):
        here = end_point if steps else point
        extra = [p for p in ctx.places(*CITY_SIGHTS)
                 if p["id"] not in ctx.used_places and opening(p, d.date) is not None and score(ctx, p) > -5]
        if not extra:
            break
        cand = min(extra, key=lambda p: catalog.haversine_km(here["lat"], here["lon"], p["lat"], p["lon"]) - 0.3 * score(ctx, p))
        trial = places + [cand]
        res = schedule_activities(ctx, d, t, point, trial, lunch_done, hotel if late_checkin else None)
        if not fits(res[2], res[3]) or res[0] - cost >= 400:
            ctx.used_places.add(cand["id"])  # не влезает — больше не пробуем
            ctx.skipped.add(cand["id"])
            continue
        places = trial
        ctx.used_places.add(cand["id"])
        cost, steps, t_end, end_point, lunch_done2 = res
    d.places = places

    for kind, p in steps:
        if kind == "lunch":
            it = add(food_item(ctx, p, "lunch"))
            used_food.add(p["id"])
        elif kind == "checkin":  # утром оставили вещи — заселяемся днём, а не поздно вечером
            # номер может быть ещё не готов: checkin_step заложил ожидание — заселение не раньше времени заезда
            add(stay(make_item("hotel", "Заселение", CHECKIN_MIN, place=hotel, fixed=stay_times(hotel)[0]), "checkin_late"))
            late_checkin = False
        else:
            add(attraction_item(ctx, p))
    point = end_point if steps else point

    # ---- вечер
    if d.kind in ("last", "single"):
        now = simulate_end(ctx, items)
        if ret - ctx.back_boarding - now - 40 >= 100 and now >= 17 * 60:
            f = nearest_food(ctx, point, d.date, now, used_food, "dinner")
            if f:
                dinner = food_item(ctx, f[0], "dinner")
                dinner["duration"] = 75
                add(dinner)
                used_food.add(f[0]["id"])
        if ctx.back_transport == "car":
            add(make_item("travel", f"Обратная дорога в {ti.origin}", ctx.back_minutes, fixed=ret, shift=-ctx.shift,
                          note="Ориентировочно, с остановками" if not ti.return_arrive_time
                          else f"Выезд {ti.return_time}, прибытие {ti.return_arrive_time}",
                          cost_kind="receipt", estimate=int(ctx.back_route["price"] / 2)))
        else:
            if ctx.back_boarding:
                add(make_item("travel", f"{(depart_pt or {}).get('name', 'Вокзал')}: посадка", ctx.back_boarding,
                              fixed=ret - ctx.back_boarding, point=depart_pt, note="Вещи из камеры хранения забрать по пути"))
            link = buy_link(ctx, d.date, False)
            label = TRANSPORT_LABEL[ctx.back_transport] if ti.back_number else ctx.back_route["label"]
            title = f"{label} {ctx.city} → {ti.origin}" + (f", № {ti.back_number}" if ti.back_number else "")
            add(make_item("travel", title, ctx.back_minutes, fixed=ret, shift=-ctx.shift,
                          point=depart_pt, cost_kind="ticket", estimate=int(ctx.back_price * ctx.people_price),
                          links=link + rasp_link(ctx, d.date, False),
                          note=leg_note(ctx.back_transport, ti.return_time, ti.return_arrive_time, ti.back_number, ctx.back_minutes)
                          + tz_note(ctx, False) + price_note(ti.back_price)))
        return items

    rest_needed = ti.pace in ("calm", "relaxed", "balanced") or ctx.kids
    if d.theatre:
        # отдых — только если до спектакля остаётся время: ужин 60 + дороги ≈ 60
        free = parse_hm(d.theatre["start"]) - simulate_end(ctx, items) - 60 - 60
        home = transfer(ctx, point, hotel)  # с выезда за город обратно дольше обычных 30 минут
        free -= max(0, (home["minutes"] if home else 0) - 30)
        if rest_needed and free >= 45:
            back(True, min(90, round5(free) - 5 if free < 95 else 90))
            point = hotel
    elif rest_needed and simulate_end(ctx, items) <= 17 * 60 + 30:  # позже — ужин ушёл бы к закрытию
        back(True, 90)
        point = hotel
    else:
        rest_needed = False

    if d.theatre:
        th = d.theatre
        f = nearest_food(ctx, th, d.date, parse_hm(th["start"]) - 90, used_food, "dinner")
        if f:
            add(food_item(ctx, f[0], "dinner", light=True))
            used_food.add(f[0]["id"])
        add(attraction_item(ctx, th))
        point = th
    else:
        at = max(19 * 60 if rest_needed else 0, simulate_end(ctx, items) + 15)
        f = nearest_food(ctx, point, d.date, at, used_food, "dinner") if at < NIGHT else []
        if f:
            dinner = add(food_item(ctx, f[0], "dinner", light=at >= 20 * 60 + 30))  # поздно — лёгкий ужин, час
            dinner["fixed_start"] = 19 * 60 if rest_needed else None
            used_food.add(f[0]["id"])
            point = f[0]
        daytrip = any(p["category"] == "daytrip" for p in d.places)
        if ti.pace in ("max", "balanced") and not ctx.small_kids and not daytrip:
            ev = evening_walk(ctx, point, d)
            tr = transfer(ctx, point, ev) if ev else None
            if ev and simulate_end(ctx, items) + (tr["minutes"] if tr else 0) >= NIGHT:
                ctx.used_places.discard(ev["id"])  # поздно — гуляем в другой вечер
                ev = None
            if ev:
                it = add(attraction_item(ctx, ev))
                it["title"] = f"Вечерняя прогулка: {ev['name']}"
                point = ev
    back(False, 30)
    return items


def evening_walk(ctx: Ctx, point: dict, d: DayPlan) -> dict | None:
    used = ctx.used_places | {p["id"] for p in d.places}  # и места других дней — чтобы не повторять
    walks = [p for p in ctx.places("walk", "landmark") if p["id"] not in used and not p.get("hours")
             and score(ctx, p) > -5]
    if not walks:
        return None
    ev = min(walks, key=lambda p: catalog.haversine_km(point["lat"], point["lon"], p["lat"], p["lon"]))
    ctx.used_places.add(ev["id"])
    return ev


def simulate_end(ctx: Ctx, items: list[dict]) -> int:
    t, prev = 0, None
    for it in items:
        tr = transfer(ctx, prev, it) if prev else None
        t += tr["minutes"] if tr else 0
        if it.get("fixed_start") is not None:
            t = max(t, it["fixed_start"])
        t += it["duration"] + it.get("shift", 0)
        if it.get("lat") is not None:
            prev = it
    return t


# ---------------------------------------------------------- alternatives

ALT_LIMIT = 30  # с поиском по названию в списке можно показать все варианты каталога


def alternatives_for(ctx: Ctx, item: dict, day: date, exclude: set[str]) -> list[str]:
    pid = item.get("place_id")
    meal = item.get("meal")
    if not pid and meal and item.get("lat") is not None:
        pool = [f for f in ctx.places("food") if opening(f, day) is not None
                and (meal.split("_")[0] in f.get("meal", []) or meal == "snack")]
        pool.sort(key=lambda x: catalog.haversine_km(item["lat"], item["lon"], x["lat"], x["lon"]))
        return [x["id"] for x in pool[:ALT_LIMIT]]
    if not pid:
        return []
    p = ctx.place(pid)
    cat = p["category"]
    if cat == "hotel":
        return []  # жильё меняется только в вводных — см. replace_place
    elif cat == "food":
        m = (meal or "lunch").split("_")[0]
        pool = [f for f in ctx.places("food") if f["id"] != pid and opening(f, day) is not None
                and (m in f.get("meal", []) or m == "snack")]
    elif cat == "theatre":
        pool = [x for x in ctx.places("theatre") if x["id"] != pid]
    else:
        pool = [x for x in ctx.places(*(("daytrip",) if cat == "daytrip" else CITY_SIGHTS))
                if x["id"] not in exclude and opening(x, day) is not None and score(ctx, x) > -5]
    pool.sort(key=lambda x: catalog.haversine_km(p["lat"], p["lon"], x["lat"], x["lon"]))
    return [x["id"] for x in pool[:ALT_LIMIT]]


# ----------------------------------------------------------------- public

def plan_trip(raw: dict, custom_places: dict | None = None, events: list[dict] | None = None) -> dict:
    """custom_places — места не из каталога (квартира по ссылке из формы): программа сразу строится от них,
    а не подставляется потом поверх отеля — иначе время заезда и дорога до жилья были бы чужие.

    events — брони с датой и временем (театр, музей, экскурсия по билету, bookings.event_place): встают в свой
    день жёстко — вечерние как театр (с ужином до него), дневные как места с фиксированным началом."""
    ti = TripInput.from_dict(raw)
    ctx = Ctx(ti)
    ctx.extra = dict(custom_places or {})
    for e in events or []:
        ctx.extra[e["id"]] = e
    for p in ctx.extra.values():
        if p.get("city") and p["city"] != ctx.city:
            raise ValueError(f"Это объявление в городе {p['city']}, а поездка — в {ctx.city}")
    hotel = chosen_stay(ctx)

    days: list[DayPlan] = []
    for i in range(ti.days):
        if ctx.night_out and i == 0:
            kind = "night"  # вечером в поезд, ночь в дороге
        elif ti.days == 1 or (ctx.night_out and i == 1 and ti.days == 2):
            kind = "single"
        elif i == int(ctx.night_out):
            kind = "first"
        elif i == ti.days - 1:
            kind = "last"
        else:
            kind = "middle"
        days.append(DayPlan(i, ctx.start_date + timedelta(days=i), kind, day_capacity(ctx, kind)))

    skipped_events = []
    for e in events or []:
        d = next((d for d in days if d.date.isoformat() == e["date"]), None)
        if d is None:
            skipped_events.append(e["name"])  # бронь не на даты поездки
        elif parse_hm(e["start"]) >= 17 * 60 and d.theatre is None:
            d.theatre = e
        else:
            d.fixed.append(e)
    th = None if any(d.theatre for d in days) else choose_theatre(ctx)  # свой билет в театр — вместо подбора
    if th:
        # в день приезда — только если до спектакля успеваем заселиться и поужинать
        arrive = ctx.arrive_at
        evening_days = [d for d in days if d.kind == "middle"
                        or (d.kind == "first" and arrive + 180 <= parse_hm(th["start"]))]
        target = next((d for d in evening_days if d.kind == "middle"), evening_days[0] if evening_days else None)
        if target:
            target.theatre = th

    assign_days(ctx, days)
    ctx.used_places = {p["id"] for d in days for p in d.places}
    # место из каталога, куда уже есть билет («Эрмитаж» по билету), второй раз не предлагаем
    ctx.used_places |= {e["same_as"] for e in events or [] if e.get("same_as")}

    used_food: set[str] = set()
    out_days = []
    built = [(d, build_day(ctx, d, hotel, used_food)) for d in days]
    all_place_ids = {p["id"] for d in days for p in d.places}
    for d, items in built:
        exclude = all_place_ids | {it["place_id"] for it in items if it["place_id"]}
        for it in items:
            booked = it["place_id"] in ctx.extra and ctx.extra[it["place_id"]].get("booked")
            it["alternatives"] = [] if booked else alternatives_for(ctx, it, d.date, exclude)
        out_days.append({
            "index": d.index,
            "date": d.date.isoformat(),
            "label": f"День {d.index + 1}",
            "sub": f"{WEEKDAYS[d.date.weekday()]}, {d.date.day} {MONTHS_GEN[d.date.month - 1][:3]}",
            "items": items,
        })

    trip = {
        "input": ti.__dict__,
        "summary": {
            "title": f"{ctx.city} · {ti.days} {plural(ti.days, 'день', 'дня', 'дней')}",
            "city": ctx.city,
            "dates": date_range(ctx.start_date, ti.days),
            "people": people_label(ti.adults, len(ti.kids_ages)),
            "transport": transport_summary(ctx),
            "pace": PACE_LABEL[ti.pace],
            "wishes": sorted(ctx.wishes),
            "wishes_note": wishes_note(ctx),
            "hotel_tier": ctx.tier,
            "skipped_bookings": skipped_events,  # брони не на даты поездки — в программу не встали
        },
        "days": out_days,
    }
    if ctx.extra:
        trip["custom_places"] = ctx.extra
    recompute(trip)
    return trip


def transport_summary(ctx: Ctx) -> str:
    """«Поезд «Сапсан»» или, если туда и обратно по-разному, «туда — поезд № 752А, обратно — самолёт»."""
    ti = ctx.ti

    def one(transport: str, route: dict, known: bool, number: str) -> str:
        if number or not known:
            return TRANSPORT_LABEL[transport] + (f" № {number}" if number else "")
        return route["label"]

    out = one(ctx.transport, ctx.route, ctx.route_known, ti.out_number)
    if ctx.back_transport == ctx.transport:
        return out
    back = one(ctx.back_transport, ctx.back_route, ctx.back_known, ti.back_number)
    return f"туда — {out[0].lower() + out[1:]}, обратно — {back[0].lower() + back[1:]}"


def wishes_note(ctx: Ctx) -> str | None:
    """«Как мы поняли пожелания» — только когда их разбирала модель."""
    if not ctx.ai or not ctx.ai.get("summary"):
        return None
    note = ctx.ai["summary"].strip()
    if note[-1] not in ".!?…":
        note += "."
    if ctx.ai.get("pace_applied"):
        note += f" Темп «{PACE_LABEL[ctx.ti.pace]}» выбран по вашим словам — его можно поменять в вводных."
    return note


def recompute(trip: dict) -> dict:
    """Пересчёт времени, переходов, предупреждений и бюджета после любой правки."""
    ctx = trip_ctx(trip)
    for d in trip["days"]:
        day = date.fromisoformat(d["date"])
        t, prev = 0, None
        for it in d["items"]:
            tr = transfer(ctx, prev, it) if prev else None
            it["transfer"] = tr if tr and tr["minutes"] > 0 else None
            t += tr["minutes"] if tr else 0
            it["warnings"] = []
            it["wait"] = 0
            fixed = it.get("fixed_start")
            if fixed is not None:
                if t > fixed and (it["kind"] in ("travel", "theatre") or it.get("doc")):
                    it["warnings"].append(f"Не успеваете к {fmt_hm(fixed)}: опоздание {t - fixed} мин")
                if t < fixed:
                    it["wait"] = fixed - t if prev is not None else 0
                    t = fixed
            it["start"] = t
            it["end"] = t + it["duration"] + it.get("shift", 0)
            if it.get("place_id"):
                p = ctx.place(it["place_id"])
                shift, start = divmod(it["start"], 24 * 60)  # после полуночи — часы следующего дня
                o = opening(p, day + timedelta(days=shift))
                if o is None and p["category"] not in ("hotel",):
                    it["warnings"].append("В этот день закрыто")
                elif o and p.get("hours"):  # без часов работы (отель, улица) — открыто всегда
                    if start < o[0]:
                        it["warnings"].append(f"Открывается в {fmt_hm(o[0])}")
                    if start + it["duration"] > o[1]:
                        it["warnings"].append(f"Закрывается в {fmt_hm(o[1])}")
            role = it.get("stay_role")
            if role in ("checkin", "checkin_late", "checkout") and it.get("place_id"):
                cin, cout, _ = stay_times(ctx.place(it["place_id"]))
                if role == "checkout" and it["end"] > cout:
                    it["warnings"].append(f"Выезд до {fmt_hm(cout)}: задержка {it['end'] - cout} мин")
                elif role != "checkout" and not it.get("checkin_early") and it["start"] < cin:
                    it["warnings"].append(f"Заселение только с {fmt_hm(cin)}")
            it["warnings"] += doc_warnings(ctx, it, day)
            t = it["end"]
            if it.get("lat") is not None:
                prev = it
    trip["budget"] = budget(trip)
    return trip


def budget(trip: dict) -> dict:
    cats = {"road": {"name": "Дорога", "sum": 0, "ok": 0, "n": 0},
            "hotel": {"name": "Проживание", "sum": 0, "ok": 0, "n": 0},
            "food": {"name": "Еда", "sum": 0, "ok": 0, "n": 0},
            "culture": {"name": "Музеи, театр, экскурсии", "sum": 0, "ok": 0, "n": 0}}
    paid = onsite = est = 0
    for d in trip["days"]:
        for it in d["items"]:
            c = it["cost"]
            if c["kind"] == "none" and not it.get("doc"):
                continue
            key = {"travel": "road", "hotel": "hotel", "food": "food"}.get(it["kind"], "culture")
            doc = it.get("doc")
            cats[key]["n"] += 1
            if doc:
                paid += doc["paid"]
                onsite += doc["onsite"]
                cats[key]["sum"] += doc["paid"] + doc["onsite"]
                cats[key]["ok"] += 1
            else:
                est += c["estimate"]
                cats[key]["sum"] += c["estimate"]
    total = paid + onsite + est
    limit = int(trip["input"]["budget"])
    tol = int(trip["input"].get("budget_tolerance_pct", 10) or 0)
    hard = int(limit * (1 + tol / 100))
    status = "ok" if total <= limit else ("tolerance" if total <= hard else "over")
    return {
        "limit": limit, "total": total, "paid": paid, "onsite": onsite, "estimate": est,
        "left": limit - total,
        "tolerance_pct": tol, "hard_limit": hard, "status": status,
        "exact_share": round(100 * (paid + onsite) / total) if total else 0,
        "categories": [dict(v, key=k) for k, v in cats.items() if v["n"]],
        "over": status == "over",
    }


# ------------------------------------------------------------ user edits

def find_item(trip: dict, item_id: str) -> tuple[dict, dict]:
    for d in trip["days"]:
        for it in d["items"]:
            if it["id"] == item_id:
                return d, it
    raise KeyError(item_id)


def set_duration(trip: dict, item_id: str, minutes: int) -> dict:
    d, it = find_item(trip, item_id)
    if it.get("doc") and (it["kind"] in ("travel", "theatre") or (it["doc"].get("start") and it["doc"].get("end"))):
        raise ValueError("Время этого пункта задано документом")
    new = max(int(trip["input"].get("step_min") or 5), min(600, int(minutes)))
    # Пункт, который заканчивается ровно к следующему фиксированному (выход из дома → посадка → поезд),
    # держим привязанным к концу: меняется его начало, а не время прибытия. Цепочку перед ним сдвигаем так же.
    items = d["items"]
    i = items.index(it)
    nxt = items[i + 1] if i + 1 < len(items) else None
    if (it.get("fixed_start") is not None and nxt and nxt.get("fixed_start") is not None
            and it["fixed_start"] + it["duration"] == nxt["fixed_start"]):
        shift = it["duration"] - new
        while i >= 0:
            cur = items[i]
            anchor = cur["fixed_start"]
            cur["fixed_start"] += shift
            prev = items[i - 1] if i > 0 else None
            if not (prev and prev.get("fixed_start") is not None and prev["fixed_start"] + prev["duration"] == anchor):
                break
            i -= 1
    if it["kind"] == "travel" and it["title"] == "Выход из дома":
        trip["input"]["to_station_min"] = new
        it["note"] = re.sub(r"≈ (?:\d+ ч ?)?(?:\d+ мин)?", f"≈ {fmt_dur(new)}", it["note"])
    it["duration"] = new
    it["user_changed"] = True
    return recompute(trip)


def replace_place(trip: dict, item_id: str, place_id: str) -> dict:
    ctx = trip_ctx(trip)
    d, it = find_item(trip, item_id)
    p = ctx.find(place_id)
    if it["kind"] == "hotel" or (p and p["category"] == "hotel"):
        # жильё задаёт весь маршрут (заезд, выезд, дорогу каждый день) — меняем в вводных и строим заново
        raise ValueError("Жильё меняется в вводных поездки: «Изменить вводные» → «Где жить», маршрут перестроится")
    if place_id not in it["alternatives"]:
        raise ValueError("Этого варианта нет среди замен")
    old = it["place_id"]
    if p["category"] == "food":
        meal = it.get("meal") or ("lunch" if it["title"].startswith("Обед") else "dinner")
        new = food_item(ctx, p, meal.split("_")[0], light=meal == "dinner_light")
    else:
        new = attraction_item(ctx, p)
    keep_fixed = it.get("fixed_start") if it["kind"] == "rest" or it.get("meal") == "breakfast" else None
    for key in ("title", "note", "place_id", "lat", "lon", "duration", "default_duration", "cost", "links", "fixed_start", "kind", "meal", "far"):
        it[key] = new[key]
    if keep_fixed is not None:
        it["fixed_start"] = keep_fixed
    it["docs"] = []
    it["doc"] = None
    it["user_changed"] = True
    rest = [a for a in it["alternatives"] if a not in (place_id, old)]
    it["alternatives"] = ([old] if old else []) + rest[:ALT_LIMIT - 1]
    return recompute(trip)


def replace_with_custom(trip: dict, item_id: str, p: dict) -> dict:
    """Поставить место не из каталога вместо пункта программы: экскурсию Трипстера или кафе с Яндекс Карт.
    Хранится в поездке (custom_places)."""
    d, it = find_item(trip, item_id)
    ctx = trip_ctx(trip)
    if p.get("lat") is not None and catalog.haversine_km(p["lat"], p["lon"], *ctx.data["center"]) > 60:
        raise ValueError(f"Это место не в городе {ctx.city} — выберите заведение рядом с маршрутом")
    if p["category"] == "food":
        if it["kind"] != "food":
            raise ValueError("Кафе или ресторан можно поставить только вместо завтрака, обеда или ужина")
    elif it["kind"] not in (*TRIPSTER_KINDS, "excursion"):
        raise ValueError("Экскурсию можно поставить только вместо музея, прогулки или достопримечательности")
    trip.setdefault("custom_places", {})[p["id"]] = p
    it["alternatives"] = [p["id"], *[a for a in it["alternatives"] if a != p["id"]]]
    return replace_place(trip, item_id, p["id"])


def listing_link(ctx: Ctx, p: dict) -> str:
    """Объявление сразу на даты поездки и с числом гостей."""
    a, b = stay_dates(ctx)
    return f"{p['url']}?occupied={a.isoformat()};{b.isoformat()}&guests_adults={ctx.ti.adults}"


def _num(v) -> int | None:
    if v in (None, ""):
        return None
    return int(str(v).replace(" ", "").replace("\u00a0", ""))


def attach_doc(trip: dict, item_id: str, doc: dict, new_files: list[dict] | None = None) -> dict:
    """Приложить или обновить документ пункта. Новые файлы добавляются к уже приложенным."""
    _, it = find_item(trip, item_id)
    old = it.get("doc") or {}
    total = _num(doc.get("sum"))
    if total is None:
        total = old.get("sum")
    if not total or total <= 0:
        raise ValueError("Укажите сумму из документа")
    paid = _num(doc.get("paid"))
    if paid is None:
        paid = total
    paid = max(0, min(paid, total))
    date_s = (doc.get("date") or "").strip() or None
    if date_s:
        date.fromisoformat(date_s)
    start = (doc.get("start") or "").strip() or None
    until = (doc.get("end") or "").strip() or None
    if start:
        parse_hm(start)
    if until:
        parse_hm(until)
    files = list(old.get("files") or [])
    files += new_files or []
    it["doc"] = {
        "id": old.get("id") or new_id(),
        "files": files,
        "title": (doc.get("title") or "").strip() or old.get("title") or it["title"],
        "sum": total,
        "paid": paid,
        "onsite": total - paid,
        "date": date_s,
        "start": start,
        "end": until,
    }
    if start and it["kind"] != "hotel":
        it["fixed_start"] = parse_hm(start)
    if start and until and it["kind"] != "hotel":
        it["duration"] = max(10, parse_hm(until) - parse_hm(start))
    return recompute(trip)


def attach_booking(trip: dict, item_id: str, b: dict, files: list[dict]) -> None:
    """Файл билета или брони из формы — документом пункта: сумма из брони, оплачено целиком."""
    _, it = find_item(trip, item_id)
    total = int(b.get("price") or 0) or int(it["cost"].get("estimate") or 0)
    it["doc"] = {"id": new_id(), "files": files, "title": b.get("title") or it["title"], "sum": total, "paid": total,
                 "onsite": 0, "date": b.get("date") or None,
                 "start": b.get("start") or None if it["kind"] not in ("hotel", "travel") else None,
                 "end": b.get("end") or None if it["kind"] not in ("hotel", "travel") else None}


def remove_doc_file(trip: dict, item_id: str, name: str) -> dict:
    _, it = find_item(trip, item_id)
    doc = it.get("doc")
    if not doc:
        raise KeyError(name)
    before = len(doc["files"])
    doc["files"] = [f for f in doc["files"] if f["name"] != name]
    if len(doc["files"]) == before:
        raise KeyError(name)
    return recompute(trip)


def detach_doc(trip: dict, item_id: str) -> tuple[dict, dict | None]:
    _, it = find_item(trip, item_id)
    old = it.get("doc")
    it["doc"] = None
    if old and old.get("start") and it["kind"] not in ("travel", "theatre"):
        it["fixed_start"] = None
    return recompute(trip), old


def doc_warnings(ctx: Ctx, it: dict, day: date) -> list[str]:
    """Сверка документа с программой: дата, выходные, часы работы, время по билету."""
    doc = it.get("doc")
    if not doc:
        return []
    out = []
    if doc.get("date"):
        dd = date.fromisoformat(doc["date"])
        if dd != day:
            out.append(f"Дата в документе {dd.day} {MONTHS_GEN[dd.month - 1]} не совпадает с днём программы "
                       f"{day.day} {MONTHS_GEN[day.month - 1]}")
    p = ctx.find(it.get("place_id") or "")
    if p and p["category"] not in ("hotel", "food"):
        o = opening(p, day)
        if o is None:
            out.append(f"По расписанию в {WEEKDAYS[day.weekday()]} закрыто — проверьте дату билета")
        elif doc.get("start"):
            s = parse_hm(doc["start"])
            if not o[0] <= s < o[1]:
                out.append(f"Время по билету {doc['start']} вне часов работы {fmt_hm(o[0])}–{fmt_hm(o[1])}")
    if doc.get("end") and it["end"] > parse_hm(doc["end"]):
        out.append(f"Программа выходит за время по билету: билет до {doc['end']}, пункт до {fmt_hm(it['end'])}")
    if doc.get("start") and it["start"] > parse_hm(doc["start"]) + 5:
        out.append(f"По программе приходите в {fmt_hm(it['start'])}, а в билете {doc['start']}")
    return out


# ------------------------------------------------------- add / remove items

def insert_pos(d: dict, after_id: str | None) -> int:
    if not after_id:
        return len(d["items"])
    ids = [i["id"] for i in d["items"]]
    if after_id not in ids:
        raise ValueError("Пункт для вставки не найден")
    return ids.index(after_id) + 1


def in_origin(trip: dict, day_index: int, after_id: str | None) -> bool:
    """Место вставки — ещё дома (до дороги туда) или уже дома (после дороги обратно), а не в городе поездки."""
    items = trip["days"][day_index]["items"]
    pos = insert_pos(trip["days"][day_index], after_id)
    legs = [i for i, it in enumerate(items) if it["kind"] == "travel" and it["cost"]["kind"] in ("ticket", "receipt")]
    if not legs:
        return False
    if day_index == 0 and (pos <= legs[0] or items[legs[0]]["end"] >= 24 * 60):  # до поезда или в ночном поезде
        return True
    last = len(trip["days"]) - 1
    return day_index == last and (day_index > 0 or len(legs) > 1) and pos > legs[-1]


def suggestions(trip: dict, day_index: int, limit: int = ALT_LIMIT, after_id: str | None = None) -> list[dict]:
    """Что можно добавить в день: места, которых ещё нет в поездке, ближе к центру дня."""
    if in_origin(trip, day_index, after_id):
        return []
    ctx = trip_ctx(trip)
    d = trip["days"][day_index]
    day = date.fromisoformat(d["date"])
    used = {it["place_id"] for dd in trip["days"] for it in dd["items"] if it.get("place_id")}
    pts = [it for it in d["items"] if it.get("lat") is not None]
    cx = sum(i["lat"] for i in pts) / len(pts) if pts else ctx.data["center"][0]
    cy = sum(i["lon"] for i in pts) / len(pts) if pts else ctx.data["center"][1]
    pool = [p for p in ctx.places(*SIGHTS, "theatre", "food")
            if p["id"] not in used and opening(p, day) is not None]
    pool.sort(key=lambda p: catalog.haversine_km(cx, cy, p["lat"], p["lon"]))
    return [p["id"] for p in pool[:limit]]


def previous_point(d: dict, item_id: str | None) -> dict | None:
    """Место перед вставкой «после item_id»: последний пункт с координатами до него включительно.
    Без item_id — последний такой пункт дня."""
    prev = None
    for it in d["items"]:
        if it.get("lat") is not None:
            prev = it
        if it["id"] == item_id:
            return prev
    return prev


def distance_from(ctx: Ctx, prev: dict | None, p: dict) -> dict | None:
    """Примерное расстояние и время от предыдущего места: «≈ 1,2 км от предыдущего места, пешком 15 мин»."""
    tr = transfer(ctx, prev, p)
    if not tr:
        return None
    if tr["km"] < 0.1:
        return {"km": 0.0, "label": "рядом с предыдущим местом"}
    km = f"{tr['km']:.1f}".replace(".", ",")
    return {"km": tr["km"], "label": f"≈ {km} км от предыдущего места, {tr['mode']} {fmt_dur(tr['minutes'])}"}


def by_rating_and_distance(rows: list[dict]) -> list[dict]:
    """Сначала выше рейтинг (когда он есть в данных), потом ближе к предыдущему месту."""
    return sorted(rows, key=lambda r: (-(r.get("rating") or 0), r["km"] if r.get("km") is not None else 1e9))


def add_item(trip: dict, day_index: int, after_id: str | None, place_id: str | None = None,
             title: str | None = None, duration: int | None = None) -> dict:
    ctx = trip_ctx(trip)
    if not 0 <= day_index < len(trip["days"]):
        raise ValueError("Нет такого дня")
    d = trip["days"][day_index]
    day = date.fromisoformat(d["date"])
    if place_id:
        if in_origin(trip, day_index, after_id):
            raise ValueError(f"Здесь вы ещё не в городе {ctx.city} — добавьте свой пункт")
        p = ctx.find(place_id)
        if not p:
            raise ValueError("Место не найдено")
        if p["category"] == "food":
            it = food_item(ctx, p, "snack")
        else:
            it = attraction_item(ctx, p)
            it["fixed_start"] = None if p["category"] != "theatre" else it["fixed_start"]
    else:
        name = (title or "").strip()
        if not name:
            raise ValueError("Укажите, что добавить")
        it = make_item("custom", name, max(ctx.ti.step_min, min(600, int(duration or 60))), note="Добавлено вами")
    it["user_changed"] = True
    used = {i["place_id"] for dd in trip["days"] for i in dd["items"] if i.get("place_id")}
    it["alternatives"] = alternatives_for(ctx, it, day, used)
    idx = insert_pos(d, after_id)
    d["items"].insert(idx, it)
    return recompute(trip)


def delete_item(trip: dict, item_id: str) -> tuple[dict, dict]:
    d, it = find_item(trip, item_id)
    if it["kind"] == "travel" and it["cost"]["kind"] == "ticket":
        raise ValueError("Дорогу туда и обратно удалить нельзя — можно поменять даты поездки")
    d["items"].remove(it)
    return recompute(trip), it


# ------------------------------------------------------------- labels

def rub(n: float) -> str:
    return f"{int(round(n)):,} ₽".replace(",", "\u202f")


def people_for(adults: int, kids: int) -> str:
    """«на 2 взрослых и 1 ребёнка»"""
    out = f"на {adults} {plural(adults, 'взрослого', 'взрослых', 'взрослых')}"
    if kids:
        out += f" и {kids} {plural(kids, 'ребёнка', 'детей', 'детей')}"
    return out


def cost_basis(ctx: Ctx, it: dict, p: dict | None) -> str:
    """Из чего сложилась оценка пункта: цена за человека, номер, ночь, машину — и на сколько."""
    c, ti, kids = it["cost"], ctx.ti, ctx.kids
    if not c.get("estimate"):
        return ""
    if c["kind"] == "booking" and p and p.get("price_night"):
        nights, units = ctx.nights, stay_units(ctx, p)
        unit = ("квартиру", "квартиры", "квартир") if stay_kind(p) == "apartment" else ("номер", "номера", "номеров")
        out = f"{rub(p['price_night'])} за {unit[0]} в ночь × {nights} {plural(nights, 'ночь', 'ночи', 'ночей')}"
        return out + (f" × {units} {plural(units, *unit)}" if units > 1 else "")
    if it["kind"] == "food" and p and p.get("avg_check"):
        # средний чек на человека уже в описании пункта — здесь только «за двоих»
        return group_for(ti.adults + kids)
    if it["kind"] == "travel" and c["kind"] == "ticket":
        per = c["estimate"] / ctx.people_price
        return (f"{rub(per)} за взрослого × {ti.adults}"
                + (f" + детский ≈ {rub(per / 2)} × {kids}" if kids else ""))
    if it["kind"] == "travel" and c["kind"] == "receipt":
        return "за машину в одну сторону: бензин и платные дороги"
    if p and p.get("source") == "booking":
        return "по билету, на всех"
    if p and p.get("price_group"):
        return "за экскурсию на всю группу"
    if p and p.get("source") == "tripster":
        return f"{rub(p['price_adult'])} за человека × {ti.adults + kids}"
    if p and p.get("price_adult"):
        out = f"{rub(p['price_adult'])} за взрослого × {ti.adults}"
        if kids:
            out += (f" + {rub(p['price_child'])} за ребёнка × {kids}" if p.get("price_child") else ", детям бесплатно")
        return out
    return "на всех"


GROUP = {1: "за одного", 2: "за двоих", 3: "за троих", 4: "за четверых", 5: "за пятерых", 6: "за шестерых"}


def group_for(n: int) -> str:
    """«за двоих», «за троих», «на 8 человек»."""
    return GROUP.get(n) or f"на {n} {plural(n, 'человека', 'человек', 'человек')}"


def plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
        return few
    return many


def people_label(adults: int, kids: int) -> str:
    s = f"{adults} {plural(adults, 'взрослый', 'взрослых', 'взрослых')}"
    if kids:
        s += f", {kids} {plural(kids, 'ребёнок', 'ребёнка', 'детей')}"
    return s


def date_range(start: date, days: int) -> str:
    end = start + timedelta(days=days - 1)
    if days == 1:
        return f"{start.day} {MONTHS_GEN[start.month - 1]}"
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GEN[end.month - 1]}"
    return f"{start.day} {MONTHS_GEN[start.month - 1]} — {end.day} {MONTHS_GEN[end.month - 1]}"
