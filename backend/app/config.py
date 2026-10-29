"""Настройки сервера из переменных окружения (и backend/.env для разработки).

Здесь — то, что задаёт окружение: база, хранилище файлов, лимиты. Ключи внешних сервисов (YandexGPT, DaData,
Трипстер, SMTP…) читают их модули сами, список — в .env.example.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def load_env_file(path: Path = BACKEND / ".env") -> None:
    """backend/.env: строки КЛЮЧ=значение. Переменные окружения важнее."""
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file()

DEV_DB_PORT = 54329  # локальный PostgreSQL из `manage.py devdb start`


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path         # кеш Трипстера, файлы при STORAGE=local
    storage: str           # local | s3
    s3_endpoint: str
    s3_region: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_prefix: str
    max_file_mb: int       # один загруженный файл
    max_upload_mb: int     # весь запрос с файлами (форма билетов — до 10 файлов)
    max_json_kb: int       # запрос без файлов
    rate_limit: bool


def get() -> Settings:
    """Читаем окружение при каждом вызове: тесты меняют переменные на ходу, а сам разбор дешёвый."""
    e = os.environ.get
    return Settings(
        database_url=(e("DATABASE_URL") or f"postgresql+psycopg://postgres@127.0.0.1:{DEV_DB_PORT}/tripplan").strip(),
        data_dir=Path(e("SH_DATA_DIR") or BACKEND / "var"),
        storage=(e("STORAGE") or "local").strip().lower(),
        s3_endpoint=e("S3_ENDPOINT", "https://storage.yandexcloud.net").strip(),  # пустой — адрес AWS (для тестов)
        s3_region=(e("S3_REGION") or "ru-central1").strip(),
        s3_bucket=(e("S3_BUCKET") or "").strip(),
        s3_access_key=(e("S3_ACCESS_KEY") or "").strip(),
        s3_secret_key=(e("S3_SECRET_KEY") or "").strip(),
        s3_prefix=(e("S3_PREFIX") or "").strip().strip("/"),
        max_file_mb=_int("MAX_FILE_MB", 15),
        max_upload_mb=_int("MAX_UPLOAD_MB", 60),
        max_json_kb=_int("MAX_JSON_KB", 512),
        rate_limit=(e("RATE_LIMIT") or "on").strip().lower() not in ("0", "off", "false", "no"),
    )
