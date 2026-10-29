"""Подсказки адресов: разбор ответов DaData и Photon, оценка дороги до вокзала, работа без сети."""
import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import json
import os
import unittest
from unittest import mock

from app import address

DADATA = {"suggestions": [
    {"value": "г Москва, ул Тверская, д 7", "unrestricted_value": "125009, г Москва, ул Тверская, д 7",
     "data": {"geo_lat": "55.7579", "geo_lon": "37.6115"}},
    {"value": "г Москва, ул Тверская", "data": {"geo_lat": None, "geo_lon": None}},
]}

PHOTON = {"type": "FeatureCollection", "features": [
    {"geometry": {"type": "Point", "coordinates": [37.6115, 55.7579]},
     "properties": {"type": "house", "street": "Тверская улица", "housenumber": "7", "city": "Москва"}},
    {"geometry": {"type": "Point", "coordinates": [37.6050, 55.7640]},
     "properties": {"type": "street", "name": "Тверская улица", "city": "Москва"}},
    {"geometry": {"type": "Point", "coordinates": [37.6115, 55.7579]},
     "properties": {"type": "house", "street": "Тверская улица", "housenumber": "7", "city": "Москва"}},
]}


class AddressTest(unittest.TestCase):
    def test_parse_dadata(self):
        out = address.parse_dadata(DADATA)
        self.assertEqual(out[0]["value"], "г Москва, ул Тверская, д 7")
        self.assertAlmostEqual(out[0]["lat"], 55.7579)
        self.assertIsNone(out[1]["lat"])

    def test_parse_photon_dedupes(self):
        out = address.parse_photon(PHOTON)
        self.assertEqual(out[0]["value"], "Москва, Тверская улица, 7")
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0]["lon"], 37.6115)

    def test_estimate_to_station(self):
        # Тверская, 7 -> Ленинградский вокзал: ~3,3 км по прямой
        m = address.estimate_minutes(55.7579, 37.6115, (55.7765, 37.6553))
        self.assertTrue(20 <= m <= 40, m)

    def test_suggest_uses_photon_without_key_and_adds_estimate(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DADATA_API_KEY", None)
            with mock.patch.object(address, "_http_json", return_value=PHOTON) as http:
                res = address.suggest("Тверская 7", "Москва", "train")
        self.assertEqual(res["provider"], "photon")
        url = http.call_args[0][0].full_url
        self.assertIn("photon", url)
        self.assertIn("%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0", url)  # город отправления добавлен к запросу
        self.assertEqual(res["suggestions"][0]["value"], "Тверская улица, 7")
        self.assertEqual(res["suggestions"][0]["full"], "Москва, Тверская улица, 7")
        self.assertEqual(res["suggestions"][0]["station"], "Ленинградский вокзал")
        self.assertGreater(res["suggestions"][0]["to_station_min"], 0)

    def test_departure_station_depends_on_destination(self):
        self.assertEqual(address.departure_point("Москва", "train", "Казань")[0], "Казанский вокзал")
        self.assertEqual(address.departure_point("Москва", "auto", "")[0], "Ленинградский вокзал")
        self.assertIsNone(address.departure_point("Москва", "train", "Урюпинск"))

    def test_suggest_uses_dadata_with_key(self):
        with mock.patch.dict(os.environ, {"DADATA_API_KEY": "test"}):
            with mock.patch.object(address, "_http_json", return_value=DADATA) as http:
                res = address.suggest("Тверская 7", "Москва", "plane")
        req = http.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Token test")
        body = json.loads(req.data)
        self.assertEqual(body["locations"], [{"city": "Москва"}])
        self.assertEqual(res["suggestions"][0]["value"], "ул Тверская, д 7")
        self.assertEqual(res["suggestions"][0]["full"], "Москва, ул Тверская, д 7")
        self.assertEqual(res["provider"], "dadata")
        self.assertEqual(res["suggestions"][0]["station"], "Аэропорт Шереметьево")

    def test_street_part(self):
        self.assertEqual(address.street_part("г Москва, ул Тверская, д 7", "Москва"), "ул Тверская, д 7")
        self.assertEqual(address.street_part("Москва, Тверская улица, 7", "москва"), "Тверская улица, 7")
        self.assertEqual(address.street_part("Химки, ул Ленина, 1", "Москва"), "Химки, ул Ленина, 1")

    def test_other_city_results_dropped_for_photon(self):
        other = {"features": [{"geometry": {"coordinates": [30.3, 59.9]},
                               "properties": {"type": "house", "street": "Тверская улица", "housenumber": "7",
                                              "city": "Санкт-Петербург"}}]}
        os.environ.pop("DADATA_API_KEY", None)
        with mock.patch.object(address, "_http_json", return_value=other):
            self.assertEqual(address.suggest("Тверская 7", "Москва", "train")["suggestions"], [])

    def test_short_query_makes_no_request(self):
        with mock.patch.object(address, "_http_json") as http:
            self.assertEqual(address.suggest("Тв", "Москва", "train")["suggestions"], [])
        http.assert_not_called()

    def test_network_error_retried_once(self):
        import urllib.error
        req = mock.Mock()
        with mock.patch.object(address.net, "get_json",
                               side_effect=[urllib.error.URLError("handshake timed out"), PHOTON]) as get:
            self.assertEqual(address._http_json(req), PHOTON)  # со второй попытки
        self.assertEqual(get.call_count, 2)
        with mock.patch.object(address.net, "get_json", side_effect=OSError("нет сети")) as get, \
                self.assertRaises(OSError):
            address._http_json(req)
        self.assertEqual(get.call_count, 2)  # не больше двух попыток
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch.object(address.net, "get_json", side_effect=err) as get, \
                self.assertRaises(urllib.error.HTTPError):
            address._http_json(req)
        self.assertEqual(get.call_count, 1)  # ответ с ошибкой — не повторяем


if __name__ == "__main__":
    unittest.main()
