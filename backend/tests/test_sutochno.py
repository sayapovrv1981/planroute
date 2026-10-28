import json
import os
import unittest
from unittest import mock

# экскурсии Трипстера из backend/.env не нужны — без сети и запросов к API
os.environ["TRIPSTER_PARTNER"] = os.environ["TRIPSTER_SECRET"] = ""

from app import planner, sutochno  # noqa: E402
from app.main import present  # noqa: E402

LD = {
    "@context": "https://schema.org", "@type": "VacationRental", "identifier": "361449",
    "name": "2-комнатная квартира посуточно, Санкт-Петербург, Невский проспект, 131, объявление 361449",
    "image": ["https://static.sutochno.ru/doc/files/objects/0/361/449/6a75edb4ce5e5.png",
              "https://static.sutochno.ru/doc/files/objects/0/361/449/6a75eda8c34a9.png"],
    "geo": {"@type": "GeoCoordinates", "latitude": "59.92656600000000", "longitude": "30.37499800000000"},
    "address": {"addressLocality": "Санкт-Петербург", "streetAddress": "Невский проспект, 131"},
    "containsPlace": {"occupancy": {"value": 5}, "numberOfBedrooms": 2, "bed": {"numberOfBeds": 3}},
    "offers": {"price": "7900.00", "priceCurrency": "RUB"},
    "aggregateRating": {"ratingValue": "9.7", "reviewCount": "95"},
}
PAGE = ('<html><head><meta property="og:url" content="https://spb.sutochno.ru/361449" />'
        f'<script type="application/ld+json">{json.dumps(LD, ensure_ascii=False)}</script></head>'
        '<body><h2>Правила дома</h2><div>Заезд</div><div>после 15:00</div><div>Отъезд</div><div>до 12:00</div>'
        '</body></html>')
BASE = {"origin": "Москва", "city": "Санкт-Петербург", "start_date": "2026-11-10", "start_time": "09:00",
        "days": 3, "transport": "auto", "adults": 2, "kids_ages": [], "budget": 100000, "pace": "balanced",
        "stay": "apartment"}


class SutochnoTest(unittest.TestCase):
    def test_find_url(self):
        self.assertEqual(sutochno.find_url("Смотри: https://spb.sutochno.ru/361449?occupied=a;b"),
                         "https://spb.sutochno.ru/361449?occupied=a;b")
        self.assertEqual(sutochno.page_url("https://m.sutochno.ru/361449?x=1"), "https://sutochno.ru/361449")
        with self.assertRaises(ValueError):
            sutochno.find_url("https://sutochno.ru.example.com/361449")

    def test_parse(self):
        p = sutochno.parse(PAGE)
        self.assertEqual(p["id"], "sutochno-361449")
        self.assertEqual(p["name"], "2-комнатная квартира, Невский проспект, 131")
        self.assertEqual((p["check_in"], p["check_out"]), ("15:00", "12:00"))
        self.assertEqual((p["price_night"], p["capacity"]), (7900, 5))
        self.assertEqual(p["url"], "https://spb.sutochno.ru/361449")
        self.assertIn("★ 9,7 (95 отзывов)", p["note"])
        self.assertEqual(sutochno.thumb(p["photos"][0]),
                         "https://static.sutochno.ru/doc/files/objects/0/361/449/small/6a75edb4ce5e5.png")

    def test_parse_without_schema_org(self):
        # на части страниц нет ld+json — берём данные карты (строка JS с двойным экранированием) и og-теги
        obj = {"objects": [{"id": "1012725", "lat": "59.93071900000000", "lng": "30.36504200000000",
                            "price": "6 400 <span class=\"rubl\">&#8381;</span>", "max_guests": "4 гостя",
                            "address": "Невский проспект, 124", "title": "Апартаменты",
                            "media": ["//static.sutochno.ru/doc/files/objects/1/12/725/medium/6265db5595000.jpg"]}],
               "location": "Россия, г. Санкт-Петербург"}
        js = json.dumps(obj).replace("\\", "\\\\").replace("/", "\\/")
        page = ('<meta property="og:title" content="Апартаменты посуточно, Санкт-Петербург, Невский проспект, 124, '
                'объявление 1012725" /><meta property="og:url" content="https://spb.sutochno.ru/1012725" />'
                f"<script>var OBJECT_MAP_DATA = '{js}';</script><div>Заезд после 14:00</div>")
        p = sutochno.parse(page)
        self.assertEqual((p["id"], p["name"], p["city"]), ("sutochno-1012725", "Апартаменты, Невский проспект, 124",
                                                           "Санкт-Петербург"))
        self.assertEqual((p["price_night"], p["capacity"], p["check_in"]), (6400, 4, "14:00"))
        self.assertNotIn("check_out", p)
        self.assertEqual(p["photos"], ["https://static.sutochno.ru/doc/files/objects/1/12/725/6265db5595000.jpg"])

    def test_parse_not_listing(self):
        with self.assertRaises(ValueError):
            sutochno.parse("<html><body>Поиск</body></html>")

    def test_booked_apartment(self):
        # своя бронь из формы: программа строится сразу от квартиры
        p = sutochno.parse(PAGE)
        trip = planner.plan_trip(dict(BASE, stay_id=p["id"]), {p["id"]: p})
        bound = [i for d in trip["days"] for i in d["items"] if i.get("place_id") == "sutochno-361449"]
        self.assertTrue(any(i["stay_role"] == "checkout" for i in bound))
        checkin = next(i for i in bound if i["stay_role"] in ("checkin", "checkin_late"))
        self.assertIn("Заселение с 15:00, выезд до 12:00", checkin["note"])
        self.assertNotIn("уточните", checkin["note"])
        self.assertEqual(checkin["cost"]["estimate"], 7900 * 2)
        self.assertEqual(checkin["alternatives"], [])
        self.assertEqual(trip["custom_places"], {p["id"]: p})

        with mock.patch.dict(os.environ, {"TP_MARKER": ""}):
            out = present("t1", trip)
        it = next(i for d in out["days"] for i in d["items"] if i["id"] == checkin["id"])
        self.assertIsNone(it["href_ad"])
        self.assertEqual(it["href"], "https://spb.sutochno.ru/361449?occupied=2026-11-10;2026-11-12&guests_adults=2")
        self.assertEqual(len(it["photos"]), 2)
        self.assertTrue(it["photo"].endswith("/small/6a75edb4ce5e5.png"))

    def test_tripster_link(self):
        trip = planner.plan_trip(dict(BASE))
        with mock.patch.dict(os.environ, {"TP_MARKER": ""}):
            out = present("t1", trip)
        its = [i for d in out["days"] for i in d["items"]]
        tripster = "https://experience.tripster.ru/experience/Saint_Petersburg/"
        for it in its:
            has = any(l["url"] == tripster for l in it["links"])
            self.assertEqual(has, it["kind"] in ("museum", "sight", "walk"), it["title"])
        self.assertTrue(any(i["kind"] == "museum" for i in its))

    def test_other_city(self):
        p = dict(sutochno.parse(PAGE), city="Казань")
        with self.assertRaises(ValueError):
            planner.plan_trip(dict(BASE, stay_id=p["id"]), {p["id"]: p})


if __name__ == "__main__":
    unittest.main()
