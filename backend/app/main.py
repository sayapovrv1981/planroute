"""HTTP API прототипа «Своим ходом».

Starlette (основа FastAPI). Запуск: uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import contextlib
import copy
import os
import re
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.middleware import Middleware
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import address, auth, bookings, catalog, planner, schedule, store, sutochno, tripster, wishes_ai, yandexmaps


def _load_env_file() -> None:
    """backend/.env: строки КЛЮЧ=значение (например DADATA_API_KEY). Переменные окружения важнее."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()

FRONT = Path(__file__).resolve().parents[2] / "frontend" / "dist"

WISH_CHIPS = [
    {"key": "theatre", "label": "Театр"},
    {"key": "kids", "label": "Места для детей"},
    {"key": "cheap", "label": "Подешевле"},
    {"key": "no_long_walks", "label": "Без долгой ходьбы"},
    {"key": "boat", "label": "Прогулка на теплоходе"},
    {"key": "vegetarian", "label": "Вегетарианская еда"},
]


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


BUY_LINK = re.compile(r"(Купить|Найти билеты|Забронировать|Подобрать и забронировать|Цены на даты|Афиша и билеты|"
                      r"Сайт и билеты|Все экскурсии|Экскурсии с гидом)")


def present(tid: str, trip: dict) -> dict:
    """Ответ клиенту: поездка + человекочитаемые альтернативы."""
    out = copy.deepcopy(trip)
    out["id"] = tid
    ctx = planner.trip_ctx(trip)
    tripster_city = planner.tripster_link(ctx)
    for d in out["days"]:
        for it in d["items"]:
            place = ctx.find(it.get("place_id") or "")
            it["cost"]["basis"] = planner.cost_basis(ctx, it, place)
            it["wiki"] = place.get("wiki") if place else None
            it["wiki_area"] = bool(place and place.get("wiki_area"))  # фото района, а не самого места
            photos = (place or {}).get("photos") or []
            it["photo"] = sutochno.thumb(photos[0]) if photos else None
            it["photos"] = ([{"src": sutochno.thumb(u), "big": u} for u in photos]
                            if it.get("stay_role") in ("checkin", "checkin_late") else [])
            links = list(it.get("links", []))
            # экскурсии Трипстера к этому месту (из кеша каталога; нет ключей или каталога — пусто)
            it["excursions"] = (tripster.for_place(place, ctx.city, kids=ctx.small_kids)
                                if place and it["kind"] in planner.TRIPSTER_KINDS else [])
            if tripster_city and it["kind"] in planner.TRIPSTER_KINDS:
                links.append({"label": "Все экскурсии с гидом — Трипстер" if it["excursions"]
                              else "Экскурсии с гидом — Трипстер", "url": tripster_city})
            it["links"] = [dict(l, url=u, ad=l.get("ad") or schedule.ad_label(u))
                           for l in links for u in [l["url"] if l.get("direct") else schedule.partner_any(l["url"])]]
            doc = it.get("doc") or {}
            if (doc.get("sum") and not doc.get("onsite")) or (place and place.get("booked")):
                # оплачено или по своему билету — купить больше не предлагаем
                it["links"] = [l for l in it["links"] if not BUY_LINK.match(l["label"])]
                it["excursions"] = []
            href = planner.listing_link(ctx, place) if place and place.get("source") == "sutochno" else None
            it["href"] = schedule.partner_any(href) if href else None
            it["href_ad"] = schedule.ad_label(it["href"]) if href else None
            alts = []
            ids = list(it.get("alternatives", []))
            prev = None
            for x in d["items"]:  # предыдущее место — последний пункт с координатами перед этим
                if x is it:
                    break
                if x.get("lat") is not None:
                    prev = x
            for pid in ids:
                p = ctx.find(pid)
                if not p:
                    continue
                meta = []
                if p["category"] == "food":
                    meta.append(f"средний чек ≈ {p['avg_check']} ₽ на человека")
                elif p["category"] == "hotel":
                    meta = stay_meta(p)
                else:
                    meta.append(f"{p.get('duration', 60)} мин")
                    if p.get("price_adult"):
                        meta.append(f"≈ {p['price_adult']} ₽ за взрослого"
                                    + (f", {p['price_child']} ₽ за ребёнка" if p.get("price_child") else ""))
                if p.get("note") and p["category"] != "hotel":
                    meta.append(p["note"])
                dist = planner.distance_from(ctx, prev, p) or {}
                alts.append({"id": pid, "name": p["name"], "meta": " · ".join(m for m in meta if m), "wiki": p.get("wiki"),
                             "wiki_area": bool(p.get("wiki_area")),
                             "photo": sutochno.thumb(p["photos"][0]) if p.get("photos") else None,
                             "rating": p.get("rating"), "km": dist.get("km"), "dist": dist.get("label")})
            # жильё — в прежнем порядке (по ценам и виду), остальное — по рейтингу и расстоянию
            it["alternatives"] = alts if it["kind"] == "hotel" else planner.by_rating_and_distance(alts)
            if place and it["kind"] in planner.TRIPSTER_KINDS:
                # в «Заменить» — сначала экскурсии с гидом сюда же: цена в бюджет, длительность в расписание
                it["alternatives"] = [{"id": f"tripster-{x['id']}", "name": f"Экскурсия с гидом: {x['title']}",
                                       "meta": x["meta"], "photo": x["photo"], "wiki": None}
                                      for x in tripster.for_place(place, ctx.city, kids=ctx.small_kids, n=6)
                                      ] + it["alternatives"]
            doc = it.get("doc")
            if doc:
                for f in doc.get("files", []):
                    f["url"] = f"/api/trips/{tid}/files/{f['name']}"
    return out


def stay_meta(p: dict) -> list[str]:
    return ["Квартира посуточно" if planner.stay_kind(p) == "apartment" else "Отель",
            f"≈ {p['price_night']:,} ₽ за {'квартиру' if planner.stay_kind(p) == 'apartment' else 'номер'} в ночь".replace(",", " "), p.get("note", ""), planner.stay_times_note(p)]


def describe(ctx: planner.Ctx, pid: str) -> dict:
    p = ctx.place(pid)
    kind = {"museum": "Музей", "landmark": "Достопримечательность", "walk": "Прогулка",
            "daytrip": "Выезд за город", "theatre": "Театр", "food": "Кафе или ресторан"}.get(p["category"], "")
    meta = [kind]
    if p["category"] == "hotel":
        meta = stay_meta(p)
    elif p["category"] == "food":
        meta.append(f"средний чек ≈ {p['avg_check']} ₽ на человека")
    elif p.get("duration"):
        meta.append(f"{p['duration']} мин")
    if p.get("note") and p["category"] != "hotel":
        meta.append(p["note"])
    return {"id": pid, "name": p["name"], "meta": " · ".join(m for m in meta if m), "wiki": p.get("wiki")}


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def meta(request: Request) -> JSONResponse:
    return JSONResponse({
        "cities": catalog.cities(),
        "transports": [{"key": k, "label": v} for k, v in planner.TRANSPORT_LABEL.items()],
        "paces": [{"key": k, "label": v} for k, v in planner.PACE_LABEL.items()],
        "stays": [{"key": k, "label": v} for k, v in planner.STAY_LABEL.items()],
        "boarding_default": planner.BOARDING_DEFAULT,
        # время в пути на машине: город поездки -> город отправления -> минуты, для расчётного времени приезда
        "drive_minutes": {c: {o: r["minutes"] for (o, t), r in routes.items() if t == "car"}
                          for c, routes in planner.ROUTES.items()},
        "wishes": WISH_CHIPS,
        "interests": [{"key": k, "label": v} for k, v in planner.INTERESTS.items()],
    })


async def address_suggest(request: Request) -> JSONResponse:
    q = request.query_params.get("q", "")
    try:
        data = await run_in_threadpool(address.suggest, q, request.query_params.get("origin", ""),
                                       request.query_params.get("transport", "auto"),
                                       request.query_params.get("city", ""))
    except Exception as e:
        print(f"[address] {address.provider()} «{q}»: {type(e).__name__}: {e}", flush=True)
        return JSONResponse({"provider": address.provider(), "suggestions": [],
                             "error": "Сервис подсказок адресов сейчас недоступен — введите адрес вручную"})
    return JSONResponse(data)


async def schedule_search(request: Request) -> JSONResponse:
    q = request.query_params
    try:
        data = await run_in_threadpool(schedule.search, q.get("from", ""), q.get("to", ""), q.get("date", ""),
                                       q.get("transport", ""))
    except Exception as e:  # сеть, таймаут, ответ Яндекса с ошибкой
        reason = f"{type(e).__name__}: {e}"
        print(f"[schedule] {q.get('transport')} {q.get('from')} -> {q.get('to')} {q.get('date')}: {reason}", flush=True)
        return JSONResponse({"available": False, "error": True, "items": [],
                             "message": f"Не удалось загрузить расписание Яндекса ({reason[:120]})"})
    return JSONResponse(data)


async def stays(request: Request) -> JSONResponse:
    """Варианты жилья для формы: город, даты и группа — из параметров запроса."""
    q = request.query_params
    try:
        kids = [int(a) for a in q.get("kids", "").split(",") if a.strip()]
        # те же вводные, от которых зависит подбор в программе: бюджет за вычетом билетов, «подешевле» в пожеланиях
        ti = planner.TripInput.from_dict({"city": q.get("city", ""), "start_date": q.get("start_date", ""),
                                          "end_date": q.get("end_date") or None, "adults": int(q.get("adults") or 2),
                                          "kids_ages": kids, "stay": q.get("stay") or "any",
                                          "budget": int(q.get("budget") or 100000), "origin": q.get("origin") or "Москва",
                                          "wishes": q.get("wishes", ""), "tags": [t for t in q.get("tags", "").split(",") if t],
                                          "transport": q.get("transport") or "auto",
                                          "return_transport": q.get("return_transport") or "",
                                          "out_price": q.get("out_price") or 0, "back_price": q.get("back_price") or 0})
    except (ValueError, TypeError) as e:
        return err(422, str(e))
    ctx = planner.Ctx(ti)
    opts = planner.stay_options(ctx)
    nights = opts["nights"]
    for r in opts["stays"]:  # как варианты «⇄ Заменить» в маршруте: фото, название, строка описания
        p = ctx.place(r["id"])
        unit = ("квартира", "квартиры", "квартир") if r["kind"] == "apartment" else ("номер", "номера", "номеров")
        total = f"{planner.rub(r['total'])} за {nights} {planner.plural(nights, 'ночь', 'ночи', 'ночей')}" + (
            f", {r['units']} {planner.plural(r['units'], *unit)}" if r["units"] > 1 else "")
        meta = stay_meta(p)
        r["meta"] = " · ".join(m for m in [meta[0], meta[1], total, *meta[2:]] if m)
    opts["search"] = stay_search(ctx)
    return JSONResponse(opts)


def stay_search(ctx: planner.Ctx) -> list[dict]:
    """Поиск жилья на даты поездки у партнёров — «Своё жильё — найти на даты поездки»."""
    out = [{"label": "Суточно.ру", "url": schedule.partner_any(planner.sutochno_link(ctx))},
           {"label": "Островок", "url": schedule.partner_any(planner.ostrovok_link(ctx))},
           {"label": "Авито", "url": planner.avito_link(ctx)},
           {"label": "Trip.com", "url": schedule.partner_any(planner.tripcom_link(ctx))}]
    for link in out:
        link["ad"] = schedule.ad_label(link["url"])
    return out


async def stay_preview(request: Request) -> JSONResponse:
    """Объявление Суточно.ру по ссылке — показать в форме до построения поездки."""
    try:
        body = await request.json()
        place = await run_in_threadpool(sutochno.load, str(body.get("url") or ""))
    except ValueError as e:
        return err(422, str(e))
    except Exception:
        return err(400, "Ожидается JSON со ссылкой")
    fn = catalog.normalize_city(str(body.get("city") or ""))
    city = catalog.load(fn)["city"] if fn else None
    if city and place.get("city") and place["city"] != city:
        return err(422, f"Это объявление в городе {place['city']}, а поездка — в {city}")
    return JSONResponse({"id": place["id"], "name": place["name"], "meta": " · ".join(m for m in stay_meta(place) if m),
                         "photo": sutochno.thumb(place["photos"][0]) if place.get("photos") else None})


def checkin_item(trip: dict) -> dict | None:
    return next((it for d in trip["days"] for it in d["items"] if it.get("stay_role") in ("checkin", "checkin_late")), None)


async def create_trip(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return err(400, "Ожидается JSON")
    # «Уже есть бронь»: объявление с Суточно.ру по ссылке — загружаем до построения, чтобы ошибку показать в форме
    stay_url = str(body.get("stay_url") or "").strip() if isinstance(body, dict) else ""
    booked = None
    if stay_url:
        try:
            booked = await run_in_threadpool(sutochno.load, stay_url)
        except ValueError as e:
            print(f"[sutochno] {stay_url!r}: {e}", flush=True)
            return err(422, f"Жильё по ссылке: {e}")
    if isinstance(body, dict):
        body = await run_in_threadpool(wishes_ai.apply, body)  # пожелания словами -> настройки (если есть ключ)
    tickets = [b for b in body.get("bookings") or [] if isinstance(b, dict)] if isinstance(body, dict) else []
    try:
        if not booked and isinstance(body.get("stay_manual"), dict) and str(body["stay_manual"].get("name") or "").strip():
            booked = planner.manual_stay_place(body["stay_manual"])  # бронь на другом сайте, введена вручную
        if booked:  # своя бронь — программа сразу от этого жилья: его время заезда и дорога до него
            body["stay_id"] = booked["id"]
        fn = catalog.normalize_city(str(body.get("city") or ""))
        center = catalog.load(fn)["center"] if fn else [0, 0]
        events = [e for i, b in enumerate(tickets) if b.get("role") == "event" and (e := bookings.event_place(b, i, center))]
        trip = planner.plan_trip(body, {booked["id"]: booked} if booked else None, events)
        if booked and not checkin_item(trip):
            raise ValueError("Поездка на один день — жильё не нужно, уберите ссылку на бронь")
    except (ValueError, TypeError) as e:
        return err(422, str(e))
    uid = request.state.user["id"]
    tid = store.create(trip, uid)
    if attach_bookings(tid, trip, tickets, uid):
        store.save(tid, trip)
    return JSONResponse(present(tid, trip), status_code=201)


def attach_bookings(tid: str, trip: dict, tickets: list[dict], uid: int) -> bool:
    """Брони из формы — документом «оплачено» к своим пунктам (дорога туда и обратно, заселение, события
    по билетам), с файлом, если его загрузили."""
    items = [it for d in trip["days"] for it in d["items"]]
    legs = [it for it in items if it["kind"] == "travel" and it["cost"]["kind"] in ("ticket", "receipt")]
    changed = False
    adopted: dict[tuple[str, str], str | None] = {}
    for i, b in enumerate(tickets):
        f = b.get("file") if isinstance(b.get("file"), dict) else None
        target = {"out": legs[0] if legs else None, "back": legs[-1] if len(legs) > 1 else None,
                  "stay": next((it for it in items if it.get("stay_role") in ("checkin", "checkin_late")), None),
                  "event": next((it for it in items if it.get("place_id") == f"event-{i}"), None)}.get(b.get("role"))
        if not target:
            continue
        name = None  # бронь из текста письма — без файла, но тоже оплачена
        if f:
            key = (str(f.get("token") or ""), str(f.get("name") or ""))
            if key in adopted:  # тот же PDF с несколькими билетами — каждому пункту своя копия файла
                if adopted[key] in [x["name"] for x in (target.get("doc") or {}).get("files", [])]:
                    continue
                src = store.file_path(tid, adopted[key])
                name = store.save_file(tid, src.name, src.read_bytes()) if src else None
            else:
                name = adopted[key] = store.adopt_pending(*key, tid, uid)
        files = [{"name": name, "filename": str(f.get("filename") or name)}] if name else []
        if name and not f.get("trip"):  # в вводных — ссылка на файл поездки: открыть билет и из «Изменить вводные»
            b["file"] = dict(f, trip=tid, name=name)
        if target.get("doc"):  # туда и обратно одним билетом — второй файл к тому же документу
            target["doc"]["files"] += files
        else:
            planner.attach_booking(trip, target["id"], b, files)
        changed = True
    if changed:
        planner.recompute(trip)
    return changed


async def parse_bookings(request: Request) -> JSONResponse:
    """Загруженные в форме билеты и брони (PDF, текст письма) -> распознанные брони с ролью в поездке."""
    form = await request.form()
    origin, city = str(form.get("origin") or ""), str(form.get("city") or "")
    token = uuid.uuid4().hex[:16]
    found, errors = [], []
    sources = [(u.filename, await u.read()) for u in form.getlist("file") if getattr(u, "filename", None)]
    if str(form.get("text") or "").strip():
        sources.append(("", str(form.get("text")).encode("utf-8")))
    for filename, content in sources[:10]:
        what = filename or "текст письма"
        try:
            text = await run_in_threadpool(bookings.text_of, filename or "письмо.txt", content)  # фото — OCR, по сети
            if not text:
                errors.append(f"{what}: " + ("не нашли текста — пришлите PDF из письма или фото в JPG или PNG"
                                             if bookings.ocr_enabled() else
                                             "фото и сканы пока не распознаём — загрузите PDF из письма или вставьте текст"))
                continue
            got = await run_in_threadpool(bookings.parse, text, origin, city)
            saved = None
            if filename and got:
                name = store.save_file(store.pending_dir(request.state.user["id"], token), filename, content)
                saved = {"token": token, "name": name, "filename": filename}
            for b in got:
                found.append(dict(b, file=saved, source=what))
            if not got:
                errors.append(f"{what}: брони с датой не нашли")
        except ValueError as e:
            errors.append(f"{what}: {e}")
        except Exception as e:  # сеть, модель — понятная ошибка вместо 500
            print(f"[bookings] {what}: {type(e).__name__}: {e}", flush=True)
            errors.append(f"{what}: не удалось распознать, попробуйте ещё раз или заполните вручную")
    return JSONResponse({"bookings": found, "errors": errors})


def load_trip(request: Request, tid: str) -> dict:
    """Поездка владельца; чужая — как несуществующая: 404, а не 403, чтобы не подтверждать, что такая есть."""
    trip = store.get(tid, request.state.user["id"])
    if trip is None:
        raise HTTPException(404, "Поездка не найдена")
    return trip


# ------------------------------------------------------------------ аккаунты

def _session_response(data: dict, request: Request, token: str | None, status: int = 200) -> JSONResponse:
    resp = JSONResponse(data, status_code=status)
    if token:
        resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
    else:
        resp.delete_cookie(auth.COOKIE, path="/")
    return resp


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Ожидается JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "Ожидается JSON-объект")
    return body


async def auth_register(request: Request) -> JSONResponse:
    body = await _json(request)
    try:
        user = await run_in_threadpool(auth.register, str(body.get("email") or ""), str(body.get("password") or ""),
                                       str(body.get("name") or ""))
    except ValueError as e:
        return err(422, str(e))
    return _session_response({"user": user}, request, auth.new_session(user["id"]), 201)


async def auth_login(request: Request) -> JSONResponse:
    body = await _json(request)
    ip = request.client.host if request.client else ""
    try:
        user = await run_in_threadpool(auth.login, str(body.get("email") or ""), str(body.get("password") or ""), ip)
    except PermissionError as e:
        return err(429, str(e))
    except ValueError as e:
        return err(401, str(e))
    return _session_response({"user": user}, request, auth.new_session(user["id"]))


async def auth_logout(request: Request) -> JSONResponse:
    auth.end_session(request.cookies.get(auth.COOKIE))
    return _session_response({"ok": True}, request, None)


async def auth_me(request: Request) -> JSONResponse:
    user = auth.session_user(request.cookies.get(auth.COOKIE))
    return JSONResponse({"user": user}) if user else err(401, "Войдите в аккаунт")


async def auth_forgot(request: Request) -> JSONResponse:
    """Письмо со ссылкой на новый пароль. Отвечаем одинаково, есть такая почта или нет."""
    body = await _json(request)
    try:
        await run_in_threadpool(auth.request_reset, str(body.get("email") or ""))
    except ValueError as e:
        return err(422, str(e))
    return JSONResponse({"ok": True})


async def auth_reset(request: Request) -> JSONResponse:
    """GET — годна ли ссылка из письма, POST — задать новый пароль и сразу войти."""
    if request.method == "GET":
        user = await run_in_threadpool(auth.reset_user, request.query_params.get("token") or "")
        return JSONResponse({"user": user}) if user else err(404, "Ссылка устарела — запросите письмо заново")
    body = await _json(request)
    try:
        user = await run_in_threadpool(auth.reset_password, str(body.get("token") or ""), str(body.get("password") or ""))
    except ValueError as e:
        return err(422, str(e))
    return _session_response({"user": user}, request, auth.new_session(user["id"]))


async def auth_password(request: Request) -> JSONResponse:
    body = await _json(request)
    uid = request.state.user["id"]
    try:
        await run_in_threadpool(auth.change_password, uid, str(body.get("old") or ""), str(body.get("new") or ""))
    except ValueError as e:
        return err(422, str(e))
    # прежние сессии закрыты (выход на других устройствах) — здесь остаёмся в аккаунте
    return _session_response({"ok": True}, request, auth.new_session(uid))


async def list_trips(request: Request) -> JSONResponse:
    return JSONResponse({"trips": store.list_trips(request.state.user["id"])})


async def delete_trip(request: Request) -> JSONResponse:
    if not store.delete(request.path_params["tid"], request.state.user["id"]):
        return err(404, "Поездка не найдена")
    return JSONResponse({"ok": True})


async def get_trip(request: Request) -> JSONResponse:
    tid = request.path_params["tid"]
    return JSONResponse(present(tid, load_trip(request, tid)))


def put_excursion(trip: dict, iid: str, pid: str) -> dict:
    """Экскурсия Трипстера вместо места программы: из каталога города — в поездку."""
    ctx = planner.trip_ctx(trip)
    _, it = planner.find_item(trip, iid)
    found = tripster.find(ctx.city, int(pid.removeprefix("tripster-"))) if pid.removeprefix("tripster-").isdigit() else None
    if not found:
        raise ValueError("Эта экскурсия больше не продаётся на Трипстере — обновите страницу")
    # с выезда за город экскурсии стартуют в городе — ставим точку в центр, иначе дорога «до Свияжска» посчитается дважды
    lat, lon = (ctx.data["center"] if it["kind"] == "daytrip" else (it["lat"], it["lon"]))
    return planner.replace_with_custom(trip, iid, tripster.as_place(*found, lat, lon))


async def patch_item(request: Request) -> JSONResponse:
    tid, iid = request.path_params["tid"], request.path_params["iid"]
    trip = load_trip(request, tid)
    try:
        body = await request.json()
    except Exception:
        return err(400, "Ожидается JSON")
    try:
        if "duration" in body:
            trip = planner.set_duration(trip, iid, int(body["duration"]))
        elif str(body.get("replace_with", "")).startswith("tripster-"):
            trip = put_excursion(trip, iid, str(body["replace_with"]))
        elif "replace_with" in body:
            trip = planner.replace_place(trip, iid, str(body["replace_with"]))
        else:
            return err(422, "Нужно поле duration или replace_with")
    except KeyError:
        return err(404, "Пункт не найден")
    except ValueError as e:
        return err(422, str(e))
    store.save(tid, trip)
    return JSONResponse(present(tid, trip))


async def put_place(request: Request) -> JSONResponse:
    """Своё кафе или ресторан по ссылке с Яндекс Карт — вместо завтрака, обеда или ужина."""
    tid, iid = request.path_params["tid"], request.path_params["iid"]
    trip = load_trip(request, tid)
    try:
        body = await request.json()
    except Exception:
        return err(400, "Ожидается JSON")
    try:
        place = await run_in_threadpool(yandexmaps.load, str(body.get("url") or ""))
        trip = planner.replace_with_custom(trip, iid, place)
    except KeyError:
        return err(404, "Пункт не найден")
    except ValueError as e:
        print(f"[yandexmaps] {body.get('url')!r}: {e}", flush=True)
        return err(422, str(e))
    store.save(tid, trip)
    return JSONResponse(present(tid, trip))


async def attach_doc(request: Request) -> JSONResponse:
    tid, iid = request.path_params["tid"], request.path_params["iid"]
    trip = load_trip(request, tid)
    form = await request.form()
    doc = {k: form.get(k) for k in ("title", "sum", "paid", "date", "start", "end")}
    saved: list[dict] = []
    try:
        planner.find_item(trip, iid)
        for upload in form.getlist("file"):
            if not getattr(upload, "filename", None):
                continue
            content = await upload.read()
            saved.append({"name": store.save_file(tid, upload.filename, content), "filename": upload.filename})
        trip = planner.attach_doc(trip, iid, doc, saved)
    except KeyError:
        return err(404, "Пункт не найден")
    except ValueError as e:
        for f in saved:
            store.delete_file(tid, f["name"])
        return err(422, str(e))
    store.save(tid, trip)
    return JSONResponse(present(tid, trip))


async def delete_doc_file(request: Request) -> JSONResponse:
    tid, iid, name = request.path_params["tid"], request.path_params["iid"], request.path_params["name"]
    trip = load_trip(request, tid)
    try:
        trip = planner.remove_doc_file(trip, iid, name)
    except KeyError:
        return err(404, "Файл не найден")
    store.delete_file(tid, name)
    store.save(tid, trip)
    return JSONResponse(present(tid, trip))


async def delete_item(request: Request) -> JSONResponse:
    tid, iid = request.path_params["tid"], request.path_params["iid"]
    trip = load_trip(request, tid)
    try:
        trip, old = planner.delete_item(trip, iid)
    except KeyError:
        return err(404, "Пункт не найден")
    except ValueError as e:
        return err(422, str(e))
    for f in (old.get("doc") or {}).get("files", []):
        store.delete_file(tid, f["name"])
    store.save(tid, trip)
    return JSONResponse(present(tid, trip))


async def suggest(request: Request) -> JSONResponse:
    tid, day = request.path_params["tid"], request.path_params["day"]
    trip = load_trip(request, tid)
    if not 0 <= day < len(trip["days"]):
        return err(404, "Нет такого дня")
    after = request.query_params.get("after") or None
    try:
        origin = planner.in_origin(trip, day, after)
        pids = planner.suggestions(trip, day, after_id=after)
    except ValueError as e:
        return err(422, str(e))
    ctx = planner.trip_ctx(trip)
    prev = planner.previous_point(trip["days"][day], after)
    places = []
    for pid in pids:
        row = describe(ctx, pid)
        dist = planner.distance_from(ctx, prev, ctx.place(pid)) or {}
        places.append(dict(row, rating=ctx.place(pid).get("rating"), km=dist.get("km"), dist=dist.get("label")))
    return JSONResponse({"places": planner.by_rating_and_distance(places),
                         "origin": trip["input"]["origin"] if origin else None})


async def add_item(request: Request) -> JSONResponse:
    tid, day = request.path_params["tid"], request.path_params["day"]
    trip = load_trip(request, tid)
    try:
        body = await request.json()
    except Exception:
        return err(400, "Ожидается JSON")
    try:
        trip = planner.add_item(trip, day, body.get("after"), body.get("place_id"),
                                body.get("title"), body.get("duration"))
    except ValueError as e:
        return err(422, str(e))
    store.save(tid, trip)
    return JSONResponse(present(tid, trip))


async def detach_doc(request: Request) -> JSONResponse:
    tid, iid = request.path_params["tid"], request.path_params["iid"]
    trip = load_trip(request, tid)
    try:
        trip, old = planner.detach_doc(trip, iid)
    except KeyError:
        return err(404, "Пункт не найден")
    for f in (old or {}).get("files", []):
        store.delete_file(tid, f["name"])
    store.save(tid, trip)
    return JSONResponse(present(tid, trip))


async def get_file(request: Request) -> Response:
    tid = request.path_params["tid"]
    p = store.file_path(tid, request.path_params["name"]) if store.owns(tid, request.state.user["id"]) else None
    if not p:
        raise HTTPException(404, "Файл не найден")
    return FileResponse(p, filename=request.query_params.get("filename") or None,
                        content_disposition_type="attachment" if "download" in request.query_params else "inline")


async def get_pending_file(request: Request) -> Response:
    """Билет, загруженный в форме, пока поездка не создана."""
    token = request.path_params["token"]
    folder = store.pending_dir(request.state.user["id"], token)  # только свои: папка с номером пользователя
    p = store.file_path(folder, request.path_params["name"]) if folder else None
    if not p:
        raise HTTPException(404, "Файл не найден — загрузите билет ещё раз")
    return FileResponse(p, filename=request.query_params.get("filename") or None,
                        content_disposition_type="attachment" if "download" in request.query_params else "inline")


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return err(exc.status_code, exc.detail)


class FrontFiles(StaticFiles):
    """Сборка фронтенда без хеша в имени: браузер должен сверять файл с сервером, иначе после npm run build работает старый."""

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


async def spa(request: Request) -> Response:
    index = FRONT / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return Response("Фронтенд не собран: выполните npm run build в папке frontend", media_type="text/plain; charset=utf-8")


routes = [
    Route("/api/health", health),
    Route("/api/auth/register", auth_register, methods=["POST"]),
    Route("/api/auth/login", auth_login, methods=["POST"]),
    Route("/api/auth/logout", auth_logout, methods=["POST"]),
    Route("/api/auth/me", auth_me),
    Route("/api/auth/forgot", auth_forgot, methods=["POST"]),
    Route("/api/auth/reset", auth_reset, methods=["GET", "POST"]),
    Route("/api/auth/password", auth_password, methods=["POST"]),
    Route("/api/meta", meta),
    Route("/api/stays", stays),
    Route("/api/stays/preview", stay_preview, methods=["POST"]),
    Route("/api/bookings", parse_bookings, methods=["POST"]),
    Route("/api/bookings/files/{token}/{name}", get_pending_file),
    Route("/api/address/suggest", address_suggest),
    Route("/api/schedule", schedule_search),
    Route("/api/trips", create_trip, methods=["POST"]),
    Route("/api/trips", list_trips),
    Route("/api/trips/{tid}", get_trip),
    Route("/api/trips/{tid}", delete_trip, methods=["DELETE"]),
    Route("/api/trips/{tid}/items/{iid}", patch_item, methods=["PATCH"]),
    Route("/api/trips/{tid}/items/{iid}", delete_item, methods=["DELETE"]),
    Route("/api/trips/{tid}/items/{iid}/doc/files/{name}", delete_doc_file, methods=["DELETE"]),
    Route("/api/trips/{tid}/days/{day:int}/suggest", suggest),
    Route("/api/trips/{tid}/days/{day:int}/items", add_item, methods=["POST"]),
    Route("/api/trips/{tid}/items/{iid}/place", put_place, methods=["POST"]),
    Route("/api/trips/{tid}/items/{iid}/doc", attach_doc, methods=["POST"]),
    Route("/api/trips/{tid}/items/{iid}/doc", detach_doc, methods=["DELETE"]),
    Route("/api/trips/{tid}/files/{name}", get_file),
]
routes.append(Mount("/assets", FrontFiles(directory=FRONT / "assets", check_dir=False), name="assets"))
routes.append(Route("/{path:path}", spa))

@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    tripster.warm_up()  # каталоги экскурсий — в фоне, сервер стартует сразу
    yield


PUBLIC_API = {"/api/health", "/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/me",
              "/api/auth/forgot", "/api/auth/reset"}


class RequireLogin:
    """Весь /api/ — только с входом в аккаунт (кроме входа и регистрации): поездки личные, а подсказки адресов,
    расписания и распознавание билетов тратят платные ключи. Пользователь — в request.state.user."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/") and scope["path"] not in PUBLIC_API:
            request = Request(scope)
            user = auth.session_user(request.cookies.get(auth.COOKIE))
            if not user:
                await err(401, "Войдите в аккаунт")(scope, receive, send)
                return
            scope.setdefault("state", {})["user"] = user
        await self.app(scope, receive, send)


app = Starlette(routes=routes, exception_handlers={HTTPException: http_error}, lifespan=lifespan,
                middleware=[Middleware(RequireLogin)])
