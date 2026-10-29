"""Файлы документов: на диске (разработка) или в S3-совместимом хранилище (Yandex Object Storage, MinIO).

Ключ файла — «папка/имя», где папка — id поездки или pending-<user>-<token> для билетов из формы.
Какое хранилище — решает STORAGE в окружении (app/config.py). Права проверяет вызывающий код: отсюда
файлы отдаются только через API, прямых ссылок на бакет нет.
"""
from __future__ import annotations

import shutil
import threading
from functools import lru_cache
from pathlib import Path

from . import config


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, content: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_folder(self, folder: str) -> None:
        shutil.rmtree(self.root / folder, ignore_errors=True)

    def move(self, src: str, dst: str) -> bool:
        p = self._path(src)
        if not p.is_file():
            return False
        self._path(dst).parent.mkdir(parents=True, exist_ok=True)
        p.rename(self._path(dst))
        return True


class S3Storage:
    def __init__(self, s: config.Settings):
        import boto3  # только для STORAGE=s3: локально без него можно обойтись
        from botocore.config import Config

        if not (s.s3_bucket and s.s3_access_key and s.s3_secret_key):
            raise RuntimeError("STORAGE=s3: задайте S3_BUCKET, S3_ACCESS_KEY и S3_SECRET_KEY")
        self.bucket, self.prefix = s.s3_bucket, (s.s3_prefix + "/" if s.s3_prefix else "")
        self.client = boto3.client("s3", endpoint_url=s.s3_endpoint or None, region_name=s.s3_region,
                                   aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key,
                                   config=Config(retries={"max_attempts": 3}, connect_timeout=5, read_timeout=30))
        self._errors = self.client.exceptions

    def _key(self, key: str) -> str:
        return self.prefix + key

    def put(self, key: str, content: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=content)

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except self._errors.NoSuchKey:
            return None

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def delete_folder(self, folder: str) -> None:
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket,
                                                                      Prefix=self._key(folder.rstrip("/") + "/"))
        for page in pages:
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})

    def move(self, src: str, dst: str) -> bool:
        try:
            self.client.copy_object(Bucket=self.bucket, Key=self._key(dst),
                                    CopySource={"Bucket": self.bucket, "Key": self._key(src)})
        except self._errors.ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return False
            raise
        self.delete(src)
        return True


_lock = threading.Lock()


@lru_cache(maxsize=4)
def _make(s: config.Settings) -> LocalStorage | S3Storage:
    if s.storage == "s3":
        return S3Storage(s)
    if s.storage != "local":
        raise RuntimeError(f"STORAGE={s.storage}: подходит local или s3")
    return LocalStorage(s.data_dir / "uploads")


def get() -> LocalStorage | S3Storage:
    with _lock:
        return _make(config.get())
