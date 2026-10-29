"""Инфраструктура этапа C.0: хранилище файлов, лимиты запросов, каталог в базе, перенос из SQLite."""
import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import dataclasses
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import boto3
from moto import mock_aws
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import manage
from app import auth, catalog, config, db, limits, storage, store


class StorageCases:
    def make(self):
        raise NotImplementedError

    def test_put_get_move_delete(self):
        s = self.make()
        s.put("t1/a.pdf", b"one")
        s.put("t1/b.pdf", b"two")
        s.put("t2/c.pdf", b"three")
        self.assertEqual(s.get("t1/a.pdf"), b"one")
        self.assertIsNone(s.get("t1/none.pdf"))
        self.assertTrue(s.move("t1/a.pdf", "t3/a.pdf"))
        self.assertFalse(s.move("t1/a.pdf", "t3/x.pdf"))  # уже перенесён
        self.assertEqual(s.get("t3/a.pdf"), b"one")
        self.assertIsNone(s.get("t1/a.pdf"))
        s.delete("t3/a.pdf")
        self.assertIsNone(s.get("t3/a.pdf"))
        s.delete_folder("t1")
        self.assertIsNone(s.get("t1/b.pdf"))
        self.assertEqual(s.get("t2/c.pdf"), b"three")  # соседняя папка цела


class LocalStorageTest(StorageCases, unittest.TestCase):
    def make(self):
        return storage.LocalStorage(Path(tempfile.mkdtemp()))


class S3StorageTest(StorageCases, unittest.TestCase):
    def setUp(self):
        self.aws = mock_aws()
        self.aws.start()
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="docs")

    def tearDown(self):
        self.aws.stop()

    def make(self):
        s = dataclasses.replace(config.get(), storage="s3", s3_endpoint="", s3_region="us-east-1", s3_bucket="docs",
                                s3_access_key="k", s3_secret_key="s", s3_prefix="trips")
        return storage.S3Storage(s)

    def test_prefix_and_settings(self):
        self.make().put("t1/a.pdf", b"x")
        keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="docs")["Contents"]]
        self.assertEqual(keys, ["trips/t1/a.pdf"])
        with self.assertRaises(RuntimeError):  # без ключей не стартуем молча
            storage.S3Storage(dataclasses.replace(config.get(), storage="s3", s3_bucket=""))

    def test_store_uses_s3(self):
        env = {"STORAGE": "s3", "S3_ENDPOINT": "", "S3_REGION": "us-east-1", "S3_BUCKET": "docs",
               "S3_ACCESS_KEY": "k", "S3_SECRET_KEY": "s", "S3_PREFIX": ""}
        with mock.patch.dict(os.environ, env):
            storage._make.cache_clear()
            folder = store.pending_dir(7, "a" * 16)
            name = store.save_file(folder, "Билет.PDF", b"%PDF")
            self.assertTrue(name.endswith(".pdf"))
            self.assertEqual(store.adopt_pending("a" * 16, name, "trip1", 7), name)
            self.assertEqual(store.read_file("trip1", name), b"%PDF")
            self.assertIsNone(store.read_file("trip1", "../" + name))
            store.delete_file("trip1", name)
            self.assertIsNone(store.read_file("trip1", name))
        storage._make.cache_clear()


def tiny_app(rules=None):
    async def ok(request):
        await request.body()
        return JSONResponse({"ok": True})

    from starlette.middleware import Middleware
    return Starlette(routes=[Route("/api/{p:path}", ok, methods=["GET", "POST"])],
                     middleware=[Middleware(limits.BodyLimit), Middleware(limits.RateLimit, rules=rules)])


class LimitsTest(unittest.TestCase):
    def test_body_limit(self):
        with mock.patch.dict(os.environ, {"MAX_JSON_KB": "1", "MAX_UPLOAD_MB": "1"}):
            c = TestClient(tiny_app([]))
            self.assertEqual(c.post("/api/x", content=b"x" * 1000).status_code, 200)
            r = c.post("/api/x", content=b"x" * 2000)
            self.assertEqual(r.status_code, 413)
            self.assertIn("1 КБ", r.json()["error"])
            # без Content-Length — считаем принятые байты
            r = c.post("/api/x", content=(b"x" * 500 for _ in range(5)))
            self.assertEqual(r.status_code, 413)
            # файлы — свой, больший лимит
            files = {"file": ("a.pdf", b"x" * 5000, "application/pdf")}
            self.assertEqual(c.post("/api/x", files=files).status_code, 200)
            files = {"file": ("a.pdf", b"x" * (2 * 1024 * 1024), "application/pdf")}
            self.assertEqual(c.post("/api/x", files=files).status_code, 413)

    def test_rate_limit(self):
        import re
        rules = [limits.Rule("t", re.compile(r"/api/t$"), ("POST",), 2, 60, by_ip=True)]
        c = TestClient(tiny_app(rules))
        self.assertEqual([c.post("/api/t").status_code for _ in range(3)], [200, 200, 429])
        r = c.post("/api/t")
        self.assertEqual(r.status_code, 429)
        self.assertGreater(int(r.headers["Retry-After"]), 0)
        self.assertEqual(c.get("/api/t").status_code, 200)  # правило только для POST
        self.assertEqual(c.post("/api/other").status_code, 200)
        with mock.patch.dict(os.environ, {"RATE_LIMIT": "off"}):
            self.assertEqual(c.post("/api/t").status_code, 200)

    def test_rules_cover_paid_endpoints(self):
        paths = {r.name: r for r in limits.RULES}
        self.assertTrue(paths["bookings"].path.match("/api/bookings"))
        self.assertTrue(paths["fetch"].path.match("/api/trips/abc/items/i1/place"))
        self.assertTrue(paths["auth"].path.match("/api/auth/register"))
        self.assertFalse(paths["auth"].path.match("/api/auth/me"))


class CatalogImportTest(unittest.TestCase):
    def tearDown(self):
        catalog.import_json()  # обратно настоящий каталог для остальных тестов

    def test_import_replaces_and_keeps_order(self):
        tmp = Path(tempfile.mkdtemp())
        data = {"city": "Тестов", "aliases": ["Тест"], "center": [1, 2],
                "places": [{"id": "b", "name": "Второе"}, {"id": "a", "name": "Первое", "hours": {"z": 1, "a": 2}}]}
        (tmp / "test_places.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(catalog.import_json(tmp), {"test_places.json": 2})
        self.assertEqual(catalog.cities(), ["Тестов"])  # остальных городов в папке нет — удалены
        got = catalog.load(catalog.normalize_city("тест"))
        self.assertEqual([p["id"] for p in got["places"]], ["b", "a"])
        self.assertEqual(list(got["by_id"]["a"]["hours"]), ["z", "a"])  # порядок ключей как в файле
        data["places"].append({"id": "a", "name": "Дубль"})
        (tmp / "test_places.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            catalog.import_json(tmp)
        self.assertEqual(len(catalog.load("test_places.json")["places"]), 2)  # ошибка — каталог прежний


class SqliteToPgTest(unittest.TestCase):
    def setUp(self):
        testdb.reset()

    def test_move(self):
        path = Path(tempfile.mkdtemp()) / "trips.sqlite3"
        con = sqlite3.connect(path)
        con.execute("create table trips (id text primary key, data text not null, created text, updated text, user_id integer)")
        con.execute("create table users (id integer primary key, email text unique not null, name text not null, "
                    "password text not null, created text not null)")
        con.execute("create table sessions (token text primary key, user_id integer not null, created text not null, "
                    "expires text not null)")
        pw = auth.hash_password("secret-pass-1")
        con.execute("insert into users values (5, 'a@mail.ru', 'Аня', ?, '2026-10-01T10:00:00+00:00')", (pw,))
        con.execute("insert into sessions values ('live', 5, '2026-10-01T10:00:00+00:00', '2999-01-01T00:00:00+00:00')")
        con.execute("insert into sessions values ('old', 5, '2026-10-01T10:00:00+00:00', '2000-01-01T00:00:00+00:00')")
        trip = {"summary": {"title": "Питер"}, "input": {"start_date": "2026-11-01"}, "days": []}
        con.execute("insert into trips values ('t1', ?, '2026-10-02T10:00:00+00:00', '2026-10-03T10:00:00+00:00', 5)",
                    (json.dumps(trip, ensure_ascii=False),))
        con.execute("insert into trips values ('t0', '{}', 'x', 'x', null)")  # из самого первого прототипа
        con.commit()
        con.close()
        manage.sqlite_to_pg(str(path))
        self.assertEqual(auth.login("a@mail.ru", "secret-pass-1")["id"], 5)
        self.assertEqual(store.get("t1", 5), trip)
        self.assertEqual(store.list_trips(5)[0]["start_date"], "2026-11-01")
        with db.tx() as c:
            self.assertEqual(db.q(c, "select token from sessions").scalars().all(), ["live"])
            self.assertIsNone(db.q(c, "select user_id from trips where id = 't0'").scalar())
        # новый пользователь — с id после перенесённых, без конфликта
        self.assertEqual(auth.register("b@mail.ru", "secret-pass-2", "Боря")["id"], 6)
        with self.assertRaises(SystemExit):  # второй раз в непустую базу — нельзя
            manage.sqlite_to_pg(str(path))


if __name__ == "__main__":
    unittest.main()
