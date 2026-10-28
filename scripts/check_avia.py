"""Диагностика цен авиабилетов из Data API Авиасейлс (Travelpayouts).

Запуск из папки tripplan:  backend/.venv/bin/python scripts/check_avia.py
Пробует несколько вариантов запроса и пишет, что вернул API, в backend/var/avia_check.txt.
Токен в отчёт не попадает.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app import main  # noqa: E402,F401  — загружает backend/.env
from app import prices  # noqa: E402

token = os.environ.get("TP_TOKEN", "").strip()
day = (date.today() + timedelta(days=3)).isoformat()
lines = [f"Токен: {'есть, ' + str(len(token)) + ' символов' if token else 'НЕТ'}; дата {day}"]

VARIANTS = {
    "A. v3 на дату, в одну сторону, прямые": ("v3", {"departure_at": day, "one_way": "true", "direct": "true"}),
    "B. v3 на дату, unique=false, прямые": ("v3", {"departure_at": day, "one_way": "false", "direct": "true",
                                                  "unique": "false"}),
    "C. v3 на месяц, в одну сторону": ("v3", {"departure_at": day[:7], "one_way": "true", "direct": "false"}),
    "D. v3 без даты, в одну сторону": ("v3", {"one_way": "true"}),
    "E. v1 прямые рейсы на месяц": ("v1", {"depart_date": day[:7]}),
}
for name, (ver, extra) in VARIANTS.items():
    if ver == "v3":
        q = {"origin": "MOW", "destination": "LED", "sorting": "price", "limit": 100, "currency": "rub",
             "token": token, **extra}
        url = prices.AVIA_URL + "?" + urllib.parse.urlencode(q)
    else:
        q = {"origin": "MOW", "destination": "LED", "currency": "rub", "token": token, **extra}
        url = "https://api.travelpayouts.com/v1/prices/direct?" + urllib.parse.urlencode(q)
    lines.append(f"\n== {name}")
    try:
        data = prices._fetch(url)
    except urllib.error.HTTPError as e:
        lines.append(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        continue
    except Exception as e:
        lines.append(f"Ошибка: {type(e).__name__}: {e}")
        continue
    lines.append("Ключи ответа: " + ", ".join(data.keys()))
    rows = data.get("data")
    if isinstance(rows, dict):  # v1: {"LED": {"0": {...}}}
        rows = [v for city in rows.values() for v in (city.values() if isinstance(city, dict) else [])]
    rows = rows or []
    lines.append(f"Записей: {len(rows)}; success={data.get('success')}; error={data.get('error')}")
    for r in rows[:8]:
        lines.append("  " + json.dumps({k: r.get(k) for k in ("airline", "flight_number", "departure_at",
                                                              "return_at", "price", "transfers", "origin_airport")},
                                       ensure_ascii=False))
    if ver == "v3":
        lines.append(f"Подошло бы к расписанию на {day}: {prices.parse_avia(data, day)}")

out = BACKEND / "var" / "avia_check.txt"
out.parent.mkdir(exist_ok=True)
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print(f"\nГотово. Отчёт: {out}")
