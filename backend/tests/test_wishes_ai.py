import testdb  # noqa: F401 — первым: временный PostgreSQL вместо настоящей базы
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from app import planner, wishes_ai

BASE = {"origin": "Москва", "city": "Казань", "start_date": "2026-10-27", "start_time": "07:00", "return_time": "19:00",
        "days": 3, "transport": "plane", "adults": 2, "kids_ages": [], "budget": 100000, "pace": "balanced"}
YANDEX_ENV = {"YANDEX_GPT_API_KEY": "key", "YANDEX_FOLDER_ID": "b1gfolder", "YANDEX_GPT_MODEL": "",
              "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "", "LLM_PROVIDER": ""}


def settings(**kw):
    out = {"interests": [], "wishes": [], "avoid": [], "must_visit": [], "skip_places": [], "pace": "", "summary": "Понятно"}
    out.update(kw)
    return out


def yandex_answer(status="ALTERNATIVE_STATUS_FINAL", **kw):
    return {"result": {"alternatives": [{"message": {"role": "assistant", "text": json.dumps(settings(**kw))},
                                         "status": status}], "modelVersion": "test"}}


def items(trip):
    return [it for d in trip["days"] for it in d["items"]]


@mock.patch.dict(os.environ, YANDEX_ENV)
class YandexGptTest(unittest.TestCase):
    def build(self, text, answer, **kw):
        with mock.patch.object(wishes_ai, "_post_json", return_value=answer) as post:
            body = wishes_ai.apply(dict(BASE, wishes=text, **kw))
        self.post = post
        return body, planner.plan_trip(body)

    def test_request_shape(self):
        body, _ = self.build("хотим в Свияжск", yandex_answer(must_visit=["sviyazhsk"]))
        req = self.post.call_args.args[0]
        self.assertEqual(req.full_url, "https://ai.api.cloud.yandex.net/foundationModels/v1/completion")
        self.assertEqual(req.get_header("Authorization"), "Api-Key key")
        sent = json.loads(req.data)
        self.assertEqual(sent["modelUri"], "gpt://b1gfolder/yandexgpt/latest")
        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user"])
        self.assertIn("хотим в Свияжск", sent["messages"][1]["text"])
        enum = sent["jsonSchema"]["schema"]["properties"]["must_visit"]["items"]["enum"]
        self.assertIn("sviyazhsk", enum)  # только места этого города
        self.assertNotIn("hermitage", enum)
        self.assertEqual(body["wishes_ai"]["source"], "yandex")

    def test_negation_and_avoid(self):
        # по ключевым словам «без театра» включило бы театр, а «без музеев» не поняло бы вовсе
        body, trip = self.build("без театра и без музеев, побольше гулять", yandex_answer(avoid=["theatre", "museum"]))
        kinds = {i["kind"] for i in items(trip)}
        self.assertNotIn("theatre", kinds)
        self.assertNotIn("museum", kinds)
        self.assertIn("walk", kinds)
        self.assertEqual(trip["summary"]["wishes_note"], "Понятно.")

    def test_must_and_skip_places(self):
        _, trip = self.build("в Свияжск не поедем, хотим в Раифу",
                             yandex_answer(must_visit=["raifa"], skip_places=["sviyazhsk"]))
        ids = {i["place_id"] for i in items(trip)}
        self.assertIn("raifa", ids)
        self.assertNotIn("sviyazhsk", ids)

    def test_named_theatre(self):
        _, trip = self.build("хотим на балет в театр Джалиля", yandex_answer(wishes=["theatre", "opera"], must_visit=["opera"]))
        self.assertIn("opera", {i["place_id"] for i in items(trip) if i["kind"] == "theatre"})

    def test_invalid_values_dropped(self):
        body, _ = self.build("что-то", yandex_answer(interests=["history", "космос"], must_visit=["hermitage", "kremlin"],
                                                     pace="очень быстро"))
        ai = body["wishes_ai"]
        self.assertEqual(ai["interests"], ["history"])
        self.assertEqual(ai["must_visit"], ["kremlin"])  # Эрмитажа в Казани нет
        self.assertEqual(ai["pace"], "")

    def test_ballet_beats_no_theatre(self):
        body, trip = self.build("хотим на балет", yandex_answer(wishes=["opera"], avoid=["theatre", "museum"]))
        self.assertEqual(body["wishes_ai"]["avoid"], ["museum"])  # противоречие в ответе модели — просьба важнее
        self.assertIn("theatre", {i["kind"] for i in items(trip)})

    def test_pace_only_over_default(self):
        body, trip = self.build("хочется спокойно", yandex_answer(pace="calm"))
        self.assertEqual(body["pace"], "calm")
        self.assertIn("выбран по вашим словам", trip["summary"]["wishes_note"])
        body, _ = self.build("хочется спокойно", yandex_answer(pace="calm"), **{"pace": "max"})
        self.assertEqual(body["pace"], "max")  # выбранный в форме темп не трогаем

    def test_reuse_for_same_text(self):
        body, _ = self.build("хотим в Свияжск", yandex_answer(must_visit=["sviyazhsk"]))
        with mock.patch.object(wishes_ai, "_post_json") as post:
            again = wishes_ai.apply(dict(body))
        post.assert_not_called()
        self.assertEqual(again["wishes_ai"]["must_visit"], ["sviyazhsk"])
        with mock.patch.object(wishes_ai, "_post_json", return_value=yandex_answer()) as post:
            wishes_ai.apply(dict(body, wishes="другой текст"))
        post.assert_called_once()

    def test_falls_back_to_keywords(self):
        body, trip = self.build("хотим в Свияжск", yandex_answer(status="ALTERNATIVE_STATUS_CONTENT_FILTER"))
        self.assertIsNone(body["wishes_ai"])
        self.assertIsNone(trip["summary"]["wishes_note"])
        self.assertIn("sviyazhsk", {i["place_id"] for i in items(trip)})  # сработал шаблон keywords
        with mock.patch.object(wishes_ai, "_post_json", side_effect=OSError("нет сети")):
            body = wishes_ai.apply(dict(BASE, wishes="хотим в Свияжск"))
        self.assertIsNone(body["wishes_ai"])
        bad = {"result": {"alternatives": [{"message": {"text": "не JSON"}, "status": "ALTERNATIVE_STATUS_FINAL"}]}}
        with mock.patch.object(wishes_ai, "_post_json", return_value=bad):
            body = wishes_ai.apply(dict(BASE, wishes="хотим в Свияжск"))
        self.assertIsNone(body["wishes_ai"])

    def test_without_keys_no_call(self):
        with mock.patch.dict(os.environ, {"YANDEX_GPT_API_KEY": ""}), mock.patch.object(wishes_ai, "_post_json") as post:
            body = wishes_ai.apply(dict(BASE, wishes="хотим в Свияжск"))
        post.assert_not_called()
        self.assertIsNone(body["wishes_ai"])

    def test_stale_result_ignored_by_planner(self):
        body, _ = self.build("без музеев", yandex_answer(avoid=["museum"]))
        trip = planner.plan_trip(dict(body, wishes="музеи любим"))  # текст поменяли в обход apply
        self.assertIn("museum", {i["kind"] for i in items(trip)})


@mock.patch.dict(os.environ, dict(YANDEX_ENV, ANTHROPIC_API_KEY="test", LLM_PROVIDER="anthropic"))
class ClaudeTest(unittest.TestCase):
    def test_provider_choice_and_request(self):
        self.assertEqual(wishes_ai.provider(), "anthropic")
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": ""}):
            self.assertEqual(wishes_ai.provider(), "yandex")  # по умолчанию — YandexGPT
        client = mock.MagicMock()
        client.beta.messages.create.return_value = SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(settings(avoid=["museum"])))])
        with mock.patch.object(wishes_ai, "_anthropic_client", return_value=client):
            body = wishes_ai.apply(dict(BASE, wishes="без музеев"))
        kw = client.beta.messages.create.call_args.kwargs
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["fallbacks"], "default")
        self.assertEqual(body["wishes_ai"]["avoid"], ["museum"])
        self.assertEqual(body["wishes_ai"]["source"], "anthropic")


if __name__ == "__main__":
    unittest.main()
