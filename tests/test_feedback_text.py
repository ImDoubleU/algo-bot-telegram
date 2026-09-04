import os
import unittest
from datetime import datetime

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:UNIT_TEST_TOKEN")

import bot
from app.core.feedback import FeedbackService
from core.feedback_text import MAX_BOT_URL


EXPECTED_TEXT = (
    "Количество астрокоинов, а также куда их потратить, "
    "можно посмотреть в боте Max"
)
OLD_URL = "algoritmika52.ru"


class FeedbackTextTests(unittest.TestCase):
    def assert_has_max_bot_block(self, text: str) -> None:
        self.assertIn(EXPECTED_TEXT, text)
        self.assertIn(MAX_BOT_URL, text)
        self.assertNotIn(OLD_URL, text)

    def test_telegram_feedback_variants_have_max_bot_link(self):
        args = ("Урок", 1, datetime(2026, 9, 4), {"educational_results": "Итоги"})

        variants = (
            bot.format_feedback(*args),
            bot.format_feedback_with_absent_students(*args, absent_students=["Иван"]),
            bot.format_feedback_online_individual(*args),
        )

        for text in variants:
            with self.subTest(text=text[:40]):
                self.assert_has_max_bot_block(text)

    def test_service_feedback_variants_have_max_bot_link(self):
        service = FeedbackService(courses_service=None)
        args = ("Урок", 1, datetime(2026, 9, 4), {"educational_results": "Итоги"})

        variants = (
            service.format_feedback(*args),
            service.format_feedback_with_absent_students(*args, absent_students=["Иван"]),
            service.format_feedback_online_individual(*args),
        )

        for text in variants:
            with self.subTest(text=text[:40]):
                self.assert_has_max_bot_block(text)


if __name__ == "__main__":
    unittest.main()
