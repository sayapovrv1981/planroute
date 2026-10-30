import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import unittest
from unittest import mock

from app import planner, trip_ai

KAZAN = {"origin": "Москва", "city": "Казань", "start_date": "2026-11-20", "start_time": "07:00", "return_time": "20:00",
         "days": 3, "transport": "plane", "adults": 2, "kids_ages": [], "budget": 120000, "pace": "balanced",
         "wishes": ""}


def items(trip):
    return [it for d in trip["days"] for it in d["items"]]


def first(trip, kinds, day=None):
    days = trip["days"] if day is None else [trip["days"][day]]
    return next(it for d in days for it in d["items"] if it["kind"] in kinds)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.trip = planner.plan_trip(dict(KAZAN))

    def test_fixed_and_unknown_items_are_not_touched(self):
        road = first(self.trip, ("travel",))
        ok, skipped = trip_ai.check(self.trip, [{"op": "remove", "item": road["id"]},
                                                {"op": "remove", "item": "нет-такого"}])
        self.assertEqual(ok, [])
        self.assertEqual(len(skipped), 1)  # про дорогу — объяснение, неизвестный пункт — молча

    def test_food_only_for_food_and_no_duplicates(self):
        lunch = next(it for it in items(self.trip) if it.get("meal") == "lunch")
        museum = first(self.trip, ("museum",))
        used = museum["place_id"]
        ok, skipped = trip_ai.check(self.trip, [{"op": "replace", "item": museum["id"], "place": "f_tubetey"},
                                                {"op": "replace", "item": lunch["id"], "place": used}])
        self.assertEqual(ok, [])
        self.assertEqual(len(skipped), 2)

    def test_duration_rounded_to_step(self):
        museum = first(self.trip, ("museum",))
        ok, _ = trip_ai.check(self.trip, [{"op": "duration", "item": museum["id"], "minutes": 47}])
        self.assertEqual(ok[0]["minutes"], 45)
        self.assertIn("45 мин", ok[0]["label"])

    def test_dinner_to_theatre_becomes_add(self):
        dinner = next(it for it in items(self.trip) if it.get("meal") == "dinner")
        ok, _ = trip_ai.check(self.trip, [{"op": "replace", "item": dinner["id"], "place": "kamal"}])
        self.assertEqual(ok[0]["op"], "add")


class ApplyTest(unittest.TestCase):
    def test_theatre_on_time_with_light_dinner(self):
        trip = planner.plan_trip(dict(KAZAN))
        trip, applied, skipped, _ = trip_ai.apply(trip, [{"op": "add", "day": 1, "place": "kamal"},
                                                         {"op": "add", "day": 1, "place": "opera"}])
        d = trip["days"][0]["items"]
        show = next(it for it in d if it["place_id"] == "kamal")
        self.assertEqual(planner.fmt_hm(show["start"]), "18:30")
        self.assertFalse(show["warnings"])
        self.assertEqual(show["why"], ["по вашей просьбе"])
        dinner = d[d.index(show) - 1]
        self.assertEqual(dinner["meal"], "dinner_light")
        self.assertFalse(any(it["title"].startswith("Вечерняя прогулка") for it in d))
        self.assertTrue(any("уже есть спектакль" in s for s in skipped))  # второй театр в тот же день — нет
        self.assertTrue(applied)

    def test_remove_and_replace(self):
        trip = planner.plan_trip(dict(KAZAN))
        museum = first(trip, ("museum",))
        lunch = next(it for it in items(trip) if it.get("meal") == "lunch")
        new = next(a for a in planner.trip_ctx(trip).places("food")
                   if a["id"] not in {it.get("place_id") for it in items(trip)} and "lunch" in a.get("meal", []))
        trip, applied, skipped, removed = trip_ai.apply(trip, [{"op": "remove", "item": museum["id"]},
                                                               {"op": "replace", "item": lunch["id"], "place": new["id"]}])
        self.assertEqual(len(applied), 2, skipped)
        self.assertEqual(removed[0]["id"], museum["id"])
        self.assertNotIn(museum["id"], [it["id"] for it in items(trip)])
        self.assertEqual(lunch["place_id"], new["id"])


class ProposeTest(unittest.TestCase):
    def test_model_answer_is_checked(self):
        trip = planner.plan_trip(dict(KAZAN))
        museum = first(trip, ("museum",))
        answer = {"actions": [{"op": "remove", "item": museum["id"], "place": "", "day": 0, "minutes": 0},
                              {"op": "remove", "item": "выдумано", "place": "", "day": 0, "minutes": 0}],
                  "summary": "Убрать музей", "unclear": ""}
        with mock.patch.object(trip_ai.wishes_ai, "available", return_value=True), \
                mock.patch.object(trip_ai.wishes_ai, "ask", return_value=answer) as ask:
            r = trip_ai.propose(trip, "убери музей")
        self.assertEqual([c["item"] for c in r["changes"]], [museum["id"]])
        self.assertEqual(r["summary"], "Убрать музей")
        user = ask.call_args.args[1]
        self.assertIn(f"[{museum['id']}]", user)
        self.assertNotIn(f"\n{museum['place_id']}:", user)  # места из программы в каталоге не предлагаем

    def test_without_model(self):
        trip = planner.plan_trip(dict(KAZAN))
        with mock.patch.object(trip_ai.wishes_ai, "available", return_value=False):
            with self.assertRaises(ValueError):
                trip_ai.propose(trip, "добавь театр")


if __name__ == "__main__":
    unittest.main()
