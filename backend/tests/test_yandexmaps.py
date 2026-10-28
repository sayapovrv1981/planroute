import html
import json
import unittest
from unittest import mock

from app import planner, yandexmaps

DAY = [{"from": {"hours": 11, "minutes": 0}, "to": {"hours": 23, "minutes": 0}}]
LATE = [{"from": {"hours": 11, "minutes": 0}, "to": {"hours": 24, "minutes": 0}}]
CARD = {"id": "1191221375", "title": "Татарская усадьба", "seoname": "tatarskaya_usadba",
        "address": "Казань, ул. Шигабутдина Марджани, 8", "coordinates": [49.117177, 55.780888],
        "categories": [{"name": "ресторан"}, {"name": "кейтеринг"}], "status": "open",
        "ratingData": {"ratingValue": 4.800000190734863, "reviewCount": 4493},
        "features": [{"id": "average_bill2", "value": "1200–1400 ₽", "type": "text"}],
        "workingTime": [DAY, None, DAY, DAY, DAY, LATE, LATE]}  # с воскресенья; пн — выходной


def page(card, title="Татарская усадьба, ресторан — Яндекс Карты"):
    state = {"stack": [{"results": {"items": [card]}}], "other": {"coordinates": [1, 2], "name": "метро"}}
    return (f"<html><head><title>{title}</title><meta property=\"og:image\" content=\"https://avatars/x.jpg\"></head>"
            f"<script type=\"application/json\" class=\"state-view\">{html.escape(json.dumps(state, ensure_ascii=False))}</script>")


class YandexMapsTest(unittest.TestCase):
    def test_parse(self):
        p = yandexmaps.parse(page(CARD))
        self.assertEqual((p["id"], p["name"], p["category"], p["source"]), ("ymaps-1191221375", "Татарская усадьба", "food", "yandex"))
        self.assertEqual((p["lat"], p["lon"], p["avg_check"], p["rating"]), (55.780888, 49.117177, 1300, 4.8))
        # пн выходной, пт и сб до полуночи
        self.assertEqual(p["hours"], {"open": "11:00", "close": "23:00", "closed": [0], "late": {"4": "23:59", "5": "23:59"}})
        self.assertEqual(p["note"], "Ресторан, кейтеринг · Казань, ул. Шигабутдина Марджани, 8 · ★ 4,8 на Яндекс Картах (4493)")
        self.assertEqual(p["url"], "https://yandex.ru/maps/org/tatarskaya_usadba/1191221375/")
        self.assertEqual(p["photos"], ["https://avatars/x.jpg"])

    def test_closed_and_not_a_card(self):
        with self.assertRaises(ValueError) as e:
            yandexmaps.parse(page(dict(CARD, status="permanent-closed")))
        self.assertIn("больше не работает", str(e.exception))
        with self.assertRaises(ValueError):
            yandexmaps.parse(page(CARD, title="Больше не работает: Татарская усадьба"))
        with self.assertRaises(ValueError):
            yandexmaps.parse("<html>капча</html>")

    def test_urls(self):
        self.assertEqual(yandexmaps.find_url("Татарская усадьба https://yandex.ru/maps/org/tatarskaya_usadba/1191221375/ "),
                         "https://yandex.ru/maps/org/tatarskaya_usadba/1191221375/")
        self.assertEqual(yandexmaps.org_id("https://yandex.ru/maps/43/kazan/?ll=49.1,55.7&oid=1191221375&z=17"), "1191221375")
        with self.assertRaises(ValueError):
            yandexmaps.find_url("https://2gis.ru/kazan/firm/1")
        with mock.patch.object(yandexmaps, "fetch", return_value=(page(CARD), "https://yandex.ru/maps/org/x/1191221375/")) as f:
            yandexmaps.load("https://yandex.ru/maps/org/tatarskaya_usadba/1191221375/?ll=1,2")
        f.assert_called_once_with("https://yandex.ru/maps/org/1191221375/")

    def test_into_program(self):
        trip = planner.plan_trip({"origin": "Москва", "city": "Казань", "start_date": "2026-11-03", "start_time": "07:00",
                                  "return_time": "19:00", "days": 3, "transport": "plane", "adults": 2, "kids_ages": [8]})
        lunch = next(i for d in trip["days"] for i in d["items"] if i.get("meal") == "lunch")
        p = yandexmaps.parse(page(CARD))
        planner.replace_with_custom(trip, lunch["id"], p)
        self.assertTrue(lunch["title"].startswith("Обед · Татарская усадьба"))
        self.assertEqual(lunch["cost"]["estimate"], int(round(1300 * (2 + 0.6), -1)))
        self.assertEqual(lunch["links"], [{"label": "Яндекс Карты", "url": p["url"]}])
        self.assertIn(p["id"], trip["custom_places"])
        museum = next(i for d in trip["days"] for i in d["items"] if i["kind"] == "museum")
        with self.assertRaises(ValueError):
            planner.replace_with_custom(trip, museum["id"], p)  # кафе — только вместо еды
        with self.assertRaises(ValueError):  # Москва — не Казань
            planner.replace_with_custom(trip, lunch["id"], dict(p, id="ymaps-2", lat=55.75, lon=37.6))


if __name__ == "__main__":
    unittest.main()
