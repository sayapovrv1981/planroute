"""Сквозной тест API: настоящий uvicorn в отдельном потоке, запросы через urllib."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from unittest import mock

os.environ["SH_DATA_DIR"] = tempfile.mkdtemp(prefix="sh-test-")
# Ключи из backend/.env не должны превращать тесты в платные запросы к моделям (.env не перекрывает заданное)
for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "YANDEX_GPT_API_KEY", "YANDEX_FOLDER_ID",
           "TRIPSTER_PARTNER", "TRIPSTER_SECRET", "YANDEX_VISION_API_KEY"):
    os.environ[_k] = ""

import uvicorn  # noqa: E402

from app import mailer  # noqa: E402
from app.main import app  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="warning"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        cls.cookie = cls.signup(f"user-{uuid.uuid4().hex[:8]}@example.ru")

    @classmethod
    def signup(cls, email, password="secret-pass-1", name="Тест"):
        """Регистрация -> cookie сессии для запросов этого пользователя."""
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}/api/auth/register", method="POST",
                                     data=json.dumps({"email": email, "password": password, "name": name}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.headers["Set-Cookie"].split(";")[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    def call(self, method, path, body=None, headers=None, cookie=None):
        data = None
        headers = dict(headers or {})
        cookie = self.cookie if cookie is None else cookie  # "" — без входа
        if cookie:
            headers["Cookie"] = cookie
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif isinstance(body, bytes):
            data = body
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw.startswith(b"{") else raw)

    def multipart(self, fields, file=None):
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        if file:
            name, content = file
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                          f'Content-Type: application/pdf\r\n\r\n').encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    def test_full_flow(self):
        status, meta = self.call("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertTrue(meta["paces"])

        status, trip = self.call("POST", "/api/trips", {"wishes": "театр недорого", "start_date": "2026-10-27"})
        self.assertEqual(status, 201)
        tid = trip["id"]
        status, again = self.call("GET", f"/api/trips/{tid}")
        self.assertEqual(status, 200)
        self.assertEqual(again["days"][0]["items"][0]["title"], "Выход из дома")

        museum = next(i for d in trip["days"] for i in d["items"] if i["kind"] == "museum" and i["alternatives"])
        self.assertIn("name", museum["alternatives"][0])

        status, trip = self.call("PATCH", f"/api/trips/{tid}/items/{museum['id']}", {"duration": museum["duration"] + 15})
        self.assertEqual(status, 200)
        alt = museum["alternatives"][0]["id"]
        status, trip = self.call("PATCH", f"/api/trips/{tid}/items/{museum['id']}", {"replace_with": alt})
        self.assertEqual(status, 200)

        hotel = next(i for d in trip["days"] for i in d["items"] if i["kind"] == "hotel")
        body, headers = self.multipart({"sum": "24 600", "paid": "12300", "title": "Бронь № 1"},
                                       ("bron.pdf", b"%PDF-1.4 test"))
        status, trip = self.call("POST", f"/api/trips/{tid}/items/{hotel['id']}/doc", body, headers)
        self.assertEqual(status, 200, trip)
        h = next(i for d in trip["days"] for i in d["items"] if i["id"] == hotel["id"])
        self.assertEqual(h["doc"]["onsite"], 12300)
        self.assertEqual(trip["budget"]["paid"], 12300)
        self.assertEqual(len(h["doc"]["files"]), 1)
        status, raw = self.call("GET", h["doc"]["files"][0]["url"])
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"%PDF-1.4 test")

        status, trip = self.call("DELETE", f"/api/trips/{tid}/items/{hotel['id']}/doc")
        self.assertEqual(status, 200)
        self.assertEqual(trip["budget"]["paid"], 0)

        status, sug = self.call("GET", f"/api/trips/{tid}/days/1/suggest")
        self.assertEqual(status, 200)
        pid = sug["places"][0]["id"]
        after = trip["days"][1]["items"][1]["id"]
        status, trip = self.call("POST", f"/api/trips/{tid}/days/1/items", {"after": after, "place_id": pid})
        self.assertEqual(status, 200, trip)
        added = trip["days"][1]["items"][2]
        self.assertEqual(added["place_id"], pid)
        status, trip = self.call("DELETE", f"/api/trips/{tid}/items/{added['id']}")
        self.assertEqual(status, 200)
        self.assertNotIn(added["id"], [i["id"] for i in trip["days"][1]["items"]])

    def test_stays_and_booked_stay_link(self):
        from unittest import mock
        from urllib.parse import quote

        from app import sutochno
        from test_sutochno import PAGE

        status, body = self.call("GET", "/api/stays?city=" + quote("Казань") + "&start_date=2026-11-03&end_date=2026-11-05&adults=2")
        self.assertEqual(status, 200)
        self.assertEqual(body["nights"], 2)
        self.assertTrue(any(r["kind"] == "apartment" for r in body["stays"]))
        status, body = self.call("GET", "/api/stays?city=" + quote("Урюпинск") + "&start_date=2026-11-03")
        self.assertEqual(status, 422)

        trip_in = {"origin": "Москва", "city": "Санкт-Петербург", "start_date": "2026-11-10", "end_date": "2026-11-12",
                   "start_time": "09:00", "adults": 2, "stay_url": "https://spb.sutochno.ru/361449"}
        with mock.patch.object(sutochno, "load", return_value=sutochno.parse(PAGE)):
            status, trip = self.call("POST", "/api/trips", trip_in)
        self.assertEqual(status, 201)
        stays = {i["place_id"] for d in trip["days"] for i in d["items"] if i.get("stay_role") and i["place_id"]}
        self.assertEqual(len(stays), 1)
        self.assertTrue(stays.pop().startswith("sutochno"))
        # программа построена сразу от этой квартиры: её время заезда, без предупреждений
        for i in (i for d in trip["days"] for i in d["items"] if i.get("stay_role")):
            self.assertFalse([w for w in i["warnings"] if "Заселение" in w or "Выезд" in w], i["title"])
        self.assertEqual(trip["input"]["stay_url"], "https://spb.sutochno.ru/361449")
        with mock.patch.object(sutochno, "load", side_effect=ValueError("объявление не найдено")):
            status, body = self.call("POST", "/api/trips", trip_in)
        self.assertEqual(status, 422)
        self.assertIn("объявление не найдено", body["error"])

    def test_bookings_upload_into_trip(self):
        from unittest import mock

        from test_bookings import BALLET, TRAIN, tiny_pdf

        boundary = "----sh" + uuid.uuid4().hex
        parts = [("origin", None, "Москва".encode()), ("city", None, "Санкт-Петербург".encode()),
                 ("file", "ballet.pdf", tiny_pdf("Mariinsky 2026-11-03 19:00")), ("file", "photo.jpg", b"\xff\xd8\xff")]
        body = b""
        for name, filename, data in parts:
            disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
            body += f"--{boundary}\r\nContent-Disposition: {disp}\r\n\r\n".encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        with mock.patch.dict(os.environ, {"YANDEX_GPT_API_KEY": "k", "YANDEX_FOLDER_ID": "f"}), \
                mock.patch("app.wishes_ai.ask", return_value={"bookings": [BALLET, dict(TRAIN, start="09:00", end="13:00")]}):
            status, got = self.call("POST", "/api/bookings", body,
                                    {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(status, 200)
        self.assertEqual([b["role"] for b in got["bookings"]], ["event", "out"])
        self.assertIn("фото", got["errors"][0])
        f = got["bookings"][0]["file"]
        self.assertEqual(f["filename"], "ballet.pdf")
        # до создания поездки билет открывается из временной папки, с download — скачивается под своим именем
        status, raw = self.call("GET", f"/api/bookings/files/{f['token']}/{f['name']}?filename=ballet.pdf")
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b"%PDF"))
        status, _ = self.call("GET", f"/api/bookings/files/../{f['name']}")
        self.assertEqual(status, 404)

        trip_in = {"origin": "Москва", "city": "Санкт-Петербург", "start_date": "2026-11-03", "end_date": "2026-11-05",
                   "start_time": "09:00", "arrive_time": "13:00", "transport": "train", "adults": 2,
                   "bookings": got["bookings"]}
        status, trip = self.call("POST", "/api/trips", trip_in)
        self.assertEqual(status, 201)
        items = [i for d in trip["days"] for i in d["items"]]
        ballet = next(i for i in items if i["place_id"] == "event-0")
        self.assertEqual(ballet["fixed_start"], 19 * 60)
        self.assertEqual((ballet["doc"]["sum"], ballet["doc"]["paid"], ballet["doc"]["start"]), (9000, 9000, "19:00"))
        self.assertEqual(ballet["doc"]["files"][0]["filename"], "ballet.pdf")
        status, raw = self.call("GET", ballet["doc"]["files"][0]["url"])
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b"%PDF"))
        out = next(i for i in items if i["kind"] == "travel" and i["cost"]["kind"] == "ticket")
        self.assertEqual(out["doc"]["files"][0]["filename"], "ballet.pdf")  # тот же PDF — и билет на поезд
        # оплачено по билету — купить больше не предлагаем, расписание остаётся
        labels = [lk["label"] for lk in out["links"]]
        self.assertFalse([lb for lb in labels if lb.startswith("Купить")], labels)
        self.assertIn("Расписание на Яндексе", labels)
        self.assertEqual(ballet["excursions"], [])
        # в вводных — ссылка на файл поездки: билет откроется и из «Изменить вводные»
        saved = trip["input"]["bookings"][0]["file"]
        self.assertEqual(saved["trip"], trip["id"])
        status, raw = self.call("GET", f"/api/trips/{trip['id']}/files/{saved['name']}?download")
        self.assertEqual(status, 200)

    def test_form_stay_matches_program(self):
        """«Подберём сами по бюджету» в форме — то же жильё, что окажется в программе."""
        from urllib.parse import urlencode

        cases = [
            {"city": "Санкт-Петербург", "budget": 100000, "wishes": "театр недорого"},
            {"city": "Санкт-Петербург", "budget": 100000, "wishes": ""},
            {"city": "Санкт-Петербург", "budget": 60000, "transport": "train", "out_price": 9000, "back_price": 9000},
            {"city": "Казань", "budget": 200000, "transport": "plane", "return_transport": "train", "stay": "apartment"},
            {"city": "Калининград", "budget": 150000, "kids_ages": [5], "tags": ["cheap"]},
        ]
        for c in cases:
            trip_in = dict({"origin": "Москва", "start_date": "2026-11-03", "end_date": "2026-11-05", "start_time": "07:00",
                            "adults": 2, "kids_ages": [], "stay": "any", "transport": "auto", "wishes": "", "tags": []}, **c)
            q = {k: trip_in[k] for k in ("city", "origin", "start_date", "end_date", "adults", "budget", "stay", "wishes",
                                          "transport")}
            q.update(kids=",".join(map(str, trip_in["kids_ages"])), tags=",".join(trip_in["tags"]),
                     return_transport=c.get("return_transport", ""), out_price=c.get("out_price", 0),
                     back_price=c.get("back_price", 0))
            status, opts = self.call("GET", "/api/stays?" + urlencode(q))
            self.assertEqual(status, 200)
            status, trip = self.call("POST", "/api/trips", trip_in)
            self.assertEqual(status, 201, trip)
            stay = next(i["place_id"] for d in trip["days"] for i in d["items"] if i.get("stay_role") in ("checkin", "checkin_late"))
            self.assertEqual(opts["recommended"], stay, c)

    def test_accounts_and_ownership(self):
        # без входа API закрыт: и поездки, и платные подсказки
        for method, path in (("GET", "/api/trips"), ("GET", "/api/meta"), ("GET", "/api/address/suggest?q=Tverskaya"),
                             ("POST", "/api/trips")):
            status, body = self.call(method, path, {} if method == "POST" else None, cookie="")
            self.assertEqual(status, 401, path)
            self.assertIn("Войдите", body["error"])
        self.assertEqual(self.call("GET", "/api/health", cookie="")[0], 200)
        status, me = self.call("GET", "/api/auth/me")
        self.assertEqual((status, me["user"]["name"]), (200, "Тест"))

        status, trip = self.call("POST", "/api/trips", {"origin": "Москва", "city": "Санкт-Петербург",
                                                        "start_date": "2026-11-03", "end_date": "2026-11-04"})
        self.assertEqual(status, 201)
        tid = trip["id"]
        status, mine = self.call("GET", "/api/trips")
        self.assertIn(tid, [t["id"] for t in mine["trips"]])
        self.assertTrue(next(t for t in mine["trips"] if t["id"] == tid)["title"].startswith("Санкт-Петербург"))

        # чужой: не видит, не меняет, не удаляет, файлов не получает
        other = self.signup(f"other-{uuid.uuid4().hex[:8]}@example.ru")
        item = trip["days"][0]["items"][0]["id"]
        for method, path, body in (("GET", f"/api/trips/{tid}", None), ("DELETE", f"/api/trips/{tid}", None),
                                   ("PATCH", f"/api/trips/{tid}/items/{item}", {"duration": 60}),
                                   ("GET", f"/api/trips/{tid}/files/x.pdf", None)):
            self.assertEqual(self.call(method, path, body, cookie=other)[0], 404, path)
        self.assertNotIn(tid, [t["id"] for t in self.call("GET", "/api/trips", cookie=other)[1]["trips"]])

        # вход, неверный пароль, выход
        email = f"third-{uuid.uuid4().hex[:8]}@example.ru"
        self.signup(email)
        self.assertEqual(self.call("POST", "/api/auth/login", {"email": email, "password": "wrong-pass"}, cookie="")[0], 401)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/auth/login", method="POST",
                                     data=json.dumps({"email": email, "password": "secret-pass-1"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            set_cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        third = set_cookie.split(";")[0]
        self.assertEqual(self.call("GET", "/api/trips", cookie=third)[0], 200)
        self.call("POST", "/api/auth/logout", {}, cookie=third)
        self.assertEqual(self.call("GET", "/api/trips", cookie=third)[0], 401)

        # удалить свою
        self.assertEqual(self.call("DELETE", f"/api/trips/{tid}")[0], 200)
        self.assertEqual(self.call("GET", f"/api/trips/{tid}")[0], 404)

    def test_forgot_password(self):
        email = f"forgot-{uuid.uuid4().hex[:8]}@example.ru"
        self.signup(email)
        sent = []
        with mock.patch.object(mailer, "send", lambda to, subject, text: sent.append(text) or True):
            # без входа; на незнакомую почту ответ такой же, письма нет
            self.assertEqual(self.call("POST", "/api/auth/forgot", {"email": "no-one@example.ru"}, cookie="")[0], 200)
            self.assertEqual(sent, [])
            self.assertEqual(self.call("POST", "/api/auth/forgot", {"email": email}, cookie="")[0], 200)
            self.assertEqual(self.call("POST", "/api/auth/forgot", {"email": "не почта"}, cookie="")[0], 422)
        self.assertEqual(len(sent), 1)
        token = sent[0].split("/reset?token=")[1].split()[0]

        status, body = self.call("GET", f"/api/auth/reset?token={token}", cookie="")
        self.assertEqual((status, body["user"]["email"]), (200, email))
        self.assertEqual(self.call("GET", "/api/auth/reset?token=no-such-token", cookie="")[0], 404)
        self.assertEqual(self.call("POST", "/api/auth/reset", {"token": token, "password": "123"}, cookie="")[0], 422)

        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/auth/reset", method="POST",
                                     data=json.dumps({"token": token, "password": "brand-new-pass"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:  # после сброса сразу вошли
            fresh = r.headers["Set-Cookie"].split(";")[0]
        self.assertEqual(self.call("GET", "/api/trips", cookie=fresh)[0], 200)
        self.assertEqual(self.call("POST", "/api/auth/login", {"email": email, "password": "brand-new-pass"}, cookie="")[0], 200)
        self.assertEqual(self.call("POST", "/api/auth/login", {"email": email, "password": "secret-pass-1"}, cookie="")[0], 401)
        self.assertEqual(self.call("GET", f"/api/auth/reset?token={token}", cookie="")[0], 404)  # ссылка одноразовая

    def test_errors(self):
        status, body = self.call("POST", "/api/trips", {"city": "Урюпинск"})
        self.assertEqual(status, 422)
        self.assertIn("Урюпинск", body["error"])
        status, body = self.call("GET", "/api/trips/nope")
        self.assertEqual(status, 404)
        status, trip = self.call("POST", "/api/trips", {})
        item = trip["days"][0]["items"][0]
        body, headers = self.multipart({"sum": "100"}, ("virus.exe", b"x"))
        status, resp = self.call("POST", f"/api/trips/{trip['id']}/items/{item['id']}/doc", body, headers)
        self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
