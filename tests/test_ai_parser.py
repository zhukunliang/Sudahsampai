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
        self.assertEqual(parsed["mode_note"], "Offline rules (AI unavailable)")
        self.assertNotIn("AI_", parsed["mode_note"] + parsed["mode_reason"])  # 页面和日志都不出现环境变量名

    def test_partly_configured_still_uses_rule_mode(self):
        env = dict(FAKE_AI_ENV, AI_MODEL="")
        with mock.patch.dict(os.environ, env):
            parsed = ai_parser.parse_reply("Ok boss", ORDER, TODAY)
        self.assertEqual(parsed["mode"], "rules")
        self.assertEqual(parsed["mode_reason"], "AI not configured")

    # ---- 这次修复的 bug：以前被判成 confirm，日期和数量都是空的 ----

    def test_bug_report_message_is_partial_with_date_and_quantity(self):
        parsed = self.parse("Boss sorry ya, lori rosak. Jumaat baru boleh hantar, 12 kotak je dulu")
        r = parsed["result"]
        self.assertEqual(r["intent"], "partial")           # 有"je"、12 < 15 → 少货；"baru boleh hantar" → 也延期
        self.assertEqual(r["quantity"], 12)
        self.assertEqual(r["new_delivery_date"], "2026-10-23")  # Jumaat → 10-21 之后的星期五
        self.assertGreaterEqual(r["confidence"], 0.6)
        self.assertFalse(parsed["needs_review"])

    # ---- data/sample_replies.json 的 5 条样例 ----

    def test_every_sample_reply_gets_the_expected_intent(self):
        for sample in SAMPLES:
            with self.subTest(sample=sample["label"]):
                self.assertEqual(self.parse(sample["text"])["result"]["intent"], sample["expected_intent"])

    def test_sample_values(self):
        by_intent = {s["expected_intent"]: self.parse(s["text"]) for s in SAMPLES}
        self.assertEqual(by_intent["delay"]["result"]["new_delivery_date"], "2026-10-23")
        self.assertEqual(by_intent["partial"]["result"]["quantity"], 13)
        self.assertEqual(by_intent["price_change"]["result"]["unit_price"], 58.0)
        self.assertFalse(by_intent["confirm"]["needs_review"])
        self.assertLess(by_intent["unclear"]["result"]["confidence"], 0.6)
        self.assertTrue(by_intent["unclear"]["needs_review"])
        for intent in ("delay", "partial", "price_change", "confirm"):
            self.assertFalse(by_intent[intent]["needs_review"], intent)

    # ---- "确认"只有在没有其他信号时才成立 ----

    def test_confirm_word_with_delay_signal_is_not_confirm(self):
        r = self.parse("Ok boss, Jumaat baru boleh hantar")["result"]
        self.assertEqual(r["intent"], "delay")

    def test_confirm_word_with_price_signal_is_price_change(self):
        r = self.parse("ok boss, harga baru RM 60 ya")["result"]
        self.assertEqual(r["intent"], "price_change")
        self.assertEqual(r["unit_price"], 60.0)

    def test_confirm_word_with_smaller_quantity_is_partial(self):
        r = self.parse("ok, 10 箱 tomorrow")["result"]  # 订单 15 箱，只送 10 箱
        self.assertEqual(r["intent"], "partial")
        self.assertEqual(r["quantity"], 10)

    def test_later_date_alone_counts_as_delay(self):
        r = self.parse("ok boss, Jumaat sampai")["result"]  # 原定 10-22，说的 10-23 更晚
        self.assertEqual(r["intent"], "delay")

    def test_plain_confirm(self):
        for text in ("Ok boss, no problem", "准时送到，没问题", "boleh hantar macam biasa"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text)["result"]["intent"], "confirm")

    def test_no_confirm_word_and_no_signal_is_unclear(self):
        for text in ("boleh hantar", "nanti saya check dulu", "jemput datang kedai"):  # "je" 不能误中 "jemput"
            with self.subTest(text=text):
                parsed = self.parse(text)
                self.assertEqual(parsed["result"]["intent"], "unclear")
                self.assertLess(parsed["result"]["confidence"], 0.6)
                self.assertTrue(parsed["needs_review"])

    def test_more_keywords(self):
        cases = {
            "sorry boss, 改天再送, Khamis boleh": ("delay", "new_delivery_date", "2026-10-22"),
            "晚点送, 星期六": ("delay", "new_delivery_date", "2026-10-24"),
            "stok kurang, 5 botol sahaja": ("partial", "quantity", 5),
            "先送 8 box": ("partial", "quantity", 8),
            "naik harga sikit boss": ("price_change", "unit_price", None),
            "涨价了 RM 12.5 一箱": ("price_change", "unit_price", 12.5),
        }
        for text, (intent, field, value) in cases.items():
            with self.subTest(text=text):
                r = self.parse(text)["result"]
                self.assertEqual(r["intent"], intent)
                self.assertEqual(r[field], value)

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
        self.assertEqual(parsed["mode_note"], "AI: test-model")  # 认不出服务商时只显示模型名
        self.assertFalse(parsed["needs_review"])

    def test_deepseek_label(self):
        env = dict(FAKE_AI_ENV, AI_BASE_URL="https://api.deepseek.com")
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(ai_parser, "_call_model", lambda *a: dict(ValidationTest.GOOD)):
            parsed = ai_parser.parse_reply("Jumaat baru sampai", ORDER, TODAY)
        self.assertEqual(parsed["mode_note"], "AI: test-model (DeepSeek)")

    def test_runpod_label_has_no_url_or_key(self):
        env = dict(FAKE_AI_ENV, AI_BASE_URL="https://api.runpod.ai/v2/abc123/openai/v1", AI_MODEL="glm-5.3")
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(ai_parser, "_call_model", lambda *a: dict(ValidationTest.GOOD)):
            parsed = ai_parser.parse_reply("Jumaat baru sampai", ORDER, TODAY)
        self.assertEqual(parsed["mode_note"], "AI: glm-5.3 (RunPod)")
        for secret in ("abc123", "test-key-not-real", "AI_"):
            self.assertNotIn(secret, parsed["mode_note"])

    def test_unextractable_answer_needs_review(self):
        parsed = self.parse_with_fake_model(lambda *a: None)  # extract_json 取不出 JSON 时返回 None
        self.assertEqual(parsed["mode"], "ai")
        self.assertTrue(parsed["needs_review"])
        self.assertEqual(parsed["result"]["intent"], "unclear")

    def test_timeout_falls_back_to_rules(self):
        class APITimeoutError(Exception):  # 和 openai 库里超时异常同名
            pass

        def slow(*args):
            raise APITimeoutError("15 seconds passed")
        parsed = self.parse_with_fake_model(slow)
        self.assertEqual(parsed["mode"], "rules")
        self.assertEqual(parsed["mode_note"], "Offline rules (AI unavailable)")
        self.assertEqual(parsed["mode_reason"], "AI timed out")

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
        self.assertEqual(parsed["mode_reason"], "AI error (ConnectionError)")
        self.assertEqual(parsed["result"]["new_delivery_date"], "2026-10-23")  # 规则模式照样能读出日期


class ExtractJsonTest(unittest.TestCase):
    """有的服务会在 JSON 外面包代码块或者加几句话，要先提取出来。"""

    def test_plain_json(self):
        self.assertEqual(ai_parser.extract_json('{"intent": "delay"}'), {"intent": "delay"})

    def test_code_fence(self):
        self.assertEqual(ai_parser.extract_json('```json\n{"intent": "delay"}\n```'), {"intent": "delay"})
        self.assertEqual(ai_parser.extract_json('```\n{"intent": "confirm"}\n```'), {"intent": "confirm"})

    def test_text_around(self):
        content = 'Sure! Here is the result:\n{"intent": "partial", "quantity": 12}\nHope this helps.'
        self.assertEqual(ai_parser.extract_json(content), {"intent": "partial", "quantity": 12})

    def test_failures_return_none(self):
        for content in ("no json here", "[1, 2, 3]", '{"intent": "delay"', "", None):
            with self.subTest(content=content):
                self.assertIsNone(ai_parser.extract_json(content))


class CallOptionsTest(unittest.TestCase):
    """max_tokens 每次都传；reasoning_effort 只有设置了才传。用假的 OpenAI 客户端，不联网。"""

    def call_with_env(self, extra_env):
        sent = {}

        class FakeCompletions:
            def create(self, **kwargs):
                sent.update(kwargs)
                message = mock.Mock(content='{"intent": "confirm"}')
                return mock.Mock(choices=[mock.Mock(finish_reason="stop", message=message)])

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.chat = mock.Mock(completions=FakeCompletions())

        env = dict(FAKE_AI_ENV, AI_MAX_TOKENS="", AI_REASONING_EFFORT="")
        env.update(extra_env)
        with mock.patch.dict(os.environ, env), mock.patch("openai.OpenAI", FakeOpenAI), \
             mock.patch.dict(ai_parser._schema_supported, {"value": None}):
            result = ai_parser._call_model("ok boss", ORDER, TODAY)
        return sent, result

    def test_defaults_send_max_tokens_but_no_reasoning_effort(self):
        sent, result = self.call_with_env({})
        self.assertEqual(sent["max_tokens"], 400)
        self.assertNotIn("reasoning_effort", sent)
        self.assertEqual(result, {"intent": "confirm"})

    def test_optional_settings_are_sent(self):
        sent, _ = self.call_with_env({"AI_MAX_TOKENS": "1500", "AI_REASONING_EFFORT": "low"})
        self.assertEqual(sent["max_tokens"], 1500)
        self.assertEqual(sent["reasoning_effort"], "low")

    def test_bad_max_tokens_falls_back_to_default(self):
        sent, _ = self.call_with_env({"AI_MAX_TOKENS": "lots"})
        self.assertEqual(sent["max_tokens"], 400)


if __name__ == "__main__":
    unittest.main()
