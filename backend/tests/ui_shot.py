"""Снимки экрана для проверки вёрстки: свой сервер на временной базе, вход новым аккаунтом.

Запуск: .venv/bin/python tests/ui_shot.py <папка> [ширина]
Снимает раздел «Бюджет» формы и страницу построенной поездки. Живой сервер на :8000 не трогает.
"""
import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import os
import socket
import sys
import threading
import time
from pathlib import Path

for _k in ("YANDEX_GPT_API_KEY", "YANDEX_VISION_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
           "TRIPSTER_PARTNER", "TRIPSTER_SECRET", "DADATA_API_KEY"):
    os.environ[_k] = ""

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app import auth, planner, store  # noqa: E402
from app.main import app  # noqa: E402
from app import tripster  # noqa: E402

# экскурсии без ключей Трипстера — две выдуманные, чтобы снять список с кнопкой «Выбрать»
tripster.for_place = lambda place, city, kids=False, n=3: [
    {"id": i, "title": f"Экскурсия {i}: тестовая прогулка с гидом", "meta": "2 ч · 1 500 ₽ за человека",
     "url": "https://experience.tripster.ru/", "photo": None, "ad": "Реклама · тест"} for i in (1, 2)][:n]

s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
BASE = f"http://127.0.0.1:{PORT}"
out = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots")
out.mkdir(parents=True, exist_ok=True)
width = int(sys.argv[2]) if len(sys.argv) > 2 else 1280

user = auth.register("shot@mail.ru", "secret-pass-1", "Снимок")
token = auth.new_session(user["id"])
trip = planner.plan_trip({"origin": "Москва", "city": "Санкт-Петербург", "start_date": "2026-11-10", "start_time": "09:00",
                          "days": 2, "adults": 2, "budget": 80000, "transport": "train", "pace": "balanced"})
tid = store.create(trip, user["id"])

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.add_cookies([{"name": auth.COOKIE, "value": token, "url": BASE}])
    page = ctx.new_page()
    page.route("**wikipedia.org/**", lambda r: r.abort())
    page.route("**wikimedia.org/**", lambda r: r.abort())
    page.goto(BASE + "/")
    page.locator("section", has_text="Бюджет").first.screenshot(path=str(out / f"budget-{width}.png"))
    page.goto(BASE + f"/trip/{tid}")
    page.wait_for_selector("text=День 1")
    page.screenshot(path=str(out / f"trip-{width}.png"), full_page=False)
    card = page.locator(".tl-item", has=page.locator(".kind", has_text="Музей")).first
    card.locator("details.excursions summary").click() if card.locator("details.excursions").count() else None
    card.screenshot(path=str(out / f"sight-{width}.png"))
    browser.close()
print(f"снимки в {out}, поездка {tid}")
