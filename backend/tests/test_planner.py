import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import os
import unittest
from unittest import mock

from app import catalog, planner

BASE = {"origin": "Москва", "city": "Санкт-Петербург", "start_date": "2026-10-27", "start_time": "09:00",
        "days": 3, "transport": "auto", "adults": 2, "kids_ages": [], "budget": 100000,
        "pace": "balanced", "wishes": "хотелось бы посетить театр недорого"}


def items(trip):
    return [it for d in trip["days"] for it in d["items"]]


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.trip = planner.plan_trip(dict(BASE))

    def test_example_from_spec(self):
        days = self.trip["days"]
        self.assertEqual(len(days), 3)
        first = days[0]["items"]
        self.assertEqual(first[0]["title"], "Выход из дома")
        # 09:00 поезд − 30 мин посадка − 40 мин дорога до вокзала
        self.assertEqual(planner.fmt_hm(first[0]["start"]), "07:50")
        train = first[2]
        self.assertEqual(planner.fmt_hm(train["start"]), "09:00")
        self.assertTrue(any(i["kind"] == "hotel" for i in first))
        # театр недорого — один раз, самый дешёвый из подходящих
        theatres = [i for i in items(self.trip) if i["kind"] == "theatre"]
        self.assertEqual(len(theatres), 1)
        self.assertEqual(theatres[0]["place_id"], "alexandrinsky")
        self.assertEqual(theatres[0]["warnings"], [])

    @mock.patch.dict(os.environ, {"TP_MARKER": ""})  # ссылки без партнёрской метки из .env
    def test_ticket_price_from_schedule(self):
        trip = planner.plan_trip(dict(BASE, transport="train", kids_ages=[8], out_number="752А", out_price=3200,
                                      back_number="760А"))
        tickets = [i for i in items(trip) if i["kind"] == "travel" and (i["cost"] or {}).get("kind") == "ticket"]
        out, back = tickets[0], tickets[-1]
        # 2 взрослых + ребёнок за полцены
        self.assertEqual(out["cost"]["estimate"], int(3200 * 2.5))
        self.assertIn("билет от 3\u202f200 ₽", out["note"])
        self.assertEqual(back["cost"]["estimate"], int(planner.ROUTES["Санкт-Петербург"][("москва", "train")]["price"] * 2.5))
        self.assertNotIn("билет от", back["note"])
        buy = [lk["url"] for lk in out["links"] if lk["label"].startswith("Купить билет")]
        self.assertEqual(buy, ["https://travel.yandex.ru/trains/moscow--saint-petersburg/?when=2026-10-27",
                               "https://www.tutu.ru/poezda/Moskva/Sankt-Peterburg/"])
        back_buy = [lk["url"] for lk in back["links"] if lk["label"].startswith("Купить билет")]
        self.assertEqual(back_buy[0], "https://travel.yandex.ru/trains/saint-petersburg--moscow/?when=2026-10-29")

    def test_hotel_and_food_links(self):
        trip = planner.plan_trip(dict(BASE, kids_ages=[8]))
        hotel = next(i for i in items(trip) if i["kind"] == "hotel")
        urls = {lk["label"]: lk["url"] for lk in hotel["links"]}
        self.assertEqual(urls["Цены на даты — Яндекс Путешествия"],
                         "https://travel.yandex.ru/hotels/search/?adults=2&checkinDate=2026-10-27"
                         "&checkoutDate=2026-10-29&childrenAges=8&geoId=2")
        self.assertIn("Сайт отеля", urls)
        food = [i for i in items(trip) if i["kind"] == "food" and i["place_id"]]
        self.assertTrue(food and all(any("Яндекс Карт" in lk["label"] for lk in i["links"]) for i in food))

    def test_times_are_sequential(self):
        for d in self.trip["days"]:
            prev_end = None
            for it in d["items"]:
                if prev_end is not None:
                    gap = (it["transfer"] or {}).get("minutes", 0)
                    self.assertGreaterEqual(it["start"], prev_end + gap - 1, it["title"])
                self.assertEqual(it["end"], it["start"] + it["duration"])
                prev_end = it["end"]

    def test_each_day_has_lunch_and_no_closed_places(self):
        for d in self.trip["days"]:
            titles = [i["title"] for i in d["items"]]
            self.assertTrue(any(t.startswith("Обед") for t in titles), d["label"])
        for it in items(self.trip):
            self.assertNotIn("В этот день закрыто", it["warnings"], it["title"])

    def test_no_place_twice(self):
        ids = [i["place_id"] for i in items(self.trip) if i["place_id"] and i["kind"] not in ("hotel",)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_hermitage_not_on_monday(self):
        t = planner.plan_trip(dict(BASE, start_date="2026-10-26", days=1, start_time="07:00", return_time="21:00"))
        self.assertNotIn("hermitage", [i["place_id"] for i in items(t)])

    def test_duration_change_shifts_following(self):
        day = self.trip["days"][1]["items"]
        target = next(i for i in day if i["kind"] == "museum")
        idx = day.index(target)
        before = day[idx + 1]["start"]
        planner.set_duration(self.trip, target["id"], target["duration"] + 30)
        self.assertEqual(day[idx + 1]["start"], before + 30)

    def test_shorter_road_to_station_means_leaving_later(self):
        first = self.trip["days"][0]["items"]
        go, boarding, train = first[0], first[1], first[2]
        planner.set_duration(self.trip, go["id"], go["duration"] - 15)
        self.assertEqual(planner.fmt_hm(go["start"]), "08:05")
        self.assertEqual(go["end"], boarding["start"])
        self.assertEqual(boarding["wait"], 0)
        self.assertEqual(self.trip["input"]["to_station_min"], 25)
        self.assertIn("≈ 25 мин", go["note"])
        planner.set_duration(self.trip, go["id"], 85)
        self.assertIn("≈ 1 ч 25 мин", go["note"])
        planner.set_duration(self.trip, go["id"], 25)
        # короче посадка — позже и посадка, и выход из дома; поезд на месте
        planner.set_duration(self.trip, boarding["id"], boarding["duration"] - 10)
        self.assertEqual(planner.fmt_hm(go["start"]), "08:15")
        self.assertEqual(planner.fmt_hm(train["start"]), "09:00")

    def test_replace_uses_alternative(self):
        it = next(i for i in items(self.trip) if i["kind"] == "museum" and i["alternatives"])
        alt = it["alternatives"][0]
        planner.replace_place(self.trip, it["id"], alt)
        self.assertEqual(it["place_id"], alt)
        self.assertTrue(it["user_changed"])
        with self.assertRaises(ValueError):
            planner.replace_place(self.trip, it["id"], "no_such_place")

    def test_document_makes_sum_exact(self):
        hotel = next(i for i in items(self.trip) if i["kind"] == "hotel")
        before = self.trip["budget"]
        planner.attach_doc(self.trip, hotel["id"], {"sum": 24600, "paid": 12300})
        b = self.trip["budget"]
        self.assertEqual(b["paid"], before["paid"] + 12300)
        self.assertEqual(b["onsite"], before["onsite"] + 12300)
        self.assertEqual(b["total"], before["total"] - hotel["cost"]["estimate"] + 24600)
        planner.detach_doc(self.trip, hotel["id"])
        self.assertEqual(self.trip["budget"]["total"], before["total"])

    def test_ticket_time_fixes_item(self):
        museum = next(i for i in items(self.trip) if i["kind"] == "museum")
        later = planner.fmt_hm(museum["start"] + 40)
        planner.attach_doc(self.trip, museum["id"], {"sum": 1000, "start": later})
        self.assertEqual(planner.fmt_hm(museum["start"]), later)
        self.assertGreater(museum["wait"], 0)
        # билет на время, к которому не успеваем, — предупреждение
        planner.attach_doc(self.trip, museum["id"], {"sum": 1000, "start": "10:00"})
        self.assertTrue(any("Не успеваете" in w for w in museum["warnings"]))

    def test_kids_get_kid_places(self):
        t = planner.plan_trip(dict(BASE, kids_ages=[6], pace="calm", wishes=""))
        kinds = [i["place_id"] for i in items(t) if i["place_id"]]
        data_ids = {"faberge", "pushkin", "erarta"}  # «взрослые» музеи
        self.assertFalse(data_ids & set(kinds))

    def test_pace_controls_amount(self):
        busy = planner.plan_trip(dict(BASE, pace="max", wishes=""))
        calm = planner.plan_trip(dict(BASE, pace="relaxed", wishes=""))
        count = lambda t: sum(1 for i in items(t) if i["kind"] in ("museum", "walk"))
        self.assertGreater(count(busy), count(calm))

    def test_settings_and_dates(self):
        t = planner.plan_trip(dict(BASE, end_date="2026-10-30", home_address="Москва, Тверская 1",
                                   to_station_min=55, boarding_min=20, sight_buffer_min=15))
        self.assertEqual(len(t["days"]), 4)
        first = t["days"][0]["items"]
        self.assertEqual(planner.fmt_hm(first[0]["start"]), "07:45")
        self.assertIn("Тверская", first[0]["note"])
        self.assertTrue(first[0]["links"])
        museum = next(i for i in items(t) if i["kind"] == "museum")
        self.assertEqual(museum["duration"], museum["default_duration"])
        with self.assertRaises(ValueError):
            planner.plan_trip(dict(BASE, end_date="2026-10-20"))

    def test_food_note_has_rating_and_hours(self):
        ctx = planner.trip_ctx(self.trip)
        for it in items(self.trip):
            if it["kind"] != "food" or not it.get("place_id"):
                continue
            p = ctx.place(it["place_id"])
            self.assertIn(f"Средний чек ≈ {planner.rub(p['avg_check'])}", it["note"])
            if p.get("placeholder"):  # у заглушки «подбор из Яндекс Карт» своих данных нет
                continue
            self.assertIn("Работает ", it["note"], it["title"])
            if p.get("rating"):
                self.assertIn("на Яндекс Картах", it["note"])

    def test_hours_text(self):
        self.assertEqual(planner.hours_text({"hours": {"open": "09:00", "close": "21:00", "closed": []}}),
                         "Работает 09:00–21:00")
        self.assertEqual(planner.hours_text({"hours": {"open": "09:00", "close": "23:00", "closed": [0, 1],
                                                       "late": {"4": "01:00", "5": "01:00", "6": "22:00"}}}),
                         "Работает 09:00–23:00, пт и сб до 01:00, вс до 22:00, выходные пн и вт")
        self.assertEqual(planner.hours_text({"hours": {"open": "00:00", "close": "23:59", "closed": []}}),
                         "Работает круглосуточно")
        self.assertEqual(planner.hours_text({}), "")
        self.assertEqual(planner.rating_text({"rating": 4.5, "reviews": 211}), "★ 4,5 на Яндекс Картах (211)")
        self.assertEqual(planner.rating_text({"rating": 5.0}), "★ 5,0 на Яндекс Картах")
        self.assertEqual(planner.rating_text({}), "")

    def test_food_including_breakfast_is_replaceable(self):
        bf = next(i for i in items(self.trip) if i.get("meal") == "breakfast")
        self.assertTrue(bf["alternatives"])
        start = bf["start"]
        planner.replace_place(self.trip, bf["id"], bf["alternatives"][0])
        self.assertTrue(bf["title"].startswith("Завтрак · "))
        self.assertLessEqual(bf["start"] - start, 30)  # прибавился только переход до кафе
        lunch = next(i for i in items(self.trip) if i.get("meal") == "lunch")
        self.assertGreater(len(lunch["alternatives"]), 4)

    def test_add_and_delete(self):
        d = self.trip["days"][1]
        sug = planner.suggestions(self.trip, 1)
        self.assertTrue(sug)
        n = len(d["items"])
        planner.add_item(self.trip, 1, d["items"][2]["id"], place_id=sug[0])
        self.assertEqual(len(d["items"]), n + 1)
        self.assertEqual(d["items"][3]["place_id"], sug[0])
        planner.add_item(self.trip, 1, None, title="Встреча с друзьями", duration=90)
        self.assertEqual(d["items"][-1]["kind"], "custom")
        planner.delete_item(self.trip, d["items"][3]["id"])
        self.assertEqual(len(d["items"]), n + 1)
        train = self.trip["days"][0]["items"][2]
        with self.assertRaises(ValueError):
            planner.delete_item(self.trip, train["id"])

    def test_no_city_places_before_arrival_and_after_departure(self):
        first, last = self.trip["days"][0]["items"], self.trip["days"][-1]["items"]
        go, train = first[0], first[2]
        self.assertTrue(planner.in_origin(self.trip, 0, go["id"]))
        self.assertEqual(planner.suggestions(self.trip, 0, after_id=go["id"]), [])
        self.assertFalse(planner.in_origin(self.trip, 0, train["id"]))
        self.assertTrue(planner.suggestions(self.trip, 0, after_id=train["id"]))
        self.assertTrue(planner.in_origin(self.trip, 2, last[-1]["id"]))
        self.assertFalse(planner.in_origin(self.trip, 2, last[0]["id"]))
        self.assertFalse(planner.in_origin(self.trip, 1, None))
        place = planner.suggestions(self.trip, 0, after_id=train["id"])[0]
        with self.assertRaises(ValueError):
            planner.add_item(self.trip, 0, go["id"], place_id=place)
        planner.add_item(self.trip, 0, go["id"], title="Заехать за билетами", duration=20)
        self.assertEqual(first[1]["title"], "Заехать за билетами")

    def test_single_day_trip_origin_positions(self):
        trip = planner.plan_trip(dict(BASE, days=1))
        items = trip["days"][0]["items"]
        self.assertTrue(planner.in_origin(trip, 0, items[0]["id"]))
        self.assertTrue(planner.in_origin(trip, 0, items[-1]["id"]))
        self.assertFalse(planner.in_origin(trip, 0, items[3]["id"]))

    def test_early_arrival_leaves_bags_and_checks_in_later(self):
        first = self.trip["days"][0]["items"]
        drop = next(i for i in first if i.get("stay_role") == "checkin")
        late = next(i for i in first if i.get("stay_role") == "checkin_late")
        # «Сапсан» приходит в 13:00, заселение в отель с 14:00
        self.assertTrue(drop["title"].startswith("Оставить вещи · "))
        self.assertEqual(drop["cost"]["kind"], "booking")
        self.assertTrue(late["title"].startswith("Заселение"))
        self.assertGreaterEqual(late["start"], planner.parse_hm("14:00"))
        self.assertEqual(late["warnings"], [])

    def test_check_in_right_after_lunch(self):
        # прилёт в 10:30: вещи оставили утром, заселяемся после обеда, а не поздно вечером
        for stay in ("hotel", "apartment"):
            trip = planner.plan_trip(dict(BASE, city="Казань", transport="plane", start_time="08:50", arrive_time="10:30",
                                          stay=stay, pace="max", wishes=""))
            first = trip["days"][0]["items"]
            late = next(i for i in first if i.get("stay_role") == "checkin_late")
            prev = first[first.index(late) - 1]
            self.assertLess(late["start"], planner.parse_hm("17:00"), stay)
            self.assertEqual(late["warnings"], [], stay)
            self.assertTrue(prev.get("meal") == "lunch" or prev["kind"] != "food", (stay, prev["title"]))

    def test_late_arrival_checks_in_at_once(self):
        trip = planner.plan_trip(dict(BASE, start_time="12:00"))
        first = trip["days"][0]["items"]
        checkin = next(i for i in first if i.get("stay_role") == "checkin")
        self.assertTrue(checkin["title"].startswith("Заселение · "))
        self.assertFalse(any(i.get("stay_role") == "checkin_late" for i in first))

    def test_apartment_stay(self):
        trip = planner.plan_trip(dict(BASE, stay="apartment", kids_ages=[8]))
        flat = trip["input"]
        roles = {i.get("stay_role"): i for i in items(trip) if i.get("stay_role")}
        apt = planner.Ctx(planner.TripInput.from_dict(flat)).place(roles["checkin"]["place_id"])
        self.assertEqual(planner.stay_kind(apt), "apartment")
        self.assertEqual(roles["breakfast"]["title"], "Завтрак в кафе рядом")
        self.assertEqual(roles["checkout"]["title"], "Выезд из квартиры")
        self.assertIn("Выезд до 11:00", roles["checkout"]["note"])
        self.assertIn("Возвращение в квартиру", roles["back"]["title"])
        # одна квартира на троих, а не номера по двое
        self.assertEqual(roles["checkin"]["cost"]["estimate"], apt["price_night"] * 2)
        urls = {lk["label"]: lk["url"] for lk in roles["checkin"]["links"]}
        self.assertEqual(urls["Квартиры на даты — Суточно.ру"],
                         "https://sutochno.ru/front/searchapp/search?occupied=2026-10-27%3B2026-10-29"
                         "&guests_adults=2&term=%D0%A1%D0%B0%D0%BD%D0%BA%D1%82-%D0%9F%D0%B5%D1%82%D0%B5%D1%80%D0%B1%D1%83%D1%80%D0%B3")
        self.assertEqual(urls["Квартиры и апартаменты на даты — Островок"],
                         "https://ostrovok.ru/hotel/russia/st._petersburg/?dates=27.10.2026-29.10.2026&guests=3")

    def test_checkout_deadline(self):
        trip = planner.plan_trip(dict(BASE, stay="apartment", pace="relaxed"))
        out = next(i for i in items(trip) if i.get("stay_role") == "checkout")
        # подъём 9:30, завтрак до 11:00 — к выезду из квартиры до 11:00 не успеваем
        self.assertTrue(any(w.startswith("Выезд до 11:00") for w in out["warnings"]), out["warnings"])

    def test_stay_chosen_in_form(self):
        trip = planner.plan_trip(dict(BASE, stay_id="h_high"))
        stays = {i["place_id"] for i in items(trip) if i.get("stay_role") and i["place_id"]}
        self.assertEqual(stays, {"h_high"})
        trip = planner.plan_trip(dict(BASE, stay_id="hermitage"))  # не жильё — подбираем сами
        self.assertNotIn("hermitage", {i["place_id"] for i in items(trip) if i.get("stay_role")})
        opts = planner.stay_options(planner.Ctx(planner.TripInput.from_dict(dict(BASE, adults=3))))
        self.assertEqual(opts["nights"], 2)
        high = next(r for r in opts["stays"] if r["id"] == "h_high")
        self.assertEqual((high["units"], high["total"]), (2, 28000 * 2 * 2))  # трое взрослых — два номера
        self.assertIn(opts["recommended"], {r["id"] for r in opts["stays"]})

    def test_manual_stay_from_other_site(self):
        m = {"name": "Квартира на Авито у Невского", "address": "Санкт-Петербург, Невский пр., 100", "lat": 59.932,
             "lon": 30.355, "price_night": "5 500", "check_in": "16:00", "check_out": "10:00", "kind": "apartment",
             "url": "https://www.avito.ru/sankt-peterburg/kvartiry/1"}
        p = planner.manual_stay_place(m)
        trip = planner.plan_trip(dict(BASE, stay_id=p["id"]), {p["id"]: p})
        stays = [i for i in items(trip) if i.get("stay_role") and i["place_id"]]
        self.assertEqual({i["place_id"] for i in stays}, {"manual-stay"})
        checkin = next(i for i in stays if i["stay_role"] in ("checkin", "checkin_late"))
        self.assertIn("Заселение с 16:00, выезд до 10:00", checkin["note"])
        self.assertEqual(checkin["cost"]["estimate"], 5500 * 2)
        self.assertEqual(checkin["links"][0], {"label": "Бронь на сайте", "url": m["url"]})
        self.assertTrue(any(i["title"] == "Выезд из квартиры" for i in stays))
        with self.assertRaises(ValueError):
            planner.manual_stay_place(dict(m, lat=None))  # адрес не из подсказок — нет координат
        with self.assertRaises(ValueError):
            planner.manual_stay_place(dict(m, check_in="25:00"))
        ctx = planner.Ctx(planner.TripInput.from_dict(BASE))
        self.assertEqual(planner.avito_link(ctx), "https://www.avito.ru/sankt-peterburg/kvartiry/sdam/posutochno")
        self.assertIn("checkin=2026-10-27&checkout=2026-10-29", planner.tripcom_link(ctx))

    def test_stay_not_replaceable_in_route(self):
        # жильё задаёт весь маршрут — меняется только в вводных с перестройкой программы
        checkin = next(i for i in items(self.trip) if i.get("stay_role") == "checkin")
        self.assertEqual(checkin["alternatives"], [])
        with self.assertRaises(ValueError) as e:
            planner.replace_place(self.trip, checkin["id"], "a_mid")
        self.assertIn("вводных", str(e.exception))
        museum = next(i for i in items(self.trip) if i["kind"] == "museum")
        with self.assertRaises(ValueError):
            planner.replace_place(self.trip, museum["id"], "a_mid")

    def test_minimum_duration_is_step(self):
        d = self.trip["days"][1]
        planner.add_item(self.trip, 1, None, title="Кофе", duration=5)
        self.assertEqual(d["items"][-1]["duration"], 5)
        planner.set_duration(self.trip, d["items"][-1]["id"], 1)
        self.assertEqual(d["items"][-1]["duration"], 5)
        self.trip["input"]["step_min"] = 15
        planner.set_duration(self.trip, d["items"][-1]["id"], 5)
        self.assertEqual(d["items"][-1]["duration"], 15)

    def test_document_checks(self):
        day2 = self.trip["days"][1]
        museum = next(i for i in day2["items"] if i["kind"] == "museum")
        orig = museum["start"]
        planner.attach_doc(self.trip, museum["id"], {"sum": 1000, "date": "2026-10-29"},
                           [{"name": "a.pdf", "filename": "a.pdf"}])
        self.assertTrue(any("не совпадает" in w for w in museum["warnings"]))
        planner.attach_doc(self.trip, museum["id"], {"sum": 1000, "date": day2["date"], "start": "21:00"},
                           [{"name": "b.pdf", "filename": "b.pdf"}])
        self.assertEqual(len(museum["doc"]["files"]), 2)
        self.assertTrue(any("вне часов работы" in w for w in museum["warnings"]))
        start = planner.fmt_hm(orig)
        planner.attach_doc(self.trip, museum["id"], {"sum": 1000, "date": day2["date"], "start": start,
                                                      "end": planner.fmt_hm(orig + 30)})
        self.assertEqual(museum["duration"], 30)
        self.assertEqual([w for w in museum["warnings"] if "билет" in w], [])
        planner.remove_doc_file(self.trip, museum["id"], "a.pdf")
        self.assertEqual(len(museum["doc"]["files"]), 1)

    def test_budget_tolerance(self):
        # лимит считаем от стоимости готовой поездки: сама сумма зависит от данных каталога
        t = planner.plan_trip(dict(BASE, budget_tolerance_pct=10))
        total = t["budget"]["total"]
        for limit, status in ((total, "ok"), (int(total / 1.05), "tolerance"), (int(total / 1.5), "over")):
            t["input"]["budget"] = limit
            b = planner.budget(t)
            self.assertEqual(b["status"], status, (limit, b["total"]))
            self.assertEqual(b["hard_limit"], int(limit * 1.1))
        self.assertGreater(b["total"], b["hard_limit"])

    def test_exact_schedule(self):
        t = planner.plan_trip(dict(BASE, transport="train", start_time="07:00", arrive_time="10:55", out_number="752А",
                                   return_time="19:30", return_arrive_time="23:25", back_number="771А"))
        out = t["days"][0]["items"][2]
        self.assertEqual((planner.fmt_hm(out["start"]), planner.fmt_hm(out["end"])), ("07:00", "10:55"))
        self.assertIn("№ 752А", out["title"])
        self.assertIn("прибытие 10:55", out["note"])
        self.assertTrue(any("rasp.yandex.ru" in l["url"] for l in out["links"]))
        back = t["days"][-1]["items"][-1]
        self.assertEqual((planner.fmt_hm(back["start"]), planner.fmt_hm(back["end"])), ("19:30", "23:25"))
        # прибытие после полуночи
        t = planner.plan_trip(dict(BASE, transport="plane", start_time="23:30", arrive_time="01:00", days=2))
        self.assertEqual(t["days"][0]["items"][2]["duration"], 90)
        with self.assertRaises(ValueError):
            planner.plan_trip(dict(BASE, arrive_time="25:00"))

    def test_different_transport_back(self):
        trip = planner.plan_trip(dict(BASE, transport="train", return_transport="plane", return_time="19:00"))
        first, last = trip["days"][0]["items"], trip["days"][-1]["items"]
        self.assertEqual(first[2]["title"], "Поезд «Сапсан» Москва → Санкт-Петербург")
        self.assertEqual(first[1]["duration"], 30)  # посадка на поезд
        board, plane = last[-2], last[-1]
        self.assertEqual(plane["title"], "Самолёт Санкт-Петербург → Москва")
        self.assertEqual(board["title"], "Аэропорт Пулково: посадка")
        self.assertEqual((board["duration"], planner.fmt_hm(board["start"])), (120, "17:00"))
        self.assertIn("avia", " ".join(l["url"] for l in plane["links"]))
        self.assertEqual(trip["summary"]["transport"], "туда — поезд «Сапсан», обратно — самолёт")
        self.assertEqual(trip["input"]["return_transport"], "plane")
        # без return_transport (старые поездки) — обратно на том же
        same = planner.plan_trip(dict(BASE, transport="train"))
        self.assertEqual(same["input"]["return_transport"], "train")
        self.assertEqual(same["summary"]["transport"], "Поезд «Сапсан»")
        with self.assertRaises(ValueError):
            planner.plan_trip(dict(BASE, return_transport="ракета"))

    def test_validation(self):
        with self.assertRaises(ValueError):
            planner.plan_trip(dict(BASE, city="Урюпинск"))
        with self.assertRaises(ValueError):
            planner.plan_trip(dict(BASE, days=0))
        with self.assertRaises(ValueError):
            planner.plan_trip(dict(BASE, start_time="25:00"))

    def test_all_paces_and_lengths_build(self):
        for pace in planner.PACE_CAPACITY:
            for days in (1, 2, 4, 7):
                for kids in ([], [4, 10]):
                    t = planner.plan_trip(dict(BASE, pace=pace, days=days, kids_ages=kids))
                    self.assertEqual(len(t["days"]), days)
                    for it in items(t):
                        self.assertLess(it["start"], 26 * 60, (pace, days, it["title"]))



KAZAN = dict(BASE, city="Казань", transport="plane", start_time="07:00", return_time="19:00", wishes="")


class KazanTest(unittest.TestCase):
    def test_cities_from_catalog_files(self):
        self.assertEqual(catalog.cities(), ["Выборг", "Казань", "Калининград", "Нижний Новгород", "Санкт-Петербург"])
        self.assertEqual(catalog.normalize_city("питер"), "spb_places.json")
        self.assertEqual(catalog.normalize_city(" казань "), "kazan_places.json")

    def test_kazan_route_and_arrival(self):
        trip = planner.plan_trip(dict(KAZAN))
        first = items(trip)
        self.assertEqual(first[2]["title"], "Самолёт Москва → Казань")
        land = next(i for i in first if i["kind"] == "hotel")
        self.assertEqual(land["transfer"]["mode"], "метро или такси")
        self.assertLess(land["transfer"]["minutes"], 90)  # аэропорт за городом — быстрее, чем по центру

    def test_daytrip_in_middle_day_with_lunch_there(self):
        trip = planner.plan_trip(dict(KAZAN, wishes="история, хотим в Свияжск"))
        day = trip["days"][1]["items"]
        trip_it = next(i for i in day if i["kind"] == "daytrip")
        self.assertEqual(trip_it["place_id"], "sviyazhsk")
        self.assertIn("такси", trip_it["transfer"]["mode"])
        self.assertEqual(trip_it["transfer"]["minutes"], 70)
        self.assertLessEqual(trip_it["start"], 12 * 60)  # с утра
        self.assertFalse(any(i.get("meal") == "lunch" for i in day))  # обед — на острове
        self.assertFalse(any(i["title"].startswith("Вечерняя прогулка") for i in day))
        self.assertTrue(all(i["end"] <= 24 * 60 for i in day))

    def test_no_daytrip_without_full_day(self):
        trip = planner.plan_trip(dict(KAZAN, days=2, wishes="хотим в Свияжск"))
        self.assertFalse(any(i["kind"] == "daytrip" for i in items(trip)))

    def test_interests_raise_score(self):
        ctx = planner.Ctx(planner.TripInput.from_dict(dict(KAZAN, wishes="люблю искусство")))
        plain = planner.Ctx(planner.TripInput.from_dict(dict(KAZAN)))
        khazine = ctx.place("khazine")
        self.assertEqual(planner.score(ctx, khazine), planner.score(plain, khazine) + 1.5)

    def test_local_food_preferred(self):
        def local_meals(**kw):
            trip = planner.plan_trip(dict(KAZAN, **kw))
            ctx = planner.trip_ctx(trip)
            return sum("local_food" in ctx.place(i["place_id"]).get("tags", [])
                       for i in items(trip) if i["kind"] == "food" and i.get("place_id"))
        self.assertGreater(local_meals(tags=["local_food"]), local_meals())

    def test_every_city_builds(self):
        for city in catalog.cities():
            for pace in planner.PACE_CAPACITY:
                for days in (1, 2, 4, 7):
                    for kids in ([], [4, 10]):
                        t = planner.plan_trip(dict(KAZAN, city=city, transport="auto", wishes="театр",
                                                   pace=pace, days=days, kids_ages=kids))
                        self.assertTrue(t["days"][0]["items"][2]["title"].endswith(f"→ {city}"), city)
                        for it in items(t):
                            self.assertLess(it["start"], 26 * 60, (city, pace, days, it["title"]))
                            if it["kind"] in ("food", "museum", "sight", "walk", "daytrip"):  # ночью — только отдых
                                self.assertLess(it["start"], planner.NIGHT, (city, pace, days, it["title"]))
                            if days > 1:  # за день до Казани поездом (12 ч) туда и обратно не успеть — это честно
                                self.assertNotIn("Не успеваете", " ".join(it["warnings"]), (city, pace, days, it["title"]))

    def test_night_arrival_only_check_in(self):
        # поезд 13:15 -> 01:00: в первый день после поезда — только заселение, без ужина в 03:45
        trip = planner.plan_trip(dict(KAZAN, transport="train", start_time="13:15", arrive_time="01:00", days=3))
        after = trip["days"][0]["items"][3:]
        self.assertEqual([i["kind"] for i in after], ["hotel"])
        self.assertEqual(after[0]["warnings"], [])
        self.assertEqual(trip["days"][1]["items"][0]["title"], "Подъём")

    def test_late_evening_arrival_no_rest_before_dinner(self):
        for arrive in ("19:30", "20:30"):
            trip = planner.plan_trip(dict(KAZAN, transport="train", start_time="09:00", arrive_time=arrive))
            day = trip["days"][0]["items"]
            self.assertFalse(any(i["title"] == "Возвращение, отдых" for i in day))
            for i in day:
                self.assertFalse(any(w.startswith("Закрывается") for w in i["warnings"]), (arrive, i["title"]))

    def test_auto_transport_flies_to_kaliningrad(self):
        trip = planner.plan_trip(dict(KAZAN, city="Калининград", transport="auto"))
        self.assertEqual(items(trip)[2]["title"], "Самолёт Москва → Калининград")
        trip = planner.plan_trip(dict(KAZAN, city="Нижний Новгород", transport="auto"))
        self.assertEqual(items(trip)[2]["title"], "Поезд «Ласточка» Москва → Нижний Новгород")

    def test_daytrip_and_theatre_on_different_days(self):
        trip = planner.plan_trip(dict(KAZAN, city="Калининград", days=4, wishes="театр, хотим на Куршскую косу"))
        kinds = [{i["kind"] for i in d["items"]} for d in trip["days"]]
        self.assertTrue(any("daytrip" in k for k in kinds))
        self.assertFalse(any({"daytrip", "theatre"} <= k for k in kinds))


class NightTrainTest(unittest.TestCase):
    NIGHT = dict(BASE, city="Казань", start_date="2026-11-20", transport="train", start_time="22:10",
                 arrive_time="09:05", return_time="21:00", wishes="")

    def test_first_day_is_only_the_road(self):
        trip = planner.plan_trip(dict(self.NIGHT))
        first, second = trip["days"][0]["items"], trip["days"][1]["items"]
        self.assertEqual([i["kind"] for i in first], ["travel"] * 3)
        self.assertIn("на следующий день", first[-1]["note"])
        # второй день — с вокзала, а не с подъёма в отеле; завтрак в кафе
        self.assertTrue(second[0]["title"].startswith("Прибытие"))
        self.assertEqual(planner.fmt_hm(second[0]["start"]), "09:05")
        self.assertNotIn("wake", [i.get("stay_role") for i in second])
        self.assertIn("breakfast", [i.get("meal") for i in second])
        # жильё — на одну ночь: ночь в поезде не оплачиваем
        ctx = planner.trip_ctx(trip)
        self.assertEqual(ctx.nights, 1)
        hotel = next(i for i in second if i.get("stay_role") == "checkin")
        self.assertEqual(hotel["cost"]["estimate"], planner.stay_estimate(ctx, ctx.place(hotel["place_id"])))
        self.assertEqual(planner.suggestions(trip, 0), [])  # в поезд места не добавляем

    def test_two_days_without_hotel(self):
        trip = planner.plan_trip(dict(self.NIGHT, days=2))
        second = trip["days"][1]["items"]
        self.assertFalse(any(i["kind"] == "hotel" for i in second))
        self.assertTrue(second[-1]["title"].startswith("Поезд Казань → Москва"))

    def test_day_train_is_not_night(self):
        ctx = planner.trip_ctx(planner.plan_trip(dict(self.NIGHT, start_time="07:00", arrive_time="18:00")))
        self.assertFalse(ctx.night_out)
        self.assertEqual(ctx.nights, 2)


class TimeZoneTest(unittest.TestCase):
    def test_kaliningrad_local_time(self):
        # Калининград на час позади Москвы: вылет 10:00 по Москве, посадка 11:30 по местному — в пути 2 ч 30 мин
        trip = planner.plan_trip(dict(BASE, city="Калининград", transport="plane", start_time="10:00",
                                      arrive_time="11:30", return_time="18:00", return_arrive_time="21:30", wishes=""))
        out = next(i for i in trip["days"][0]["items"] if i["kind"] == "travel" and i["cost"]["kind"] == "ticket")
        self.assertEqual(out["duration"], 150)
        self.assertEqual(planner.fmt_hm(out["end"]), "11:30")
        self.assertIn("время прибытия местное", out["note"])
        back = next(i for i in trip["days"][-1]["items"] if i["kind"] == "travel" and i["cost"]["kind"] == "ticket")
        self.assertEqual(back["duration"], 150)  # 18:00 местного = 19:00 по Москве, прилёт 21:30
        self.assertEqual(planner.fmt_hm(back["end"]), "21:30")



class VyborgTest(unittest.TestCase):
    def test_from_petersburg_by_lastochka(self):
        trip = planner.plan_trip(dict(BASE, origin="Санкт-Петербург", city="Выборг", days=2, start_date="2026-11-21",
                                      transport="auto", wishes=""))
        legs = [i for d in trip["days"] for i in d["items"] if i["kind"] == "travel" and i["cost"]["kind"] == "ticket"]
        self.assertEqual([i["duration"] for i in legs], [80, 80])
        self.assertIn("Ласточка", legs[0]["title"])
        # в маленьком городе обедаем каждый день в новом месте
        lunches = [i["place_id"] for d in trip["days"] for i in d["items"] if i.get("meal") == "lunch"]
        self.assertEqual(len(lunches), len(set(lunches)))
        self.assertTrue(lunches)


if __name__ == "__main__":
    unittest.main()
