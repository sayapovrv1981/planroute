"""Проверка ключа API Яндекс.Расписаний на реальных данных.

Запуск из папки tripplan:  backend/.venv/bin/python scripts/check_rasp.py
Ключ берётся из backend/.env и на экран не выводится.
Отчёт пишется в backend/var/rasp_check.txt, пример ответа — в backend/var/rasp_sample.json.
"""
import json
import os
import sys
import urllib.error
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app import main  # noqa: E402,F401  — загружает backend/.env
from app import prices, schedule  # noqa: E402

lines = []


def out(s=""):
    print(s)
    lines.append(s)


key = schedule.api_key()
out(f"Токен Travelpayouts для цен самолётов: {'есть' if os.environ.get('TP_TOKEN') else 'нет (TP_TOKEN в backend/.env)'}")
out(f"Ключ: {'есть, ' + str(len(key)) + ' символов' if key else 'НЕ НАЙДЕН в backend/.env'}")
day = (date.today() + timedelta(days=3)).isoformat()
sample = None
for kind in ("train", "plane", "bus"):
    out(f"\n== {kind}: Москва -> Санкт-Петербург, {day}")
    params = {"from": schedule.city_code("Москва"), "to": schedule.city_code("Санкт-Петербург"), "date": day,
              "transport_types": kind, "format": "json", "lang": "ru_RU", "limit": 100}
    try:
        raw = schedule._get(params)
    except urllib.error.HTTPError as e:
        out(f"Ошибка HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        continue
    except Exception as e:  # сеть, таймаут
        out(f"Ошибка: {type(e).__name__}: {e}")
        continue
    sample = sample or raw
    s = raw.get("search", {})
    out(f"Яндекс понял коды как: {s.get('from', {}).get('title')} -> {s.get('to', {}).get('title')}")
    items = schedule.parse(raw)
    out(f"Рейсов: {len(items)} (ещё с интервалом: {len(raw.get('interval_segments', []))})")
    note = prices.enrich(items, "Москва", "Санкт-Петербург", day, kind)
    out(f"Дополнительный источник цен: {note or 'нет данных (см. строки [prices] выше, если есть)'}")
    priced = [t for t in items if t["price_from"]]
    out(f"С ценой билета: {len(priced)} из {len(items)}"
        + (f", например {priced[0]['number'] or priced[0]['carrier']} от {priced[0]['price_from']} ₽" if priced else ""))
    for t in items[:5]:
        price = f", от {t['price_from']} ₽" if t["price_from"] else ""
        classes = ", ".join(f"{c['name']} {c['price']}" for c in t.get("classes", []))
        out(f"  {t['departure']} -> {t['arrival']}{' +1' if t['next_day'] else ''}  {t['number']}  "
            f"{t['title']}  {t['carrier']}{price}{'  [' + classes + ']' if classes else ''}")

out("\nКоды городов в приложении: " + ", ".join(f"{c} {k}" for k, c in schedule.CITY_CODES.items()))

var = BACKEND / "var"
var.mkdir(exist_ok=True)
(var / "rasp_check.txt").write_text("\n".join(lines), encoding="utf-8")
if sample:
    sample["segments"] = sample.get("segments", [])[:3]
    (var / "rasp_sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
print("\nГотово. Отчёт: backend/var/rasp_check.txt")
