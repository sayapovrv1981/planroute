"""Письма пользователю: пока только восстановление пароля.

Отправка по SMTP, настройки — в backend/.env (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM).
Порт 465 — SSL сразу, 587 — STARTTLS. Для Яндекс 360: smtp.yandex.ru:465, пароль приложения из Яндекс ID.
Без настроек письмо не уходит, а пишется в журнал (`journalctl -u tripplan`) — так удобно проверять на этом сервере.
APP_URL — адрес, с которого пользователь открывает приложение: из него собираем ссылку в письме.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("svoim-hodom.mail")

TIMEOUT = 20
SENDER_NAME = "Своим ходом"


def app_url() -> str:
    return (os.environ.get("APP_URL") or "http://localhost:8000").rstrip("/")


def _settings() -> dict | None:
    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = os.environ.get("SMTP_PASSWORD") or ""
    if not host or not user or not password:
        return None
    return {"host": host, "port": int(os.environ.get("SMTP_PORT") or 465), "user": user, "password": password,
            "sender": (os.environ.get("SMTP_FROM") or user).strip()}


def send(to: str, subject: str, text: str) -> bool:
    """Отправить письмо. False — почта не настроена (письмо ушло в журнал) или SMTP отказал."""
    cfg = _settings()
    if not cfg:
        log.warning("SMTP не настроен, письмо для %s не отправлено:\n%s\n%s", to, subject, text)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, cfg["sender"]))
    msg["To"] = to
    msg.set_content(text)
    try:
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=TIMEOUT, context=ssl.create_default_context()) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=TIMEOUT) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        log.error("SMTP %s:%s — письмо для %s не ушло: %s", cfg["host"], cfg["port"], to, e)
        return False
    log.info("Письмо «%s» отправлено на %s", subject, to)
    return True


def send_password_reset(to: str, name: str, token: str, hours: int) -> bool:
    link = f"{app_url()}/reset?token={token}"
    text = (f"{name}, здравствуйте!\n\n"
            f"Вы попросили восстановить пароль в «Своим ходом». Ссылка, чтобы задать новый:\n{link}\n\n"
            f"Ссылка работает {hours} ч и только один раз. Если пароль вы не забывали — просто удалите письмо, "
            f"старый пароль продолжает работать.\n")
    return send(to, "Новый пароль в «Своим ходом»", text)
