"""Проверка подсказок адресов на реальных данных.

Запуск из папки tripplan:  backend/.venv/bin/python scripts/check_address.py
Отчёт пишется в backend/var/address_check.txt. Ключи на экран не выводятся.
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app import main  # noqa: E402,F401  — загружает backend/.env
from app import address  # noqa: E402

lines = []


def out(s=""):
    print(s)
    lines.append(s)


out(f"Источник подсказок: {address.provider()}")
for q in ["Тверская", "Тверская 7", "Ленинский проспект 3", "Профсоюзная"]:
    out(f"\n== «{q}» (Москва)")
    try:
        raw = address.fetch(q, (55.7765, 37.6553), "Москва", count=10)
        out(f"Сырых ответов от сервиса: {len(raw)}")
        for r in raw[:6]:
            out(f"   {r['value']}")
        res = address.suggest(q, "Москва", "train")
        out(f"Показали бы пользователю: {len(res['suggestions'])}")
        for r in res["suggestions"][:6]:
            out(f"   {r['value']}  (≈{r.get('to_station_min', '?')} мин)")
    except Exception as e:
        out(f"Ошибка: {type(e).__name__}: {e}")

var = BACKEND / "var"
var.mkdir(exist_ok=True)
(var / "address_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("\nГотово. Отчёт: backend/var/address_check.txt")
