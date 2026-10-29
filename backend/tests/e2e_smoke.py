"""Сквозная проверка в браузере (Playwright).

Форма -> маршрут -> длительность, замена, «ещё», удаление, добавление -> документ с двумя файлами
и проверкой даты -> «К пункту» -> возврат к вводным -> телефон.

Запуск: python tests/e2e_smoke.py [папка_для_скриншотов]
Сервер поднимается свой — на свободном порту, с пустой временной базой и без платных ключей (YandexGPT, Vision,
Трипстер): живые поездки на :8000 и первый аккаунт не трогаем, запросы к платным API не тратим.
"""
import base64
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

os.environ["SH_DATA_DIR"] = tempfile.mkdtemp(prefix="sh-e2e-")
for _k in ("YANDEX_GPT_API_KEY", "YANDEX_VISION_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
           "TRIPSTER_PARTNER", "TRIPSTER_SECRET"):
    os.environ[_k] = ""  # backend/.env не перекрывает заданное

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import uvicorn  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from app import mailer  # noqa: E402
from app.main import app  # noqa: E402

letters: list[str] = []  # письма вместо SMTP — чтобы пройти путь «забыли пароль» до конца
mailer.send = lambda to, subject, text: bool(letters.append(text)) or True

_sock = socket.socket()
_sock.bind(("127.0.0.1", 0))
PORT = _sock.getsockname()[1]
_sock.close()
_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=_server.run, daemon=True).start()
for _ in range(200):
    if _server.started:
        break
    time.sleep(0.05)
BASE = f"http://127.0.0.1:{PORT}"
os.environ["APP_URL"] = BASE  # ссылка в письме — на свой сервер, а не на живой :8000
out = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-shots")
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []

# 1×1 PNG — подменяем ответы Википедии, чтобы тест не зависел от интернета
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkqH9aDwAEgQGAz4mlJAAAAABJRU5ErkJggg==")


def fake_wiki(route):
    if "rest_v1/page/summary" in route.request.url:
        route.fulfill(content_type="application/json",
                      body=json.dumps({"thumbnail": {"source": "https://upload.wikimedia.org/test.png"},
                                       "content_urls": {"desktop": {"page": "https://ru.wikipedia.org/wiki/Test"}}}))
    elif "upload.wikimedia.org" in route.request.url:
        route.fulfill(content_type="image/png", body=PIXEL)
    else:
        route.abort()


def setup(page):
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))
    page.on("requestfailed", lambda r: "fonts.g" not in r.url and errors.append(f"запрос не удался: {r.url}"))
    page.route("https://ru.wikipedia.org/**", fake_wiki)
    page.route("https://upload.wikimedia.org/**", fake_wiki)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    setup(page)

    # --- без входа — страница входа; регистрация открывает форму
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.ru"
    page.goto(BASE + "/")
    expect(page.get_by_role("heading", name="Вход")).to_be_visible()
    page.screenshot(path=str(out / "0-login.png"), full_page=True)
    page.get_by_role("tab", name="Регистрация").click()
    page.get_by_label("Как к вам обращаться").fill("Тест")
    page.get_by_label("Почта").fill(email)
    page.get_by_label("Пароль").fill("коротк")
    page.get_by_role("button", name="Создать аккаунт").click()
    expect(page.get_by_role("heading", name="Регистрация")).to_be_visible()  # короткий пароль не пропустит браузер
    page.get_by_label("Пароль").fill("secret-pass-1")
    # «Показать» открывает пароль, чтобы увидеть опечатку
    page.get_by_role("button", name="Показать").click()
    expect(page.get_by_label("Пароль")).to_have_attribute("type", "text")
    page.get_by_role("button", name="Скрыть").click()
    page.screenshot(path=str(out / "0-register.png"), full_page=True)
    page.get_by_role("button", name="Создать аккаунт").click()

    # --- форма
    expect(page.get_by_role("button", name="Выйти")).to_be_visible()
    expect(page.get_by_role("heading", name="Откуда и куда")).to_be_visible()
    # подсказки адресов: ответ сервера подменяем, чтобы не зависеть от внешнего сервиса
    def fake_schedule(route):
        out = "from=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0" in route.request.url  # from=Москва
        trips = [("752А", "07:00", "10:55"), ("754А", "08:00", "11:55")] if out else \
            [("760А", "19:30", "23:25"), ("762А", "20:00", "23:55")]
        route.fulfill(content_type="application/json", body=json.dumps({"available": True, "items": [
            {"number": n, "title": "Москва — Санкт-Петербург", "carrier": "ФПК", "departure": d, "arrival": a,
             "next_day": False, "duration_min": 235, "from": "", "to": "", "price_from": 4100} for n, d, a in trips],
            "buy_links": [{"label": "Яндекс Путешествия", "url": "https://travel.yandex.ru/trains/moscow--saint-petersburg/?when=2026-10-27"},
                          {"label": "Туту.ру", "url": "https://www.tutu.ru/poezda/Moskva/Sankt-Peterburg/"}] if out else []}))
    page.route("**/api/schedule*", fake_schedule)
    page.route("**/api/address/suggest*", lambda r: r.fulfill(content_type="application/json", body=json.dumps({
        "provider": "photon",
        "suggestions": [{"value": "ул. Тверская, 7", "full": "Москва, ул. Тверская, 7", "lat": 55.7579, "lon": 37.6115,
                         "to_station_min": 50, "station": "Ленинградский вокзал"},
                        {"value": "Тверская площадь", "full": "Москва, Тверская площадь", "lat": 55.761, "lon": 37.609}]})))
    expect(page.locator(".with-prefix .prefix")).to_have_text("Москва,")
    page.get_by_label("Откуда выходите: улица и дом").fill("Тверская 7")
    page.get_by_role("option", name="ул. Тверская, 7").click()
    expect(page.get_by_label("Откуда выходите: улица и дом")).to_have_value("ул. Тверская, 7")
    expect(page.get_by_text("Дорога до места отправления ≈ 50 мин (Ленинградский вокзал)")).to_be_visible()
    expect(page.get_by_label("Дорога до вокзала, мин")).to_have_count(0)
    page.get_by_label("Дата выезда").fill("2026-10-27")
    page.get_by_label("Дата возвращения").fill("2026-10-29")
    expect(page.get_by_text("3 дня")).to_be_visible()
    expect(page.locator(".leg legend").first).to_have_text("Туда · 27 октября, вт · Москва → Санкт-Петербург")
    expect(page.locator(".leg legend").nth(1)).to_have_text("Обратно · 29 октября, чт · Санкт-Петербург → Москва")
    # точное расписание поезда туда и обратно
    there = page.get_by_role("group", name="Транспорт туда")
    back_tr = page.get_by_role("group", name="Транспорт обратно")
    there.get_by_role("button", name="Поезд", exact=True).click()
    expect(back_tr.get_by_role("button", name="Поезд", exact=True)).to_have_attribute("aria-pressed", "true")
    # туда: вводим только номер поезда — время подставляется из расписания
    page.get_by_label("№ поезда").first.fill("752a")
    expect(page.get_by_text("время подставлено")).to_be_visible()
    expect(page.get_by_label("Отправление поезда").first).to_have_value("07:00")
    expect(page.get_by_label("Прибытие").first).to_have_value("10:55")
    expect(page.get_by_text("билет от 4 100 ₽ за взрослого — учтём в бюджете")).to_be_visible()
    expect(page.get_by_role("link", name="Яндекс Путешествия ↗")).to_have_attribute(
        "href", "https://travel.yandex.ru/trains/moscow--saint-petersburg/?when=2026-10-27")
    expect(page.get_by_role("link", name="Туту.ру ↗")).to_have_attribute("href", "https://www.tutu.ru/poezda/Moskva/Sankt-Peterburg/")
    page.get_by_label("№ поезда").first.fill("752А")
    # обратно: выбираем поезд из списка
    # после выбора «Поезд» расписание «туда» открывается само
    expect(page.locator(".leg").first.locator(".trips")).to_be_visible()
    expect(page.get_by_role("button", name="07:00 → 10:55")).to_be_visible()
    # обратно: список открывается кликом по полю «№ поезда»
    page.get_by_label("№ поезда").nth(1).click()
    page.get_by_role("button", name="19:30 → 23:25").click()
    expect(page.get_by_label("№ поезда").nth(1)).to_have_value("760А")
    # переключились на самолёт и обратно — выбранные поезда на месте
    there.get_by_role("button", name="Самолёт", exact=True).click()
    expect(page.get_by_label("№ рейса").first).to_have_value("")
    there.get_by_role("button", name="Поезд", exact=True).click()
    expect(page.get_by_label("№ поезда").first).to_have_value("752А")
    expect(page.get_by_label("№ поезда").nth(1)).to_have_value("760А")
    expect(page.get_by_label("Отправление поезда").nth(1)).to_have_value("19:30")
    expect(page.get_by_label("Прибытие").nth(1)).to_have_value("23:25")
    # обратно — на другом транспорте: «туда» не трогаем, вернулись на поезд — выбранный поезд на месте
    back_tr.get_by_role("button", name="Самолёт", exact=True).click()
    expect(page.get_by_label("№ поезда")).to_have_count(1)
    expect(page.get_by_label("№ поезда").first).to_have_value("752А")
    expect(page.get_by_label("№ рейса")).to_have_value("")
    back_tr.get_by_role("button", name="Поезд", exact=True).click()
    expect(page.get_by_label("№ поезда").nth(1)).to_have_value("760А")
    # где жить — как «⇄ Заменить» у жилья в маршруте
    stay_sec = page.locator(".sec").filter(has_text="Где жить").first
    chosen = stay_sec.locator(".stay-chosen")
    expect(chosen).to_contain_text("Подберём сами по бюджету")
    expect(chosen).to_contain_text("за 2 ночи")
    chosen.get_by_role("button", name="⇄ Заменить").click()
    stay_url = stay_sec.get_by_placeholder("https://spb.sutochno.ru/361449")
    stay_url.fill("не ссылка")
    stay_url.press("Enter")  # своя бронь — не отправляет всю форму
    expect(stay_sec.locator(".hint.warn")).to_be_visible()
    expect(page.get_by_role("heading", name="Откуда и куда")).to_be_visible()
    first = stay_sec.locator(".alts .alt").first
    name = first.locator("b").inner_text()
    first.get_by_role("button", name="Выбрать").click()
    expect(chosen).to_contain_text("Ваш выбор")
    expect(chosen).to_contain_text(name)
    # бронь на другом сайте — вручную: без адреса из подсказок не сохранить
    chosen.get_by_role("button", name="⇄ Заменить").click()
    stay_sec.get_by_role("button", name="Бронь на другом сайте (Авито, Trip.com, Островок…) — ввести вручную").click()
    manual = stay_sec.locator(".manual-stay")
    manual.get_by_label("Название").fill("Квартира с Авито")
    manual.get_by_role("button", name="Жить здесь").click()
    expect(manual.locator(".hint.warn")).to_contain_text("адрес из подсказок")
    manual.get_by_label("Адрес — выберите из подсказок").fill("Тверская 7")
    manual.get_by_role("option", name="ул. Тверская, 7").click()
    manual.get_by_label("Цена за ночь, ₽").fill("4500")
    manual.get_by_role("button", name="Жить здесь").click()
    expect(chosen).to_contain_text("Своя бронь")
    expect(chosen).to_contain_text("Квартира с Авито")
    expect(chosen).to_contain_text("4 500 ₽ за ночь")
    chosen.get_by_role("button", name="⇄ Заменить").click()
    stay_sec.get_by_role("button", name="Подобрать самим по бюджету").click()
    expect(chosen).to_contain_text("Подберём сами по бюджету")
    page.get_by_role("button", name="Настройки поездки").click()
    page.get_by_label("Шаг кнопок − и + в маршруте").select_option("5")
    page.get_by_label("Запас к каждой достопримечательности, мин").fill("10")
    page.screenshot(path=str(out / "1-form-desktop.png"), full_page=True)
    page.get_by_role("button", name="Построить маршрут").click()

    # --- маршрут
    page.wait_for_url("**/trip/*")
    expect(page.get_by_role("heading", name="Выход из дома")).to_be_visible()
    expect(page.get_by_text("Москва, ул. Тверская, 7 → Ленинградский вокзал")).to_be_visible()
    # 07:00 поезд − 30 мин посадка − 50 мин дорога до вокзала = 05:40
    expect(page.locator(".tl-time b").first).to_have_text("05:40")
    expect(page.get_by_role("heading", name="Поезд Москва → Санкт-Петербург, № 752А")).to_be_visible()
    expect(page.get_by_text("Отправление 07:00 · прибытие 10:55 · поезд № 752А")).to_be_visible()
    page.get_by_role("tab", name="День 2").click()
    expect(page.locator(".photo img").first).to_be_visible()
    expect(page.get_by_text("пеший маршрут в Яндекс Картах").first).to_be_visible()

    # длительность с шагом 5 минут
    museum = page.locator(".tl-card").filter(has=page.locator(".kind.k-museum")).first
    museum.get_by_role("button", name="Длиннее на 5 минут").click()
    page.wait_for_timeout(300)

    # замена завтрака + «ещё варианты»
    bf = page.locator(".tl-card").filter(has_text="Завтрак в отеле").first
    bf.get_by_role("button", name="Заменить").click()
    more = bf.get_by_role("button", name="Ещё варианты")
    expect(more).to_be_visible()
    count_before = bf.locator(".alt").count()
    more.click()
    assert bf.locator(".alt").count() > count_before, "кнопка «Ещё варианты» не добавила вариантов"
    # поиск по названию: остаются подходящие, пустая строка возвращает все; у вариантов — расстояние
    expect(bf.locator(".alt-dist").first).to_contain_text("от предыдущего места")
    search = bf.get_by_role("searchbox", name="Поиск по названию")
    search.fill("штолле")
    expect(bf.locator(".alt")).not_to_have_count(0)
    for name in bf.locator(".alt b").all_inner_texts():
        assert "Штолле" in name, name
    search.fill("")
    expect(bf.get_by_role("button", name="Ещё варианты")).to_be_visible()
    bf.locator(".alt").first.get_by_role("button", name="Выбрать").click()
    page.wait_for_timeout(300)
    expect(page.get_by_role("heading", name="Завтрак · ").first).to_be_visible()

    # удалить пункт
    n_before = page.locator(".tl-item").count()
    page.once("dialog", lambda d: d.accept())
    page.locator(".tl-card").filter(has=page.locator(".kind.k-walk")).first.get_by_role("button", name="Убрать").click()
    page.wait_for_timeout(400)
    assert page.locator(".tl-item").count() == n_before - 1, "пункт не удалился"

    # добавить пункт после первого
    page.locator(".insert-btn").first.click()
    panel = page.locator(".add-panel")
    expect(panel.locator(".alt").first).to_be_visible()
    panel.locator(".alt").first.get_by_role("button", name="Выбрать").click()
    page.wait_for_timeout(400)
    assert page.locator(".tl-item").count() == n_before, "пункт не добавился"
    # свой пункт в конец дня
    page.get_by_role("button", name="+ Добавить пункт в конец дня").click()
    page.get_by_placeholder("Например: встреча с друзьями, шопинг").fill("Встреча с друзьями")
    page.locator(".custom-add").get_by_role("button", name="Добавить").click()
    expect(page.get_by_role("heading", name="Встреча с друзьями")).to_be_visible()

    # --- документ: бронь отеля, два файла, неверная дата -> предупреждение
    page.get_by_role("tab", name="День 1").click()
    hotel = page.locator(".tl-card").filter(has=page.locator(".kind.k-hotel")).first
    hotel.get_by_role("button", name="Приложить бронь").click()
    tmp = Path(tempfile.mkdtemp())
    (tmp / "bron.pdf").write_bytes(b"%PDF-1.4\n% booking\n")
    (tmp / "oplata.png").write_bytes(PIXEL)
    hotel.locator("input[type=file]").set_input_files([str(tmp / "bron.pdf"), str(tmp / "oplata.png")])
    hotel.locator("input[name=title]").fill("Бронь № 4817, 2 ночи")
    hotel.locator("input[name=sum]").fill("24600")
    hotel.locator("input[name=paid]").fill("12300")
    hotel.locator("input[name=date]").fill("2026-10-28")
    hotel.get_by_role("button", name="Сохранить").click()
    page.wait_for_timeout(500)
    expect(hotel.get_by_text("✓ Документ приложен")).to_be_visible()
    expect(hotel.locator(".files li")).to_have_count(2)
    expect(hotel.get_by_text("не совпадает с днём программы")).to_be_visible()
    page.screenshot(path=str(out / "2-trip-day1.png"), full_page=True)

    # --- «К пункту» из списка билетов: переключает день и прокручивает
    page.get_by_role("tab", name="День 1").click()
    row = page.locator(".docs li").filter(has_text="Петропавловская").first
    if row.count() == 0:
        row = page.locator(".docs li").last
    row.get_by_role("button", name="К пункту").click()
    page.wait_for_timeout(600)
    expect(page.locator(".tl-item.focused")).to_have_count(1)
    page.screenshot(path=str(out / "3-trip-focus.png"))

    # --- жильё в маршруте не заменить: «⇄ Изменить жильё» ведёт в вводные, к разделу «Где жить»
    page.get_by_role("tab").first.click()
    stay_card = page.locator(".tl-card").filter(has=page.get_by_role("button", name="⇄ Изменить жильё")).first
    expect(stay_card.get_by_role("button", name="⇄ Заменить")).to_have_count(0)
    stay_card.get_by_role("button", name="⇄ Изменить жильё").click()
    expect(page.get_by_role("heading", name="Изменить вводные поездки")).to_be_visible()
    expect(page.locator("#stay")).to_be_in_viewport()
    page.go_back()
    expect(page.get_by_role("heading", name="Выход из дома")).to_be_visible()

    # --- назад к вводным: форма заполнена
    page.get_by_role("button", name="← Изменить вводные: откуда, куда, даты").click()
    expect(page.get_by_role("heading", name="Изменить вводные поездки")).to_be_visible()
    expect(page.get_by_label("Откуда выходите: улица и дом")).to_have_value("ул. Тверская, 7")
    # выбранные поезда, время и даты тоже на месте
    expect(page.get_by_label("№ поезда").first).to_have_value("752А")
    expect(page.get_by_label("№ поезда").nth(1)).to_have_value("760А")
    expect(page.get_by_label("Отправление поезда").first).to_have_value("07:00")
    expect(page.get_by_label("Прибытие").nth(1)).to_have_value("23:25")
    expect(page.get_by_label("Дата выезда")).to_have_value("2026-10-27")
    page.go_back()
    expect(page.get_by_role("heading", name="Выход из дома")).to_be_visible()
    trip_url = page.url
    # браузерное «Назад» со страницы поездки на форму — введённое сохранилось (черновик)
    page.goto(BASE + "/")
    expect(page.get_by_label("Откуда выходите: улица и дом")).to_have_value("ул. Тверская, 7")
    expect(page.get_by_label("№ поезда").first).to_have_value("752А")
    # «Новая поездка» — чистая форма
    page.goto(BASE + "/?new")
    expect(page.get_by_label("Откуда выходите: улица и дом")).to_have_value("")

    # --- билеты и брони: распознанное заполняет «Туда» и встаёт в программу по времени (ответ сервера подменяем)
    def fake_bookings(route):
        route.fulfill(content_type="application/json", body=json.dumps({"errors": [], "bookings": [
            {"kind": "train", "title": "Поезд 752А Москва → Санкт-Петербург", "date": "2026-10-27", "start": "07:00",
             "end": "10:55", "end_date": "", "number": "752А", "from_city": "Москва", "to_city": "Санкт-Петербург",
             "venue": "", "address": "", "price": 8200, "persons": 2, "role": "out", "file": None, "source": "письмо"},
            {"kind": "theatre", "title": "Александринский театр: Ревизор", "date": "2026-10-27", "start": "19:30",
             "end": "", "end_date": "", "number": "", "from_city": "", "to_city": "", "venue": "Александринский театр",
             "address": "", "price": 4000, "persons": 2, "role": "event", "lat": 59.9324, "lon": 30.3366,
             "place_id": "alexandrinsky", "file": None, "source": "письмо"}]}))
    page.route("**/api/bookings", fake_bookings)
    page.get_by_label("Или вставьте текст письма с билетом").fill("Электронный билет РЖД 752А … Александринский театр …")
    page.get_by_role("button", name="Распознать текст").click()
    expect(page.get_by_role("list", name="Загруженные билеты и брони").get_by_role("listitem")).to_have_count(2)
    expect(page.locator(".hint.ok")).to_contain_text("в «Туда»")
    # билет туда загружен — он куплен: вместо выбора и «Купить» карточка оплаченного билета
    paid = page.locator(".leg-paid")
    expect(paid).to_have_count(1)
    expect(paid).to_contain_text("Билет загружен · оплачено")
    expect(paid).to_contain_text("07:00 → 10:55")
    expect(paid.get_by_role("link")).to_have_count(0)
    expect(page.get_by_role("group", name="Транспорт туда")).to_have_count(0)
    expect(page.locator(".booking-list")).to_contain_text("✓ оплачено")
    page.get_by_label("Дата выезда").fill("2026-10-27")
    page.get_by_label("Дата возвращения").fill("2026-10-29")
    page.get_by_role("button", name="Построить маршрут").click()
    show = page.get_by_role("heading", name="Александринский театр: Ревизор")
    expect(show).to_be_visible()
    expect(page.locator("li").filter(has=show).first).to_contain_text("19:30")
    expect(page.locator(".tl-card").filter(has=show).first).to_contain_text("оплачено полностью")
    expect(page.locator(".tl-card").filter(has=show).first).to_contain_text("✓ Оплачено")  # бронь из текста — без файла
    train = page.locator(".tl-card").filter(has=page.get_by_role("heading", name=re.compile("№ 752А"))).first
    expect(train).to_be_visible()
    expect(train.get_by_role("link", name=re.compile("Купить"))).to_have_count(0)
    expect(page.locator(".tl-card").filter(has=show).first.get_by_role("link", name=re.compile("Афиша|Купить"))).to_have_count(0)

    # --- телефон
    phone_ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
    phone_ctx.add_cookies(page.context.cookies())  # тот же аккаунт на телефоне
    phone = phone_ctx.new_page()
    setup(phone)
    phone.goto(trip_url)
    expect(phone.get_by_role("heading", name="Выход из дома")).to_be_visible()
    width = phone.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"горизонтальная прокрутка на телефоне: {width}px"
    phone.screenshot(path=str(out / "4-trip-phone.png"), full_page=True)
    phone.goto(BASE + "/")
    width = phone.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"горизонтальная прокрутка формы на телефоне: {width}px"
    phone.screenshot(path=str(out / "5-form-phone.png"), full_page=True)
    # --- «Мои поездки»: поездка в списке; выход — снова вход, поездка без входа не открывается
    page.goto(BASE + "/trips")
    expect(page.get_by_role("list", name="Мои поездки").get_by_role("listitem")).not_to_have_count(0)
    page.get_by_role("button", name="Выйти").click()
    expect(page.get_by_role("heading", name="Вход")).to_be_visible()
    page.goto(trip_url)
    expect(page.get_by_role("heading", name="Вход")).to_be_visible()

    # --- забыли пароль: письмо -> ссылка -> новый пароль -> вход и поездки на месте
    page.get_by_role("button", name="Забыли пароль?").click()
    expect(page.get_by_role("heading", name="Забыли пароль")).to_be_visible()
    page.get_by_label("Почта аккаунта").fill(email)
    page.screenshot(path=str(out / "0-forgot.png"), full_page=True)
    letters.clear()
    page.get_by_role("button", name="Прислать ссылку").click()
    expect(page.get_by_text(f"Письмо со ссылкой отправлено на {email}")).to_be_visible()
    assert len(letters) == 1, f"письмо не отправлено: {letters}"
    link = re.search(r"http\S+/reset\?token=\S+", letters[0]).group(0)
    page.goto(link)
    expect(page.get_by_role("heading", name="Новый пароль")).to_be_visible()
    expect(page.get_by_text(email)).to_be_visible()
    page.get_by_label("Новый пароль").fill("brand-new-pass")
    page.get_by_label("Ещё раз").fill("brand-new-другой")
    page.get_by_role("button", name="Сохранить и войти").click()
    expect(page.get_by_text("Пароли не совпадают")).to_be_visible()
    page.get_by_label("Ещё раз").fill("brand-new-pass")
    page.screenshot(path=str(out / "0-reset.png"), full_page=True)
    page.get_by_role("button", name="Сохранить и войти").click()
    expect(page.get_by_role("heading", name="Мои поездки")).to_be_visible()  # вошли сразу, поездки на месте
    expect(page.get_by_role("list", name="Мои поездки").get_by_role("listitem")).not_to_have_count(0)
    page.goto(link)  # ссылка одноразовая
    expect(page.get_by_text("Ссылка устарела")).to_be_visible()
    # старый пароль больше не подходит, новый — работает
    page.goto(BASE + "/trips")
    page.get_by_role("button", name="Выйти").click()
    expect(page.get_by_role("heading", name="Вход")).to_be_visible()
    page.get_by_label("Почта").fill(email)
    page.get_by_label("Пароль").fill("secret-pass-1")
    page.get_by_role("button", name="Войти").click()
    expect(page.get_by_text("Неверная почта или пароль")).to_be_visible()
    expect(page.get_by_label("Пароль")).to_have_value("")  # после ошибки поле пустое
    page.get_by_label("Пароль").fill("brand-new-pass")
    page.get_by_role("button", name="Войти").click()
    expect(page.get_by_role("heading", name="Откуда и куда")).to_be_visible()
    browser.close()
_server.should_exit = True

if errors:
    print("Ошибки в браузере:", *errors, sep="\n  ")
    sys.exit(1)
print("OK: сценарий пройден, скриншоты в", out)
