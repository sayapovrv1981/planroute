"""Аккаунты: регистрация и вход по почте и паролю, сессии в cookie.

Пароль храним только хешем scrypt (стандартная библиотека) с солью. Сессия — случайный токен в cookie
HttpOnly + SameSite=Lax; в базе лежит sha256 от него, так что утёкшая база не даёт войти чужими сессиями.
Подбор пароля: после MAX_FAILS неудачных попыток за FAIL_WINDOW по почте или по адресу — пауза.
Забыли пароль: одноразовый токен на RESET_HOURS в письме (`mailer.py`), в базе — только sha256 от него.
Поездки, созданные до появления аккаунтов (без владельца), достаются первому зарегистрированному.
Дальше по плану — вход через Яндекс ID и VK ID.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db, mailer

COOKIE = "sh_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
MAX_FAILS = 5
FAIL_WINDOW = 15 * 60
RESET_HOURS = 2
RESET_MAX = 3  # запросов на сброс по одной почте за RESET_WINDOW
RESET_WINDOW = 60 * 60
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

def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    password = hash_password(password)  # scrypt — до транзакции, чтобы не держать блокировку
    with db.tx() as con:
        # две регистрации одновременно: иначе обе сочтут себя первыми или пройдут проверку почты
        db.q(con, "lock table users in share row exclusive mode")
        if db.q(con, "select 1 from users where email = :email", email=email).first():
            raise ValueError("Такая почта уже зарегистрирована — войдите")
        first = db.q(con, "select count(*) from users").scalar() == 0
        uid = db.q(con, "insert into users (email, name, password, created) values (:email, :name, :pw, :now) "
                        "returning id", email=email, name=name, pw=password, now=_now()).scalar()
        if first:  # поездки, построенные до аккаунтов, — первому пользователю (владельцу прототипа)
            db.q(con, "update trips set user_id = :uid where user_id is null", uid=uid)
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
    with db.tx() as con:
        row = db.q(con, "select id, email, name, password from users where email = :email", email=email).first()
    if not row or not check_password(password or "", row[3]):
        _fail(*keys)
        raise ValueError("Неверная почта или пароль")
    with _fails_lock:
        _fails.pop(keys[0], None)
    return _public(row)


def change_password(user_id: int, old: str, new: str) -> None:
    with db.tx() as con:
        row = db.q(con, "select password from users where id = :uid", uid=user_id).first()
    if not row or not check_password(old or "", row[0]):
        raise ValueError("Текущий пароль неверный")
    _check_new_password(new)
    password = hash_password(new)
    with db.tx() as con:
        db.q(con, "update users set password = :pw where id = :uid", pw=password, uid=user_id)
        db.q(con, "delete from sessions where user_id = :uid", uid=user_id)  # выйти на других устройствах


# ------------------------------------------------------------------ sessions

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with db.tx() as con:
        db.q(con, "insert into sessions (token, user_id, created, expires) values (:t, :uid, :now, :exp)",
             t=_digest(token), uid=user_id, now=now, exp=now + timedelta(days=SESSION_DAYS))
        db.q(con, "delete from sessions where expires < :now", now=now)
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with db.tx() as con:
        row = db.q(con, "select u.id, u.email, u.name from sessions s join users u on u.id = s.user_id "
                        "where s.token = :t and s.expires > :now", t=_digest(token), now=_now()).first()
    return _public(row) if row else None


def end_session(token: str | None) -> None:
    if token:
        with db.tx() as con:
            db.q(con, "delete from sessions where token = :t", t=_digest(token))


# ------------------------------------------------------------------ забыли пароль

def request_reset(email: str) -> bool:
    """Послать письмо со ссылкой на новый пароль. False — такой почты нет или слишком часто просят.

    Наружу разницу не показываем: иначе по ответу можно узнать, кто зарегистрирован.
    """
    email = norm_email(email)
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("Проверьте адрес почты")
    with db.tx() as con:
        row = db.q(con, "select id, email, name from users where email = :email", email=email).first()
        if not row:
            return False
        now = _now()
        since = now - timedelta(seconds=RESET_WINDOW)
        recent = db.q(con, "select count(*) from resets where user_id = :uid and created > :since",
                      uid=row[0], since=since).scalar()
        if recent >= RESET_MAX:
            return False
        token = secrets.token_urlsafe(32)
        db.q(con, "insert into resets (token, user_id, created, expires, used) values (:t, :uid, :now, :exp, null)",
             t=_digest(token), uid=row[0], now=now, exp=now + timedelta(hours=RESET_HOURS))
        db.q(con, "delete from resets where expires < :since", since=since)  # старые — убираем
    mailer.send_password_reset(row[1], row[2], token, RESET_HOURS)
    return True


def reset_user(token: str) -> dict | None:
    """Чей это токен сброса, если он ещё годен."""
    if not token:
        return None
    with db.tx() as con:
        row = db.q(con, "select u.id, u.email, u.name from resets r join users u on u.id = r.user_id "
                        "where r.token = :t and r.used is null and r.expires > :now", t=_digest(token), now=_now()).first()
    return _public(row) if row else None


def reset_password(token: str, new: str) -> dict:
    """Задать новый пароль по токену из письма; все прежние сессии закрываются."""
    user = reset_user(token)
    if not user:
        raise ValueError("Ссылка устарела — запросите письмо заново")
    _check_new_password(new)
    password = hash_password(new)
    with db.tx() as con:
        # used is null — в условии: две отправки одной ссылки одновременно не сменят пароль дважды
        if not db.q(con, "update resets set used = :now where token = :t and used is null",
                    now=_now(), t=_digest(token)).rowcount:
            raise ValueError("Ссылка устарела — запросите письмо заново")
        db.q(con, "update users set password = :pw where id = :uid", pw=password, uid=user["id"])
        db.q(con, "delete from resets where user_id = :uid and used is null", uid=user["id"])  # прочие письма — недействительны
        db.q(con, "delete from sessions where user_id = :uid", uid=user["id"])
    with _fails_lock:
        _fails.pop(f"email:{user['email']}", None)  # пароль сменили — пауза за подбор больше не нужна
    return user
