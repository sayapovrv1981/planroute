import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from app import catalog, store, tripster


def exp(i, title, tagline="", minutes=90, rating=4.8, reviews=10, unit="за одного", fmt="experience", kids=True):
    return {"id": i, "title": title, "tagline": tagline, "url": f"https://experience.tripster.ru/experience/{i}/",
            "duration": minutes / 60, "rating": rating, "review_count": reviews, "format": fmt, "child_friendly": kids,
            "status": "active", "is_visible": True, "photos": [{"thumbnail": f"https://img/{i}.jpg"}],
            "price": {"value": 2000.0, "unit_string": unit, **({} if unit else {"per_group": {"value": 2000.0}})}}


PAGES = {
    1: {"next": "https://experience.tripster.ru/PAGE2", "results": [exp(1, "Казанский кремль за 2 часа", rating=5.0, reviews=200),
                                     exp(2, "Обзорная по Казани", "с посещением кремля", unit=""),
                                     exp(3, "Свияжск на теплоходе", minutes=480)]},
    2: {"next": None, "results": [exp(4, "Кремль и Кул-Шариф", rating=4.5, reviews=5),
                                  exp(5, "Тур по Татарстану на 3 дня", "кремль", minutes=4320, fmt="tour"),
                                  exp(6, "Кремль ночью", kids=False, rating=4.9, reviews=300)]},
}


class FakeApi:
    def __init__(self):
        self.calls, self.tokens = [], 0

    def __call__(self, req):
        url = req.full_url
        self.calls.append(url)
        if url.endswith("/api/auth/obtain_token/partner/"):
            body = json.loads(req.data)
            assert body == {"partner": "planroute", "secret": "s3cret"}, body
            self.tokens += 1
            return {"token": f"t{self.tokens}"}
        if req.get_header("Authorization") != f"Bearer t{self.tokens}":
            raise urllib.error.HTTPError(url, 401, "expired", {}, None)
        if "/cities/" in url:
            return {"results": [{"id": 197, "name_ru": "Казань"}]}
        if "/experiences/" in url:
            return PAGES[1]
        if url.endswith("/PAGE2"):
            return PAGES[2]
        if url.endswith("/api/partners/planroute/"):
            return {"name": "planroute", "ad_info": {"erir_token": "2Vtzq", "about": "ООО «Рога», ИНН 1"}}
        raise AssertionError(url)


@mock.patch.dict(os.environ, {"TRIPSTER_PARTNER": "planroute", "TRIPSTER_SECRET": "s3cret"})
class TripsterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = mock.patch.object(store, "BASE", __import__("pathlib").Path(tempfile.mkdtemp()))
        self.tmp.start()
        tripster._cache.clear()
        tripster._token = None
        self.api = FakeApi()
        self.http = mock.patch.object(tripster, "_http", self.api)
        self.http.start()
        self.kremlin = catalog.load(catalog.normalize_city("Казань"))["by_id"]["kremlin"]

    def tearDown(self):
        self.http.stop()
        self.tmp.stop()

    def test_catalog_and_matching(self):
        data = tripster.city_data("Казань", wait=True)
        self.assertEqual(data["city_id"], 197)
        self.assertEqual(len(data["items"]), 6)  # обе страницы
        got = tripster.for_place(self.kremlin, "Казань")
        # больше совпавших слов — выше; многодневный тур не предлагаем
        self.assertEqual([x["id"] for x in got], [4, 6, 1])
        self.assertNotIn(5, [x["id"] for x in tripster.for_place(self.kremlin, "Казань", n=10)])
        self.assertNotIn(6, [x["id"] for x in tripster.for_place(self.kremlin, "Казань", kids=True, n=10)])
        x = next(g for g in tripster.for_place(self.kremlin, "Казань", n=10) if g["id"] == 1)
        self.assertEqual(x["meta"], "1 ч 30 мин · 2 000 ₽ за одного · ★ 5,0 · 200 отзывов")
        self.assertEqual(x["ad"], "Реклама · ООО «Рога», ИНН 1 · erid: 2Vtzq")
        self.assertIn("exp_partner=planroute&utm_source=planroute&utm_campaign=affiliates&utm_medium=api", x["url"])
        self.assertIn("erid=2Vtzq", x["url"])
        group = next(g for g in tripster.for_place(self.kremlin, "Казань", n=10) if g["id"] == 2)
        self.assertIn("2 000 ₽ за экскурсию", group["meta"])

    def test_cache_and_token_refresh(self):
        tripster.city_data("Казань", wait=True)
        n = len(self.api.calls)
        tripster._cache.clear()  # из памяти пропало — читаем с диска, без запросов
        self.assertEqual(len(tripster.city_data("Казань")["items"]), 6)
        self.assertEqual(len(self.api.calls), n)
        # токен истёк — получаем новый и повторяем запрос
        tripster._token = "old"
        self.assertEqual(tripster.api_get("https://experience.tripster.ru/api/partners/planroute/")["name"], "planroute")
        self.assertEqual(self.api.tokens, 2)

    def test_excursion_into_program(self):
        from app import main, planner

        tripster.city_data("Казань", wait=True)
        trip = planner.plan_trip({"origin": "Москва", "city": "Казань", "start_date": "2026-11-03", "start_time": "07:00",
                                  "return_time": "19:00", "days": 3, "transport": "plane", "adults": 2, "kids_ages": [9]})
        it = next(i for d in trip["days"] for i in d["items"] if i.get("place_id") == "kremlin")
        out = main.present("t", trip)
        alts = next(i for d in out["days"] for i in d["items"] if i["id"] == it["id"])["alternatives"]
        self.assertEqual(alts[0]["id"], "tripster-4")  # экскурсии — первыми в «Заменить»
        # за одного: на всех троих; цена за экскурсию — один раз на группу
        trip = main.put_excursion(trip, it["id"], "tripster-1")
        self.assertEqual((it["kind"], it["duration"], it["cost"]["estimate"]), ("excursion", 90, 2000 * 3))
        self.assertIn("kremlin", it["alternatives"])
        self.assertIn("tripster-1", trip["custom_places"])
        trip = main.put_excursion(trip, it["id"], "tripster-2")
        self.assertEqual(it["cost"]["estimate"], 2000)
        link = main.present("t", trip)["days"]
        link = next(i for d in link for i in d["items"] if i["id"] == it["id"])["links"][0]
        self.assertEqual(link["ad"], "Реклама · ООО «Рога», ИНН 1 · erid: 2Vtzq")
        self.assertIn("exp_partner=planroute", link["url"])
        with self.assertRaises(ValueError):
            main.put_excursion(trip, it["id"], "tripster-999")  # сняли с продажи
        hotel = next(i for d in trip["days"] for i in d["items"] if i.get("stay_role") == "checkin")
        with self.assertRaises(ValueError):
            main.put_excursion(trip, hotel["id"], "tripster-1")

    def test_keywords(self):
        self.assertEqual(tripster.keywords("Остров-град Свияжск", "Казань"), ["свияж"])
        self.assertEqual(tripster.keywords("Прогулка по улице Баумана", "Казань"), ["баума"])
        self.assertNotIn("казан", "".join(tripster.keywords(self.kremlin["name"], "Казань")))

    def test_disabled_without_keys(self):
        with mock.patch.dict(os.environ, {"TRIPSTER_SECRET": ""}):
            self.assertFalse(tripster.enabled())
            self.assertEqual(tripster.for_place(self.kremlin, "Казань"), [])
        self.assertEqual(self.api.calls, [])

    def test_api_errors_keep_old_catalog(self):
        tripster.city_data("Казань", wait=True)
        tripster._cache["planroute-Казань"] = (0, tripster._cache["planroute-Казань"][1])  # устарел
        with mock.patch.object(tripster, "_http", side_effect=OSError("нет сети")):
            data = tripster.city_data("Казань", wait=True)
        self.assertEqual(len(data["items"]), 6)


if __name__ == "__main__":
    unittest.main()
