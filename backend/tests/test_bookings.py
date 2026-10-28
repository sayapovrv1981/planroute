import os
import unittest
from unittest import mock

from app import bookings, planner


def tiny_pdf(text: str) -> bytes:
    """Минимальный PDF с одной строкой латиницы — pypdf достаёт из него текст."""
    stream = f"BT /F1 12 Tf 40 760 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


TRAIN = {"kind": "train", "title": "Поезд 752А Москва → Санкт-Петербург", "date": "2026-11-03", "start": "7:00",
         "end": "10:55", "end_date": "", "number": "752А", "from_city": "Москва", "to_city": "Санкт-Петербург",
         "venue": "", "address": "", "price": 9840.0, "persons": 2}
BALLET = {"kind": "theatre", "title": "Мариинский театр: Лебединое озеро", "date": "2026-11-03", "start": "19:00",
          "end": "", "end_date": "", "number": "", "from_city": "", "to_city": "", "venue": "Мариинский театр",
          "address": "Театральная площадь, 1", "price": 9000, "persons": 2}


class BookingsTest(unittest.TestCase):
    def test_text_of(self):
        self.assertIn("Train 752A", bookings.text_of("ticket.pdf", tiny_pdf("Train 752A 2026-11-03 07:00")))
        self.assertEqual(bookings.text_of("letter.html", "<p>Билет <b>752А</b></p>".encode()), "Билет 752А")
        self.assertEqual(bookings.text_of("photo.jpg", b"\xff\xd8\xff"), "")  # фото — нужен OCR
        with self.assertRaises(ValueError):
            bookings.text_of("broken.pdf", b"%PDF-1.4 garbage")

    @mock.patch.dict(os.environ, {"YANDEX_VISION_API_KEY": "v", "YANDEX_FOLDER_ID": "b1g", "YANDEX_VISION_FOLDER_ID": ""})
    def test_ocr_for_photos_and_scans(self):
        import base64
        import json

        answer = {"result": {"textAnnotation": {"fullText": "Поезд 752А 03.11.2026 07:00"}}}
        with mock.patch.object(bookings, "_post_json", return_value=answer) as post:
            self.assertEqual(bookings.text_of("ticket.JPG", b"\xff\xd8\xff jpeg"), "Поезд 752А 03.11.2026 07:00")
        req = post.call_args.args[0]
        body = json.loads(req.data)
        self.assertEqual((body["mimeType"], body["model"], body["languageCodes"]), ("JPEG", "page", ["ru", "en"]))
        self.assertEqual(base64.b64decode(body["content"]), b"\xff\xd8\xff jpeg")
        self.assertEqual((req.get_header("Authorization"), req.get_header("X-folder-id")), ("Api-Key v", "b1g"))
        # PDF без текстового слоя (скан) — тоже через OCR; с текстом — без него
        with mock.patch.object(bookings, "_post_json", return_value=answer) as post:
            self.assertEqual(bookings.text_of("scan.pdf", tiny_pdf("")), "Поезд 752А 03.11.2026 07:00")
            self.assertEqual(json.loads(post.call_args.args[0].data)["mimeType"], "PDF")
            bookings.text_of("ticket.pdf", tiny_pdf("Train 752A 2026-11-03 07:00 Moscow"))
            self.assertEqual(post.call_count, 1)
            self.assertEqual(bookings.text_of("photo.webp", b"RIFF"), "")  # формат, который OCR не берёт
        with mock.patch.dict(os.environ, {"YANDEX_VISION_API_KEY": ""}):
            self.assertEqual(bookings.text_of("ticket.png", b"\x89PNG"), "")

    def test_clean_and_role(self):
        b = bookings.clean(TRAIN)
        self.assertEqual((b["start"], b["price"], b["persons"]), ("07:00", 9840, 2))
        self.assertIsNone(bookings.clean(dict(TRAIN, date="03.11.2026")))  # без ISO-даты в программу не поставить
        self.assertEqual(bookings.clean(dict(TRAIN, kind="ракета"))["kind"], "other")
        self.assertEqual(bookings.role(b, "Москва", "Санкт-Петербург"), "out")
        back = dict(b, from_city="СПб", to_city="Москва")
        self.assertEqual(bookings.role(back, "Москва", "Санкт-Петербург"), "back")
        self.assertEqual(bookings.role(bookings.clean(BALLET), "Москва", "Санкт-Петербург"), "event")

    @mock.patch.dict(os.environ, {"YANDEX_GPT_API_KEY": "k", "YANDEX_FOLDER_ID": "f", "LLM_PROVIDER": ""})
    def test_parse_and_locate(self):
        with mock.patch("app.wishes_ai.ask", return_value={"bookings": [TRAIN, BALLET, dict(BALLET, date="")]}) as ask:
            got = bookings.parse("билеты", "Москва", "Санкт-Петербург")
        self.assertEqual([b["role"] for b in got], ["out", "event"])  # без даты — пропустили
        self.assertEqual(got[1]["place_id"], "mariinsky")  # театр из каталога — его координаты
        self.assertIn("Москва → Санкт-Петербург", ask.call_args.args[1])
        with mock.patch.dict(os.environ, {"YANDEX_GPT_API_KEY": ""}), self.assertRaises(ValueError):
            bookings.parse("билеты", "Москва", "Санкт-Петербург")

    def test_event_in_program(self):
        e = bookings.event_place(dict(bookings.clean(BALLET), lat=59.9257, lon=30.2961, place_id="mariinsky"), 0, [59.9, 30.3])
        self.assertEqual((e["category"], e["duration"], e["start"], e["price_group"]), ("theatre", 180, "19:00", 9000))
        museum = bookings.event_place(dict(bookings.clean(dict(BALLET, kind="museum", title="Эрмитаж", date="2026-11-04",
                                                                start="11:00", end="13:30")), place_id="hermitage"), 1, [59.9, 30.3])
        base = {"origin": "Москва", "city": "Санкт-Петербург", "start_date": "2026-11-03", "end_date": "2026-11-05",
                "start_time": "07:00", "arrive_time": "11:00", "return_time": "19:00", "transport": "train", "adults": 2}
        trip = planner.plan_trip(base, None, [e, museum])
        items = [i for d in trip["days"] for i in d["items"]]
        ballet = next(i for i in items if i["place_id"] == "event-0")
        self.assertEqual((planner.fmt_hm(ballet["start"]), ballet["kind"], ballet["warnings"]), ("19:00", "theatre", []))
        ermitage = next(i for i in items if i["place_id"] == "event-1")
        self.assertEqual((planner.fmt_hm(ermitage["start"]), ermitage["duration"]), ("11:00", 150))
        self.assertEqual(ermitage["alternatives"], [])  # билет — не заменить
        self.assertNotIn("hermitage", {i["place_id"] for i in items})  # второй раз в Эрмитаж не ведём
        self.assertEqual(sum(i["kind"] == "theatre" for i in items), 1)  # свой билет вместо подбора театра
        # бронь не на даты поездки — в список пропущенных
        trip = planner.plan_trip(base, None, [dict(e, date="2026-12-01")])
        self.assertEqual(trip["summary"]["skipped_bookings"], [e["name"]])


if __name__ == "__main__":
    unittest.main()
