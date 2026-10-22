"""ai_parser 的测试：规则备选模式 + 后端校验。不调用真实的 AI，也不需要 API 密钥。

运行（在项目根目录）：python -m unittest discover tests
"""
import json
import os
import sys
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import ai_parser  # noqa: E402

TODAY = date(2026, 10, 21)  # 星期三，和演示起始日一样
ORDER = {"id": 2, "supplier": "Segar Sayur Sdn Bhd", "product": "青菜", "quantity": 15, "unit": "箱",
         "delivery_date": "2026-10-22"}

with open(os.path.join(ROOT, "data", "sample_replies.json"), encoding="utf-8") as f:
    SAMPLES = json.load(f)


FAKE_AI_ENV = {"AI_BASE_URL": "http://localhost:9/v1", "AI_API_KEY": "test-key-not-real",
               "AI_MODEL": "test-model"}


def without_api_key():
    """在测试里临时清空 AI_* 环境变量，确保走规则模式。"""
    return mock.patch.dict(os.environ, {name: "" for name in ai_parser.AI_ENV_VARS})


class RuleModeTest(unittest.TestCase):

    def parse(self, text):
        with without_api_key():
            return ai_parser.parse_reply(text, ORDER, TODAY)

    def test_no_api_key_uses_rule_mode(self):
        parsed = self.parse("Ok boss, 明天 sampai on time")
        self.assertEqual(parsed["mode"], "rules")
        self.assertIn("规则模式", parsed["mode_note"])
        self.assertIn("AI_API_KEY", parsed["mode_note"])  # 告诉用户缺的是哪个变量（只有名字，不是值）

    def test_partly_configured_still_uses_rule_mode(self):
        env = dict(FAKE_AI_ENV, AI_MODEL="")
        with mock.patch.dict(os.environ, env):
            parsed = ai_parser.parse_reply("Ok boss", ORDER, TODAY)
        self.assertEqual(parsed["mode"], "rules")
        self.assertIn("AI_MODEL", parsed["mode_note"])

    def test_every_sample_reply_gets_the_expected_intent(self):
        for sample in SAMPLES:
            with self.subTest(sample=sample["label"]):
                self.assertEqual(self.parse(sample["text"])["result"]["intent"], sample["expected_intent"])

    def test_delay_converts_jumaat_to_next_friday(self):
        r = self.parse("Bos, sorry ya hari ni tak dapat hantar. Jumaat baru sampai, ok?")["result"]
        self.assertEqual(r["intent"], "delay")
        self.assertEqual(r["new_delivery_date"], "2026-10-23")  # 10-21 是星期三，下一个星期五是 10-23

    def test_weekday_equal_to_today_means_next_week(self):
        self.assertEqual(ai_parser.find_date("rabu baru sampai", TODAY), "2026-10-28")  # 今天就是星期三

    def test_relative_and_explicit_dates(self):
        self.assertEqual(ai_parser.find_date("esok", TODAY), "2026-10-22")
        self.assertEqual(ai_parser.find_date("后天送", TODAY), "2026-10-23")
        self.assertEqual(ai_parser.find_date("hantar 25/10", TODAY), "2026-10-25")
        self.assertEqual(ai_parser.find_date("2026-11-02 sampai", TODAY), "2026-11-02")
        self.assertIsNone(ai_parser.find_date("nanti saya check", TODAY))

    def test_partial_extracts_quantity(self):
        r = self.parse("Hi boss, stok tak cukup, only 13 kotak boleh hantar esok")["result"]
        self.assertEqual(r["intent"], "partial")
        self.assertEqual(r["quantity"], 13)

    def test_price_change_extracts_price(self):
        r = self.parse("Boss, harga minyak naik lah, 每桶 RM 58.50 mulai minggu ni")["result"]
        self.assertEqual(r["intent"], "price_change")
        self.assertEqual(r["unit_price"], 58.5)

    def test_unclear_reply_needs_review(self):
        parsed = self.parse("Boss tu macam mana ya... 我问一下 my brother dulu")
        self.assertEqual(parsed["result"]["intent"], "unclear")
        self.assertTrue(parsed["needs_review"])

    def test_delay_without_date_is_low_confidence(self):
        parsed = self.parse("sorry boss lambat sikit")
        self.assertEqual(parsed["result"]["intent"], "delay")
        self.assertIsNone(parsed["result"]["new_delivery_date"])
        self.assertTrue(parsed["needs_review"])  # 把握度 0.5 < 0.6，而且缺日期

    def test_empty_and_too_long_replies(self):
        self.assertTrue(self.parse("   ")["needs_review"])
        self.assertTrue(self.parse("x" * 1001)["needs_review"])


class ValidationTest(unittest.TestCase):
    """后端校验：不管结果来自 AI 还是规则，格式不对都要拦下来。"""

    GOOD = {"intent": "delay", "new_delivery_date": "2026-10-23", "quantity": None, "unit_price": None,
            "confidence": 0.9, "summary_en": "Delivery moved to Friday."}

    def test_good_result_passes(self):
        clean, problems = ai_parser.validate(dict(self.GOOD))
        self.assertEqual(problems, [])
        self.assertEqual(clean["new_delivery_date"], "2026-10-23")

    def test_bad_fields_are_reported_and_cleared(self):
        bad = dict(self.GOOD, intent="cancel", new_delivery_date="Friday", quantity=-3,
                   unit_price="58", confidence=1.7, summary_en="")
        clean, problems = ai_parser.validate(bad)
        self.assertEqual(clean["intent"], "unclear")
        self.assertIsNone(clean["new_delivery_date"])
        self.assertIsNone(clean["quantity"])
        self.assertIsNone(clean["unit_price"])
        self.assertEqual(clean["confidence"], 0.0)
        self.assertEqual(len(problems), 6)

    def test_intent_without_its_field_is_a_problem(self):
        _, problems = ai_parser.validate(dict(self.GOOD, new_delivery_date=None))
        self.assertTrue(any("缺少 new_delivery_date" in p for p in problems))

    def test_not_a_dict(self):
        clean, problems = ai_parser.validate(["delay"])
        self.assertEqual(clean["intent"], "unclear")
        self.assertTrue(problems)


class AIModeFallbackTest(unittest.TestCase):
    """AI 模式的兜底：用假的模型调用代替真实 API，不花钱、不需要网络。"""

    def parse_with_fake_model(self, fake):
        with mock.patch.dict(os.environ, FAKE_AI_ENV), mock.patch.object(ai_parser, "_call_model", fake):
            return ai_parser.parse_reply("Jumaat baru sampai", ORDER, TODAY)

    def test_good_ai_result_is_used(self):
        parsed = self.parse_with_fake_model(lambda *a: dict(ValidationTest.GOOD))
        self.assertEqual(parsed["mode"], "ai")
        self.assertIn("test-model", parsed["mode_note"])
        self.assertFalse(parsed["needs_review"])

    def test_timeout_falls_back_to_rules(self):
        class APITimeoutError(Exception):  # 和 openai 库里超时异常同名
            pass

        def slow(*args):
            raise APITimeoutError("15 seconds passed")
        parsed = self.parse_with_fake_model(slow)
        self.assertEqual(parsed["mode"], "rules")
        self.assertIn("超时", parsed["mode_note"])

    def test_low_confidence_ai_result_needs_review(self):
        parsed = self.parse_with_fake_model(lambda *a: dict(ValidationTest.GOOD, confidence=0.4))
        self.assertTrue(parsed["needs_review"])

    def test_malformed_ai_result_needs_review(self):
        parsed = self.parse_with_fake_model(lambda *a: dict(ValidationTest.GOOD, new_delivery_date="next Friday"))
        self.assertTrue(parsed["needs_review"])

    def test_api_error_falls_back_to_rules(self):
        def broken(*args):
            raise ConnectionError("network down")
        parsed = self.parse_with_fake_model(broken)
        self.assertEqual(parsed["mode"], "rules")
        self.assertIn("AI 调用失败", parsed["mode_note"])
        self.assertEqual(parsed["result"]["new_delivery_date"], "2026-10-23")  # 规则模式照样能读出日期


if __name__ == "__main__":
    unittest.main()
