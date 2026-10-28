"""Цены для списка рейсов: Туту.ру (поезда) и Авиасейлс (самолёты), без сети."""
import os
import unittest
from unittest import mock

from app import prices, schedule

TUTU = {"trips": [
    {"trainNumber": "752А", "categories": [{"type": "sedentary", "price": 3900}, {"type": "sedentary", "price": 4300}]},
    {"trainNumber": "054А", "categories": [{"type": "coupe", "price": 4307}, {"type": "plazcard", "price": 2650},
                                           {"type": "lux", "price": 14465}]},
    {"trainNumber": "", "categories": [{"type": "coupe", "price": 1}]},
], "url": "https://www.tutu.ru/"}

AVIA = {"success": True, "data": [
    {"airline": "SU", "flight_number": "6", "departure_at": "2026-10-27T07:00:00+03:00", "price": 5200, "transfers": 0},
    {"airline": "SU", "flight_number": "6", "departure_at": "2026-10-27T07:00:00+03:00", "price": 4800, "transfers": 0},
    {"airline": "DP", "flight_number": "6823", "departure_at": "2026-10-27T08:05:00+03:00", "price": 2990, "transfers": 0},
    {"airline": "FV", "flight_number": "6039", "departure_at": "2026-10-28T06:00:00+03:00", "price": 1000, "transfers": 0},
    {"airline": "S7", "flight_number": "1", "departure_at": "2026-10-27T10:00:00+03:00", "price": 100, "transfers": 1},
]}


class PricesTest(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(prices.train_key("054A"), prices.train_key("54А"))
        self.assertEqual(prices.flight_key("SU 6"), "SU6")
        self.assertEqual(prices.flight_key("SU-0006"), "SU6")

    def test_parse_tutu(self):
        t = prices.parse_tutu(TUTU)
        self.assertEqual(t["752А"], [{"name": "сидячий", "price": 3900}])
        self.assertEqual([c["name"] for c in t["54А"]], ["плацкарт", "купе", "СВ"])
        self.assertEqual(len(t), 2)

    def test_parse_avia_only_date_and_direct(self):
        a = prices.parse_avia(AVIA, "2026-10-27")
        self.assertEqual(a, {"SU6": 4800, "DP6823": 2990})

    def test_jsonp(self):
        with mock.patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value.read.return_value = b'n({"trips": []});'
            self.assertEqual(prices._fetch("https://x"), {"trips": []})

    def test_enrich_train(self):
        items = [{"number": "054А", "price_from": None}, {"number": "999", "price_from": None}]
        with mock.patch.object(prices, "_fetch", return_value=TUTU) as f:
            note = prices.enrich(items, "Москва", "Санкт-Петербург", "2026-10-27", "train")
        self.assertIn("term=2000000", f.call_args[0][0])
        self.assertEqual(items[0]["price_from"], 2650)
        self.assertEqual(items[0]["classes"][1], {"name": "купе", "price": 4307})
        self.assertNotIn("classes", items[1])
        self.assertIn("Туту.ру", note)

    def test_enrich_plane_needs_token(self):
        items = [{"number": "SU 6", "price_from": None}]
        os.environ.pop("TP_TOKEN", None)
        with mock.patch.object(prices, "_fetch", return_value=AVIA) as f:
            self.assertIsNone(prices.enrich(items, "Москва", "Санкт-Петербург", "2026-10-27", "plane"))
            f.assert_not_called()
        with mock.patch.dict(os.environ, {"TP_TOKEN": "t"}):
            with mock.patch.object(prices, "_fetch", return_value=AVIA):
                note = prices.enrich(items, "Москва", "Санкт-Петербург", "2026-10-27", "plane")
        self.assertEqual(items[0]["price_from"], 4800)
        self.assertIn("Авиасейлс", note)
        # самый дешёвый в ответе — DP 6823, которого нет в этом списке: подпись всё равно называет рейс
        self.assertIn("DP 6823", note)
        self.assertFalse(items[0]["cheapest"])

    def test_source_error_keeps_schedule(self):
        schedule._CACHE.clear()
        seg = {"departure": "2026-10-27T07:00:00+03:00", "arrival": "2026-10-27T10:55:00+03:00", "duration": 14100,
               "thread": {"number": "752А", "title": "Москва — Санкт-Петербург"}}
        with mock.patch.dict(os.environ, {"YANDEX_RASP_API_KEY": "k"}):
            with mock.patch.object(schedule, "_get", return_value={"segments": [seg]}):
                with mock.patch.object(prices, "_fetch", side_effect=OSError("нет сети")):
                    res = schedule.search("Москва", "Санкт-Петербург", "2026-10-27", "train")
        self.assertTrue(res["available"])
        self.assertEqual(len(res["items"]), 1)
        self.assertIsNone(res["price_note"])


if __name__ == "__main__":
    unittest.main()
