"""Расписание: разбор ответа API Яндекс.Расписаний, поиск по номеру, работа без ключа."""
import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import os
import unittest
from unittest import mock

from app import schedule

SAMPLE = {"segments": [
    {"departure": "2026-10-27T13:30:00+03:00", "arrival": "2026-10-27T17:25:00+03:00", "duration": 14100.0,
     "from": {"title": "Москва (Ленинградский вокзал)"}, "to": {"title": "Санкт-Петербург (Московский вокзал)"},
     "thread": {"number": "758А", "title": "Москва — Санкт-Петербург", "carrier": {"title": "ФПК"}},
     "tickets_info": {"places": [{"price": {"whole": 5200, "cents": 0}}, {"price": {"whole": 4100, "cents": 0}}]}},
    {"departure": "2026-10-27T07:00:00+03:00", "arrival": "2026-10-27T10:55:00+03:00", "duration": 14100.0,
     "from": {"title": "Москва (Ленинградский вокзал)"}, "to": {"title": "Санкт-Петербург (Московский вокзал)"},
     "thread": {"number": "752А", "title": "Москва — Санкт-Петербург", "carrier": {"title": "ФПК"}}},
    {"departure": "2026-10-27T23:55:00+03:00", "arrival": "2026-10-28T08:20:00+03:00", "duration": 30300.0,
     "from": {"title": "Москва"}, "to": {"title": "Санкт-Петербург"},
     "thread": {"number": "054А", "title": "Москва — Санкт-Петербург", "carrier": {"title": "ФПК"}}},
]}


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        schedule._CACHE.clear()

    def test_dedupe_and_station_names(self):
        seg = {"departure": "2026-10-27T00:20:00+03:00", "arrival": "2026-10-27T09:00:00+03:00", "duration": 31200.0,
               "from": {"title": "Москва (Ленинградский вокзал)", "popular_title": "Ленинградский вокзал"},
               "to": {"title": "Санкт-Петербург (Московский вокзал)", "popular_title": None},
               "thread": {"number": "022А", "title": "Москва — Санкт-Петербург"}}
        items = schedule.parse({"segments": [seg, dict(seg)]})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["from"], "Ленинградский вокзал")
        self.assertEqual(items[0]["to"], "Санкт-Петербург (Московский вокзал)")

    def test_cache(self):
        with mock.patch.dict(os.environ, {"YANDEX_RASP_API_KEY": "k"}):
            with mock.patch.object(schedule, "_get", return_value=SAMPLE) as get:
                schedule.search("Москва", "Санкт-Петербург", "2026-10-27", "train")
                schedule.search("Москва", "Санкт-Петербург", "2026-10-27", "train")
        self.assertEqual(get.call_count, 2)  # поезда и электрички — один раз

    def test_parse_sorted_with_prices_and_overnight(self):
        items = schedule.parse(SAMPLE)
        self.assertEqual([i["number"] for i in items], ["752А", "758А", "054А"])
        self.assertEqual((items[0]["departure"], items[0]["arrival"]), ("07:00", "10:55"))
        self.assertEqual(items[1]["price_from"], 4100)
        self.assertIsNone(items[0]["price_from"])
        self.assertTrue(items[2]["next_day"])
        self.assertEqual(items[0]["duration_min"], 235)

    def test_find_by_number_ignores_latin_and_spaces(self):
        items = schedule.parse(SAMPLE)
        self.assertEqual(schedule.find(items, "752a")["departure"], "07:00")
        self.assertEqual(schedule.find(items, " 752 А ")["arrival"], "10:55")
        self.assertIsNone(schedule.find(items, "999"))

    def test_without_key_is_unavailable(self):
        os.environ.pop("YANDEX_RASP_API_KEY", None)
        res = schedule.search("Москва", "Санкт-Петербург", "2026-10-27", "train")
        self.assertFalse(res["available"])
        self.assertIn("ключ", res["message"])

    def test_with_key_requests_city_codes(self):
        with mock.patch.dict(os.environ, {"YANDEX_RASP_API_KEY": "k"}):
            with mock.patch.object(schedule, "_get", return_value=SAMPLE) as get:
                res = schedule.search("Москва", "Санкт-Петербург", "2026-10-27", "train")
        params = get.call_args_list[0][0][0]
        self.assertEqual((params["from"], params["to"], params["transport_types"]), ("c213", "c2", "train"))
        self.assertEqual(get.call_args_list[1][0][0]["transport_types"], "suburban")
        self.assertTrue(res["available"])
        self.assertEqual(len(res["items"]), 3)  # те же рейсы электричками не повторяются

    def test_suburban_in_train_list(self):
        sub = {"segments": [{"departure": "2026-10-27T08:10:00+03:00", "arrival": "2026-10-27T09:40:00+03:00",
                             "duration": 5400.0, "from": {"title": "Москва (Ярославский вокзал)"},
                             "to": {"title": "Сергиев Посад"}, "thread": {"number": "6001", "title": "Москва — Александров"},
                             "tickets_info": {"places": [{"price": {"whole": 330}}]}}]}
        with mock.patch.dict(os.environ, {"YANDEX_RASP_API_KEY": "k"}):
            with mock.patch.object(schedule, "_get", side_effect=lambda p: sub if p["transport_types"] == "suburban" else SAMPLE), \
                    mock.patch.object(schedule.prices, "enrich", return_value=None):  # без сети
                res = schedule.search("Москва", "Санкт-Петербург", "2026-10-27", "train")
        el = [x for x in res["items"] if x.get("suburban")]
        self.assertEqual([(x["number"], x["price_from"]) for x in el], [("6001", 330)])
        self.assertEqual([x["departure"] for x in res["items"]][:2], ["07:00", "08:10"])  # по времени вместе с поездами

    def test_travel_url(self):
        self.assertEqual(schedule.travel_url("Москва", "Санкт-Петербург", "plane", "2026-10-27"),
                         "https://travel.yandex.ru/avia/routes/moscow--saint-petersburg/?when=2026-10-27")
        self.assertEqual(schedule.travel_url("Санкт-Петербург", "Москва", "bus"),
                         "https://travel.yandex.ru/buses/saint-petersburg--moscow/")
        self.assertIsNone(schedule.travel_url("Урюпинск", "Москва", "train"))

    def test_buy_links(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in [k for k in os.environ if k.startswith("TP_")]:  # без настроек партнёрки из .env
                os.environ.pop(k, None)
            plane = schedule.buy_links("Москва", "Санкт-Петербург", "plane", "2026-10-27", adults=2)
            self.assertEqual([x["label"] for x in plane], ["Яндекс Путешествия", "Авиасейлс"])
            self.assertEqual(plane[1]["url"], "https://www.aviasales.ru/search/MOW2710LED2")
            train = schedule.buy_links("Санкт-Петербург", "Москва", "train", "2026-10-29")
            self.assertEqual(train[1]["url"], "https://www.tutu.ru/poezda/Sankt-Peterburg/Moskva/")
        # с маркером Travelpayouts ссылка Авиасейлс становится партнёрской, Туту.ру — только когда задан номер программы
        with mock.patch.dict(os.environ, {"TP_MARKER": "12345", "TP_PROGRAM_TUTU": "", "TP_PROGRAM_AVIASALES": ""}):
            plane = schedule.buy_links("Москва", "Санкт-Петербург", "plane", "2026-10-27")
            self.assertTrue(plane[1]["url"].startswith("https://tp.media/r?marker=12345&p=4114&u=https%3A%2F%2Fwww.aviasales.ru"))
            train = schedule.buy_links("Москва", "Санкт-Петербург", "train", "2026-10-27")
            self.assertEqual(train[1]["url"], "https://www.tutu.ru/poezda/Moskva/Sankt-Peterburg/")

    def test_unknown_city(self):
        with mock.patch.dict(os.environ, {"YANDEX_RASP_API_KEY": "k"}):
            res = schedule.search("Урюпинск", "Санкт-Петербург", "2026-10-27", "train")
        self.assertFalse(res["available"])


class PartnerLinkTest(unittest.TestCase):
    ENV = {"TP_MARKER": "795224", "TP_TRS": "591843", "TP_PROGRAM_OSTROVOK": "7038",
           "TP_CAMPAIGN_OSTROVOK": "459", "TP_ERID_OSTROVOK": "2VtzqwyEybB", "TP_PROGRAM_TUTU": "",
           "TP_PROGRAM_SUTOCHNO": ""}

    def test_ostrovok(self):
        with mock.patch.dict(os.environ, self.ENV):
            url = schedule.partner_any("https://ostrovok.ru/hotel/russia/st._petersburg/?dates=1&guests=2")
        self.assertTrue(url.startswith("https://tp.media/r?marker=795224&p=7038&"))
        self.assertIn("campaign_id=459", url)
        self.assertIn("erid=2VtzqwyEybB", url)
        self.assertIn("u=https%3A%2F%2Fostrovok.ru%2Fhotel", url)

    def test_ad_label(self):
        with mock.patch.dict(os.environ, dict(self.ENV, TP_AD_OSTROVOK="ООО «Бронирование Гостиниц», ИНН 7703389880")):
            url = schedule.partner_any("https://ostrovok.ru/hotel/russia/st._petersburg/")
            self.assertEqual(schedule.ad_label(url),
                             "Реклама · ООО «Бронирование Гостиниц», ИНН 7703389880 · erid: 2VtzqwyEybB")
        self.assertIsNone(schedule.ad_label("https://spb.sutochno.ru/361449"))

    def test_untouched(self):
        with mock.patch.dict(os.environ, self.ENV):
            for url in ("https://spb.sutochno.ru/361449", "https://tp.media/r?marker=1&u=x",
                        "https://www.tutu.ru/poezda/Moskva/Sankt-Peterburg/"):  # у Туту.ру и Суточно.ру не задан номер программы
                self.assertEqual(schedule.partner_any(url), url)


if __name__ == "__main__":
    unittest.main()
