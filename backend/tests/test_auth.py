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


if __name__ == "__main__":
    unittest.main()
