"""Общий HTTPS для запросов к внешним сервисам (Яндекс.Расписания, DaData, Photon).

Python с python.org на macOS не видит системные сертификаты, и любой HTTPS-запрос падает
с CERTIFICATE_VERIFY_FAILED. Тогда берём сертификаты из certifi или из /etc/ssl/cert.pem (есть в macOS).
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) and not os.environ.get("SH_FORCE_CA"):
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for path in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem"):
        if os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    return ctx


def get_json(req: urllib.request.Request, timeout: float) -> dict:
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))
