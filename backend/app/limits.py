"""Ограничения на запросы: размер тела и частота.

Размер: запрос с файлами — до MAX_UPLOAD_MB, остальные — до MAX_JSON_KB (app/config.py). Проверяем и
Content-Length, и фактически принятые байты: заголовок можно не прислать или прислать неверный.

Частота: окно фиксированной длины на ключ «правило + пользователь (или адрес, если не вошёл)».
Счётчики в памяти процесса — сервер запускаем одним процессом uvicorn; если процессов станет несколько,
счётчики надо перенести в общее хранилище (Redis) или ограничивать в nginx.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from starlette.responses import JSONResponse

from . import config


class TooLarge(Exception):
    pass


def _too_large(limit: int) -> JSONResponse:
    mb = limit / 1024 / 1024
    size = f"{mb:.0f} МБ" if mb >= 1 else f"{limit // 1024} КБ"
    return JSONResponse({"error": f"Слишком большой запрос: не больше {size}"}, status_code=413)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        s = config.get()
        headers = dict(scope.get("headers") or [])
        multipart = headers.get(b"content-type", b"").startswith(b"multipart/form-data")
        limit = s.max_upload_mb * 1024 * 1024 if multipart else s.max_json_kb * 1024
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        if declared > limit:
            await _too_large(limit)(scope, receive, send)
            return
        got = 0
        started = False

        async def counted():
            nonlocal got
            message = await receive()
            if message["type"] == "http.request":
                got += len(message.get("body", b""))
                if got > limit:
                    raise TooLarge()
            return message

        async def watched(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except TooLarge:
            if started:
                raise
            await _too_large(limit)(scope, receive, send)


@dataclass(frozen=True)
class Rule:
    name: str
    path: re.Pattern
    methods: tuple[str, ...]
    limit: int
    window: int  # секунды
    by_ip: bool = False  # до входа пользователя ещё нет — считаем по адресу


RULES = [
    # подбор пароля дополнительно ограничен в auth.login; здесь — массовые регистрации и письма
    Rule("auth", re.compile(r"/api/auth/(register|login|forgot|reset)$"), ("POST",), 20, 600, by_ip=True),
    # платные и внешние сервисы: распознавание билетов, модель для пожеланий, страницы Суточно и Карт
    Rule("bookings", re.compile(r"/api/bookings$"), ("POST",), 30, 600),
    Rule("create", re.compile(r"/api/trips$"), ("POST",), 30, 600),
    Rule("fetch", re.compile(r"/api/(stays/preview|trips/[^/]+/items/[^/]+/place)$"), ("POST",), 30, 600),
    Rule("suggest", re.compile(r"/api/(address/suggest|schedule)$"), ("GET",), 120, 60),
    Rule("api", re.compile(r"/api/"), ("GET", "POST", "PUT", "PATCH", "DELETE"), 600, 60),
]


class RateLimit:
    def __init__(self, app, rules: list[Rule] | None = None):
        self.app = app
        self.rules = RULES if rules is None else rules
        self.hits: dict[tuple[str, str], tuple[float, int]] = {}  # ключ -> (начало окна, сколько запросов)
        self.cleaned = time.monotonic()

    def _check(self, scope) -> tuple[Rule, float] | None:
        """Первое превышенное правило и через сколько секунд можно снова; None — пропускаем."""
        now = time.monotonic()
        if now - self.cleaned > 600:  # старые окна — из памяти
            longest = max((r.window for r in self.rules), default=0)
            self.hits = {k: v for k, v in self.hits.items() if now - v[0] < longest}
            self.cleaned = now
        user = (scope.get("state") or {}).get("user")
        ip = (scope.get("client") or ("?",))[0]
        over = None
        for r in self.rules:
            if scope["method"] not in r.methods or not r.path.match(scope["path"]):
                continue
            key = (r.name, f"ip:{ip}" if r.by_ip or not user else f"user:{user['id']}")
            start, n = self.hits.get(key, (now, 0))
            if now - start >= r.window:
                start, n = now, 0
            self.hits[key] = (start, n + 1)
            if n + 1 > r.limit and over is None:
                over = (r, r.window - (now - start))
        return over

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and config.get().rate_limit:
            over = self._check(scope)
            if over:
                _, wait = over
                minutes = max(1, round(wait / 60))
                resp = JSONResponse({"error": f"Слишком много запросов — попробуйте через {minutes} мин"},
                                    status_code=429, headers={"Retry-After": str(max(1, int(wait)))})
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
