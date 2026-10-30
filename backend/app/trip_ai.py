"""Правка готовой программы словами: «во второй день поменьше ходить», «добавь театр», «обед поближе к Кремлю».

Модель только переводит текст в действия над пунктами — те же, что у кнопок в программе: убрать пункт,
заменить место, добавить место, поменять длительность. Расписание, переходы, проверки и бюджет после них
считает planner, как при ручной правке.

Два шага: propose() — список изменений для подтверждения (поездка не меняется), apply() — применить
выбранные. Действия перед применением проверяются заново по текущей поездке: между шагами её могли поменять.
"""
from __future__ import annotations

from datetime import date

from . import planner, wishes_ai

OPS = ["remove", "replace", "add", "duration"]
FIXED_KINDS = ("travel", "hotel", "rest")  # дорогу, жильё и отдых словами не трогаем
WHY_ASKED = "по вашей просьбе"
SIGHT_GROUP = (*planner.SIGHTS, "theatre", "excursion", "event")

SYSTEM = """Ты помогаешь править готовую программу поездки. Тебе дана программа по дням (у каждого пункта id \
в квадратных скобках), каталог мест города (id: название, категория) и просьба пользователя. Переведи просьбу \
в список действий и ответь только JSON по схеме.

Действия (поле op):
- remove — убрать пункт: item = id пункта.
- replace — поставить другое место вместо пункта: item = id пункта, place = id места из каталога. \
Кафе меняется только на кафе (категория food), достопримечательность — только на достопримечательность или театр.
- add — добавить место из каталога: day = номер дня (с 1), place = id места; item = id пункта, после которого \
вставить, или пустая строка — тогда место встанет после последнего места дня.
- duration — поменять длительность пункта: item = id, minutes = новая длительность в минутах.

Неиспользуемые поля: item и place — пустая строка, day и minutes — 0.

Правила:
- Делай только то, о чём просят. Ничего не добавляй и не убирай сверх просьбы.
- Пункты с пометкой (не менять) — дорога, жильё, отдых — не трогай.
- Не добавляй место, которое уже есть в программе.
- В каталоге только места, которых ещё нет в программе.
- «Поменьше ходить», «полегче» в какой-то день — убери из этого дня одно-два места с наименьшей важностью (важность 1–5 указана у пунктов), в первую очередь прогулки. Места с важностью 5 не убирай, если о них не просят.
- «Добавь театр» — add одного подходящего театра (несколько — только если просят) в день, где вечером нет театра; item — пустая строка: время спектакля и ужин перед ним программа подберёт сама.
- summary — одно-два коротких предложения по-русски: что ты предлагаешь поменять.
- unclear — если часть просьбы нельзя выполнить этими действиями (поменять даты, жильё, темп, транспорт) \
или непонятно, о чём речь, — коротко объясни что; иначе пустая строка."""


def _schema(item_ids: list[str], place_ids: list[str]) -> dict:
    action = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": OPS},
            "item": {"type": "string", "enum": ["", *item_ids]},
            "place": {"type": "string", "enum": ["", *place_ids]},
            "day": {"type": "integer"},
            "minutes": {"type": "integer"},
        },
        "required": ["op", "item", "place", "day", "minutes"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"actions": {"type": "array", "items": action}, "summary": {"type": "string"},
                       "unclear": {"type": "string"}},
        "required": ["actions", "summary", "unclear"],
        "additionalProperties": False,
    }


def _catalog(ctx: planner.Ctx) -> list[dict]:
    """Места, которые можно поставить в программу: всё, кроме жилья; свои места поездки — тоже."""
    places = [p for p in ctx.data["places"] if p["category"] != "hotel"]
    return places + [p for p in ctx.extra.values() if p.get("category") != "hotel"]


def _program(trip: dict, ctx: planner.Ctx) -> str:
    lines = []
    for n, d in enumerate(trip["days"], 1):
        day = date.fromisoformat(d["date"])
        lines.append(f"День {n} ({planner.WEEKDAYS[day.weekday()]}, {day.day} {planner.MONTHS_GEN[day.month - 1]}):")
        for it in d["items"]:
            fixed = " (не менять)" if it["kind"] in FIXED_KINDS else ""
            p = ctx.find(it.get("place_id") or "")
            place = f", место {p['id']}, важность {p.get('priority', 3)}" if p and p["category"] != "food" else (
                f", место {p['id']}" if p else "")
            lines.append(f"  [{it['id']}] {planner.fmt_hm(it['start'])}–{planner.fmt_hm(it['end'])} {it['title']}"
                         f" ({it['kind']}{place}){fixed}")
    return "\n".join(lines)


def _day_of(trip: dict, item_id: str) -> int:
    return next(n for n, d in enumerate(trip["days"]) if any(it["id"] == item_id for it in d["items"]))


def _default_after(d: dict, p: dict) -> str | None:
    """Куда добавить место без указания: после последнего места или еды дня, до вечернего возвращения и дороги;
    спектакль с началом по расписанию — после последнего пункта, который заканчивается до начала."""
    start = planner.parse_hm(p["start"]) if p.get("start") else None
    last = None
    for it in d["items"]:
        if it["kind"] not in FIXED_KINDS and (start is None or it["end"] <= start):
            last = it["id"]
    return last


def _make_room(trip: dict, d: dict, p: dict) -> tuple[str | None, list[str]]:
    """Место для спектакля в этот день: ужин — лёгкий и к началу, вечерняя прогулка — убрать.
    Возвращает пункт, после которого встаёт спектакль, и подписи сделанного."""
    ctx = planner.trip_ctx(trip)
    start = planner.parse_hm(p["start"])
    done = []
    for it in [it for it in d["items"] if it["title"].startswith("Вечерняя прогулка")]:
        d["items"].remove(it)
        done.append(f"убрать {_name(None, it)}: вечер занят спектаклем")
    dinner = next((it for it in d["items"] if it.get("meal") in ("dinner", "dinner_light")), None)
    if not dinner or not dinner.get("place_id"):
        return _default_after(d, p), done
    light = planner.food_item(ctx, ctx.place(dinner["place_id"]), "dinner", light=True)
    for key in ("title", "note", "duration", "default_duration", "cost", "meal"):
        dinner[key] = light[key]
    tr = planner.transfer(ctx, dinner, p)
    dinner["fixed_start"] = start - dinner["duration"] - (tr["minutes"] if tr else 20)  # к спектаклю, как в планировщике
    done.append(f"{_name(None, dinner)} — до спектакля")
    return dinner["id"], done


def _drop_until_on_time(trip: dict, d: dict, place_id: str, limit: int = 3) -> list[str]:
    """Не успеваем к спектаклю — убираем последние места перед ним (без приложенных билетов), не больше limit."""
    done = []
    for _ in range(limit):
        show = next(it for it in d["items"] if it.get("place_id") == place_id)
        if not any(w.startswith("Не успеваете") for w in show["warnings"]):
            break
        before = d["items"][:d["items"].index(show)]
        spare = [it for it in before if it["kind"] in ("museum", "sight", "walk", "excursion") and not it.get("doc")]
        if not spare:
            break
        d["items"].remove(spare[-1])
        planner.recompute(trip)
        done.append(f"убрать {_name(None, spare[-1])}: иначе не успеть к спектаклю")
    return done


def _name(p: dict | None, it: dict | None = None) -> str:
    return f"«{(p or {}).get('name') or (it or {}).get('title', '')}»"


def check(trip: dict, raw_actions: list) -> tuple[list[dict], list[str]]:
    """Действия модели (или присланные клиентом) -> допустимые, с подписью для человека; и что пропущено."""
    ctx = planner.trip_ctx(trip)
    places = {p["id"]: p for p in _catalog(ctx)}
    items = {it["id"]: it for d in trip["days"] for it in d["items"]}
    in_trip = {it.get("place_id") for it in items.values()}
    step = ctx.ti.step_min
    ok, skipped, seen = [], [], set()
    shows = {n + 1 for n, d in enumerate(trip["days"]) if any(it["kind"] == "theatre" for it in d["items"])}
    for a in raw_actions if isinstance(raw_actions, list) else []:
        if not isinstance(a, dict) or a.get("op") not in OPS:
            continue
        op, iid, pid = a["op"], str(a.get("item") or ""), str(a.get("place") or "")
        it, p = items.get(iid), places.get(pid)
        try:
            day = int(a.get("day") or 0)
            minutes = int(a.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
        if op != "add" and not it:
            continue  # пункт не из этой программы
        if it and it["kind"] in FIXED_KINDS:
            skipped.append(f"{_name(None, it)}: дорогу, жильё и отдых правят кнопками в программе")
            continue
        if op == "replace" and p and it["kind"] == "food" and p["category"] in SIGHT_GROUP:
            op, day = "add", _day_of(trip, iid) + 1  # «ужин заменить на театр» — спектакль после ужина, ужин остаётся
        if op in ("remove", "replace", "duration") and iid in seen:
            continue  # два действия над одним пунктом — оставляем первое
        if op == "remove":
            label = f"День {_day_of(trip, iid) + 1}: убрать {_name(None, it)}"
            out = {"op": op, "item": iid}
        elif op == "replace":
            if not p or pid == it.get("place_id"):
                continue
            food = it["kind"] == "food"
            if food != (p["category"] == "food") or (not food and p["category"] not in SIGHT_GROUP):
                skipped.append(f"{_name(None, it)} → {_name(p)}: кафе меняется только на кафе, место — на место")
                continue
            if pid in in_trip:
                skipped.append(f"{_name(p)} уже есть в программе")
                continue
            label = f"День {_day_of(trip, iid) + 1}: {_name(None, it)} → {_name(p)}"
            out = {"op": op, "item": iid, "place": pid}
            in_trip.add(pid)
        elif op == "add":
            if not p or not 1 <= day <= len(trip["days"]):
                continue
            if pid in in_trip:
                skipped.append(f"{_name(p)} уже есть в программе")
                continue
            if p["category"] == "theatre" and day in shows:
                skipped.append(f"{_name(p)}: в день {day} уже есть спектакль")
                continue
            if p.get("start"):
                iid, it = "", None  # спектакль по расписанию встаёт по времени начала, а не после названного пункта
                shows.add(day)
            after = iid if it and _day_of(trip, iid) == day - 1 else None
            label = f"День {day}: добавить {_name(p)}" + (f" после {_name(None, it)}" if after else "")
            out = {"op": op, "day": day, "place": pid, "item": after or ""}
            in_trip.add(pid)
        else:  # duration
            minutes = max(step, min(600, round(minutes / step) * step))
            if minutes == it["duration"]:
                continue
            label = (f"День {_day_of(trip, iid) + 1}: {_name(None, it)} — {planner.fmt_dur(minutes)} "
                     f"вместо {planner.fmt_dur(it['duration'])}")
            out = {"op": op, "item": iid, "minutes": minutes}
        if op != "add":
            seen.add(iid)
        ok.append(dict(out, label=label))
    return ok, skipped


def propose(trip: dict, text: str) -> dict:
    """Просьба словами -> {summary, unclear, changes, skipped}; поездка не меняется. ValueError — модель недоступна."""
    text = text.strip()
    if not text:
        raise ValueError("Напишите, что поменять в программе")
    if not wishes_ai.available():
        raise ValueError("Правка словами сейчас недоступна — поменяйте программу кнопками")
    ctx = planner.trip_ctx(trip)
    places = _catalog(ctx)
    used = {it.get("place_id") for d in trip["days"] for it in d["items"]}

    places = [p for p in places if p["id"] not in used]  # повторно то же место не предлагаем

    def line(p: dict) -> str:
        extra = [f"начало {p['start']}"] if p.get("start") else []
        extra += ["местная кухня"] if "local_food" in p.get("tags", []) else []
        return f"{p['id']}: {p['name']} ({', '.join([p['category'], *extra])})"

    listing = "\n".join(line(p) for p in places)
    user = (f"Город: {ctx.city}\n\nПрограмма:\n{_program(trip, ctx)}\n\nКаталог мест (id: название, категория):\n"
            f"{listing}\n\nПросьба пользователя:\n{text[:1000]}")
    item_ids = [it["id"] for d in trip["days"] for it in d["items"]]
    raw = wishes_ai.ask(SYSTEM, user, _schema(item_ids, [p["id"] for p in places]), max_tokens=3000)
    if not isinstance(raw, dict):
        raise ValueError("Не получилось разобрать просьбу — переформулируйте или поменяйте программу кнопками")
    changes, skipped = check(trip, raw.get("actions"))
    summary = raw.get("summary") if isinstance(raw.get("summary"), str) else ""
    unclear = raw.get("unclear") if isinstance(raw.get("unclear"), str) else ""
    return {"summary": summary.strip()[:400], "unclear": unclear.strip()[:400], "changes": changes, "skipped": skipped}


def apply(trip: dict, actions: list) -> tuple[dict, list[str], list[str], list[dict]]:
    """Применить подтверждённые действия по очереди. Возвращает поездку, применённые и пропущенные изменения
    и убранные пункты (их файлы документов удаляет вызывающий)."""
    changes, skipped = check(trip, actions)
    applied, removed = [], []
    for c in changes:
        try:
            if c["op"] == "remove":
                trip, old = planner.delete_item(trip, c["item"])
                removed.append(old)
            elif c["op"] == "replace":
                _, it = planner.find_item(trip, c["item"])
                it["alternatives"] = [c["place"], *[a for a in it["alternatives"] if a != c["place"]]]
                trip = planner.replace_place(trip, c["item"], c["place"])
                it["why"] = [WHY_ASKED]
            elif c["op"] == "add":
                d = trip["days"][c["day"] - 1]
                before = {it["id"] for it in d["items"]}
                p = planner.trip_ctx(trip).find(c["place"])
                after, done = _make_room(trip, d, p) if p.get("start") else (c["item"] or _default_after(d, p), [])
                trip = planner.add_item(trip, c["day"] - 1, after, c["place"])
                if p.get("start"):
                    done += _drop_until_on_time(trip, d, c["place"])
                applied += [f"День {c['day']}: {x}" for x in done]
                for it in d["items"]:
                    if it["id"] not in before:
                        it["why"] = [WHY_ASKED]
            else:
                trip = planner.set_duration(trip, c["item"], c["minutes"])
        except (KeyError, ValueError) as e:
            skipped.append(f"{c['label']}: {e}" if str(e) else c["label"])
            continue
        applied.append(c["label"])
    return trip, applied, skipped, removed
