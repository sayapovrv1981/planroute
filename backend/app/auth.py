"""Аккаунты: регистрация и вход по почте и паролю, сессии в cookie.

Пароль храним только хешем scrypt (стандартная библиотека) с солью. Сессия — случайный токен в cookie
HttpOnly + SameSite=Lax; в базе лежит sha256 от него, так что утёкшая база не даёт войти чужими сессиями.
Подбор пароля: после MAX_FAILS неудачных попыток за FAIL_WINDOW по почте или по адресу — пауза.
Поездки, созданные до появления аккаунтов (без владельца), достаются первому зарегистрированному.
Дальше по плану — вход через Яндекс ID и VK ID и восстановление пароля по почте.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import store

COOKIE = "sh_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
MAX_FAILS = 5
FAIL_WINDOW = 15 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


# ------------------------------------------------------------------ passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), h)


def _check_new_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Пароль — не короче {MIN_PASSWORD} символов")
    if len(password) > 200:
        raise ValueError("Слишком длинный пароль")


# ------------------------------------------------------------------ users

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(row) -> dict:
    return {"id": row[0], "email": row[1], "name": row[2]}


def norm_email(email: str) -> str:
    return (email or "").strip().lower()


def register(email: str, password: str, name: str) -> dict:
    email, name = norm_email(email), (name or "").strip()[:80]
    if not EMAIL_RE.fullmatch(email) or len(email) > 200:
        raise ValueError("Проверьте адрес почты")
    if not name:
        raise ValueError("Напишите, как к вам обращаться")
    _check_new_password(password)
    with store._lock, store._db() as con:
        if con.execute("select 1 from users where email = ?", (email,)).fetchone():
            raise ValueError("Такая почта уже зарегистрирована — войдите")
        first = con.execute("select count(*) from users").fetchone()[0] == 0
        uid = con.execute("insert into users (email, name, password, created) values (?, ?, ?, ?)",
                          (email, name, hash_password(password), _now())).lastrowid
        if first:  # поездки, построенные до аккаунтов, — первому пользователю (владельцу прототипа)
            con.execute("update trips set user_id = ? where user_id is null", (uid,))
    return {"id": uid, "email": email, "name": name}


def _limited(*keys: str) -> bool:
    cutoff = time.time() - FAIL_WINDOW
    with _fails_lock:
        return any(len([t for t in _fails.get(k, []) if t > cutoff]) >= MAX_FAILS for k in keys)


def _fail(*keys: str) -> None:
    cutoff = time.time() - FAIL_WINDOW
    with _fails_lock:
        for k in keys:
            _fails[k] = [t for t in _fails.get(k, []) if t > cutoff] + [time.time()]


def login(email: str, password: str, ip: str = "") -> dict:
    """Проверить пароль; ValueError — неверно, PermissionError — слишком много попыток."""
    email = norm_email(email)
    keys = (f"email:{email}", f"ip:{ip}")
    if _limited(*keys):
        raise PermissionError("Слишком много попыток входа — подождите 15 минут")
    with store._lock, store._db() as con:
        row = con.execute("select id, email, name, password from users where email = ?", (email,)).fetchone()
    if not row or not check_password(password or "", row[3]):
        _fail(*keys)
        raise ValueError("Неверная почта или пароль")
    with _fails_lock:
        _fails.pop(keys[0], None)
    return _public(row)


def change_password(user_id: int, old: str, new: str) -> None:
    with store._lock, store._db() as con:
        row = con.execute("select password from users where id = ?", (user_id,)).fetchone()
    if not row or not check_password(old or "", row[0]):
        raise ValueError("Текущий пароль неверный")
    _check_new_password(new)
    with store._lock, store._db() as con:
        con.execute("update users set password = ? where id = ?", (hash_password(new), user_id))
        con.execute("delete from sessions where user_id = ?", (user_id,))  # выйти на других устройствах


# ------------------------------------------------------------------ sessions

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with store._lock, store._db() as con:
        con.execute("insert into sessions values (?, ?, ?, ?)", (_digest(token), user_id, _now(), expires))
        con.execute("delete from sessions where expires < ?", (_now(),))
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with store._lock, store._db() as con:
        row = con.execute("select u.id, u.email, u.name from sessions s join users u on u.id = s.user_id "
                          "where s.token = ? and s.expires > ?", (_digest(token), _now())).fetchone()
    return _public(row) if row else None


def end_session(token: str | None) -> None:
    if token:
        with store._lock, store._db() as con:
            con.execute("delete from sessions where token = ?", (_digest(token),))
