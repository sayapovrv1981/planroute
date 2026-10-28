"""Диагностика автобусов из API Яндекс.Расписаний.

Запуск из папки tripplan:  backend/.venv/bin/python scripts/check_bus.py 2026-10-28
(дата — та, на которую смотрите расписание; без даты — через 3 дня).
Пишет отчёт в backend/var/bus_check.txt. Ключ в отчёт не попадает.
"""
import os
import sys
import urllib.error
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app import main  # noqa: E402,F401  — загружает backend/.env
from app import schedule  # noqa: E402

day = sys.argv[1] if len(sys.argv) > 1 else (date.today() + timedelta(days=3)).isoformat()
lines = [f"Дата: {day}"]
base = {"from": "c213", "to": "c2", "transport_types": "bus", "format": "json", "lang": "ru_RU", "limit": 100}
VARIANTS = {
    "A. как в приложении": {"date": day},
    "B. с пересадками": {"date": day, "transfers": "true"},
    "C. без фильтра по транспорту (только автобусы из ответа)": {"date": day, "transport_types": None},
    "D. обратно: Санкт-Петербург → Москва": {"date": day, "from": "c2", "to": "c213"},
}
for name, extra in VARIANTS.items():
    params = {**base, **extra}
    params = {k: v for k, v in params.items() if v is not None}
    lines.append(f"\n== {name}")
    try:
        raw = schedule._get(params)
    except urllib.error.HTTPError as e:
        lines.append(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        continue
    except Exception as e:
        lines.append(f"Ошибка: {type(e).__name__}: {e}")
        continue
    segs = raw.get("segments", [])
    pag = raw.get("pagination", {})
    lines.append(f"Сегментов: {len(segs)}, с интервалом: {len(raw.get('interval_segments', []))}, pagination={pag}")
    for s in segs:
        th = s.get("thread") or {}
        if th.get("transport_type") not in (None, "bus") and name.startswith("C"):
            continue
        lines.append("  {} → {}  {}  {} | от: {} | до: {} | пересадки: {}".format(
            (s.get("departure") or "")[11:16], (s.get("arrival") or "")[11:16], th.get("number") or "—",
            (th.get("carrier") or {}).get("title", ""), (s.get("from") or {}).get("title", ""),
            (s.get("to") or {}).get("title", ""), s.get("has_transfers")))
        if "details" in s:
            lines.append(f"     детали: {len(s['details'])} участков")
    for s in raw.get("interval_segments", []):
        th = s.get("thread") or {}
        lines.append(f"  [интервальный] {th.get('title')} {th.get('interval')}")

out = BACKEND / "var" / "bus_check.txt"
out.parent.mkdir(exist_ok=True)
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print(f"\nГотово. Отчёт: {out}")
