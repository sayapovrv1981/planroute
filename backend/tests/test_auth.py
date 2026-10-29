import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import auth, store


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.base = mock.patch.object(store, "BASE", Path(tempfile.mkdtemp()))
        self.base.start()
        auth._fails.clear()

    def tearDown(self):
        self.base.stop()

    def test_password_hash(self):
        h = auth.hash_password("пароль-123")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertNotIn("пароль", h)
        self.assertTrue(auth.check_password("пароль-123", h))
        self.assertFalse(auth.check_password("пароль-124", h))
        self.assertNotEqual(h, auth.hash_password("пароль-123"))  # соль своя каждый раз
        self.assertFalse(auth.check_password("x", "мусор"))

    def test_register_and_orphan_trips(self):
        # поездки, построенные до аккаунтов, — первому пользователю, дальше никому
        with store._db() as con:
            con.execute("insert into trips (id, data, created, updated) values ('old1', '{}', 'x', 'x')")
        first = auth.register("Owner@Mail.ru ", "secret-pass-1", "Руслан")
        self.assertEqual(first["email"], "owner@mail.ru")
        self.assertIsNotNone(store.get("old1", first["id"]))
        second = auth.register("guest@mail.ru", "secret-pass-2", "Гость")
        self.assertIsNone(store.get("old1", second["id"]))
        for email, pw, name in (("owner@mail.ru", "secret-pass-3", "Ещё"), ("не почта", "secret-pass-3", "А"),
                                ("a@b.ru", "short", "А"), ("a@b.ru", "secret-pass-3", " ")):
            with self.assertRaises(ValueError):
                auth.register(email, pw, name)

    def test_login_sessions_and_limits(self):
        u = auth.register("u@mail.ru", "secret-pass-1", "Юзер")
        self.assertEqual(auth.login("U@mail.ru", "secret-pass-1", "1.1.1.1")["id"], u["id"])
        token = auth.new_session(u["id"])
        self.assertEqual(auth.session_user(token)["email"], "u@mail.ru")
        with store._db() as con:  # в базе — только хеш токена
            self.assertIsNone(con.execute("select 1 from sessions where token = ?", (token,)).fetchone())
        auth.end_session(token)
        self.assertIsNone(auth.session_user(token))
        for _ in range(auth.MAX_FAILS):
            with self.assertRaises(ValueError):
                auth.login("u@mail.ru", "wrong", "2.2.2.2")
        with self.assertRaises(PermissionError):  # подбор — пауза даже с верным паролем
            auth.login("u@mail.ru", "secret-pass-1", "3.3.3.3")
        # смена пароля закрывает все сессии
        auth._fails.clear()
        t1 = auth.new_session(u["id"])
        with self.assertRaises(ValueError):
            auth.change_password(u["id"], "wrong", "new-secret-1")
        auth.change_password(u["id"], "secret-pass-1", "new-secret-1")
        self.assertIsNone(auth.session_user(t1))
        self.assertEqual(auth.login("u@mail.ru", "new-secret-1")["id"], u["id"])

    def test_password_reset(self):
        u = auth.register("u@mail.ru", "secret-pass-1", "Юзер")
        sent = []
        with mock.patch.object(auth.mailer, "send", lambda to, subject, text: sent.append((to, text)) or True):
            self.assertFalse(auth.request_reset("нет-такой@mail.ru"))  # чужой адрес — ответ тот же, письма нет
            self.assertTrue(auth.request_reset("U@mail.ru "))
            with self.assertRaises(ValueError):
                auth.request_reset("не почта")
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][0], "u@mail.ru")
            token = sent[0][1].split("/reset?token=")[1].split()[0]
            with store._db() as con:  # в базе — только хеш токена
                self.assertIsNone(con.execute("select 1 from resets where token = ?", (token,)).fetchone())
            # пока не использован — ссылка годна и говорит, чей это аккаунт
            self.assertEqual(auth.reset_user(token)["email"], "u@mail.ru")
            self.assertIsNone(auth.reset_user("мусор"))
            session = auth.new_session(u["id"])
            with self.assertRaises(ValueError):
                auth.reset_password(token, "short")
            auth.reset_password(token, "new-secret-1")
            self.assertIsNone(auth.session_user(session))  # прежние сессии закрыты
            self.assertEqual(auth.login("u@mail.ru", "new-secret-1")["id"], u["id"])
            self.assertIsNone(auth.reset_user(token))  # ссылка одноразовая
            with self.assertRaises(ValueError):
                auth.reset_password(token, "new-secret-2")
            # просроченная ссылка не работает
            auth.request_reset("u@mail.ru")
            old = sent[-1][1].split("/reset?token=")[1].split()[0]
            with store._db() as con:
                con.execute("update resets set expires = '2000-01-01T00:00:00+00:00' where used is null")
            self.assertIsNone(auth.reset_user(old))
            # не чаще RESET_MAX писем в час
            sent.clear()
            with store._db() as con:
                con.execute("delete from resets")
            for _ in range(auth.RESET_MAX):
                self.assertTrue(auth.request_reset("u@mail.ru"))
            self.assertFalse(auth.request_reset("u@mail.ru"))
            self.assertEqual(len(sent), auth.RESET_MAX)


if __name__ == "__main__":
    unittest.main()
