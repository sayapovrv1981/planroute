import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import unittest
from unittest import mock

from starlette.testclient import TestClient

from app import main, ostrovok

URL = "https://ostrovok.ru/hotel/russia/st._petersburg/mid10593104/helen_hotel_8/"
# фрагменты настоящей страницы отеля: og-теги и данные в разметке
PAGE = (
    '<meta property="og:title" content="Отель Helen 4* в Санкт-Петербурге 678 отзывов об отеле, цены и фото номеров">'
    '<meta property="og:image" content="https://cdn.ostrovok.ru/t/640x400/x.JPEG">'
    '{"policy":{"checkInTimeStart":{"hours":14,"minutes":0,"dayPeriod":null},'
    '"checkOutTimeEnd":{"hours":12,"minutes":0,"dayPeriod":null}},'
    '"facts":{"kind":"HOTEL","register":{"address":"190031, г Санкт-Петербург, ул Большая Морская, д. 28/13","kind":"RK_HOTEL"}},'
    '"address":"Большая Морская улица, д. 28/ 13, Санкт-Петербург","latitude":59.93388748168945,"longitude":30.313425064086914}'
)


class OstrovokTest(unittest.TestCase):
    def test_parse(self):
        m = ostrovok.parse(PAGE, URL)
        self.assertEqual(m["name"], "Отель Helen")
        self.assertEqual(m["address"], "Большая Морская улица, д. 28/ 13")  # не юридический адрес; город — приставкой в форме
        self.assertEqual((m["lat"], m["lon"]), (59.933887, 30.313425))
        self.assertEqual((m["check_in"], m["check_out"], m["kind"]), ("14:00", "12:00", "hotel"))
        apart = ostrovok.parse(PAGE.replace('"kind":"HOTEL"', '"kind":"APARTMENT"'), URL)
        self.assertEqual(apart["kind"], "apartment")
        self.assertEqual(ostrovok.find_url(f"вот {URL}?dates=20.11.2026-22.11.2026&guests=2"), URL)
        self.assertIsNone(ostrovok.find_url("https://spb.sutochno.ru/361449"))
        with self.assertRaises(ValueError):
            ostrovok.parse("<html></html>", URL)

    def test_preview_fills_manual_stay(self):
        client = TestClient(main.app)
        client.post("/api/auth/register", json={"email": "ostrovok@example.test", "password": "secret-123", "name": "Т"})
        with mock.patch.object(ostrovok, "fetch", return_value=PAGE):
            r = client.post("/api/stays/preview", json={"url": URL, "city": "Санкт-Петербург"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["manual"]["name"], "Отель Helen")
            r = client.post("/api/stays/preview", json={"url": URL, "city": "Казань"})
            self.assertEqual(r.status_code, 422)
            self.assertIn("не в городе Казань", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
