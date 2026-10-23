"""对比不同 AI 模型解析供应商回复的效果。

用当前终端里设置的 AI_BASE_URL / AI_API_KEY / AI_MODEL（以及可选的 AI_MAX_TOKENS、AI_REASONING_EFFORT），
把 data/sample_replies.json 里的全部样例，加上一条混合了延期和少货的消息，逐条解析，打印一张表。
模拟日期固定为 2026-10-21，订单是一张虚构的合成订单。

运行（在项目根目录）：
    python scripts/compare_models.py

脚本只打印模型名和服务商，不会打印密钥或地址。没设置好环境变量时会走规则模式，表格里会标出来。
"""
import json
import os
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import ai_parser  # noqa: E402

TODAY = date(2026, 10, 21)  # 模拟日期（星期三）
ORDER = {  # 虚构的合成订单
    "id": 2, "supplier": "Segar Sayur Sdn Bhd", "product": "Choy sum", "quantity": 15, "unit": "boxes",
    "unit_price": 18.00, "delivery_date": "2026-10-22",
}
EXTRA_MESSAGE = "Boss sorry ya, lori rosak. Jumaat baru boleh hantar, 12 kotak je dulu"

COLUMNS = [("Message", 40), ("Mode", 5), ("Intent", 12), ("New date", 10), ("Qty", 4), ("Price", 7),
           ("Conf", 4), ("Secs", 5), ("Review", 6)]


def load_messages():
    with open(os.path.join(ROOT, "data", "sample_replies.json"), encoding="utf-8") as f:
        messages = [s["text"] for s in json.load(f)]
    return messages + [EXTRA_MESSAGE]


def cell(value, width):
    """把一个值变成固定宽度的格子（太长就截断）。"""
    text = "-" if value is None else str(value)
    text = text.replace("\n", " ")
    return text[:width].ljust(width)


def main():
    configured = all(os.environ.get(name) for name in ai_parser.AI_ENV_VARS)
    label = ai_parser.ai_label() if configured else "AI not configured -> offline rules"
    print(f"Model: {label}")
    print(f"Max tokens: {ai_parser.max_tokens_setting()} | "
          f"Reasoning effort: {os.environ.get('AI_REASONING_EFFORT', '').strip() or '(not sent)'} | "
          f"Simulated date: {TODAY.isoformat()}")
    print()

    header = " | ".join(cell(name, width) for name, width in COLUMNS)
    print(header)
    print("-" * len(header))

    times, reviews, fallbacks = [], 0, 0
    for text in load_messages():
        start = time.perf_counter()
        parsed = ai_parser.parse_reply(text, ORDER, TODAY)
        seconds = time.perf_counter() - start
        times.append(seconds)
        r = parsed["result"]
        reviews += parsed["needs_review"]
        if parsed["mode"] != "ai":
            fallbacks += 1
        row = [text[:40], parsed["mode"], r["intent"], r["new_delivery_date"], r["quantity"],
               f"{r['unit_price']:g}" if r["unit_price"] is not None else None,
               f"{r['confidence']:.2f}", f"{seconds:.2f}", "YES" if parsed["needs_review"] else "no"]
        print(" | ".join(cell(value, width) for value, (_, width) in zip(row, COLUMNS)))
        if parsed["mode"] != "ai" and parsed["mode_reason"]:
            print(f"    (fell back to offline rules: {parsed['mode_reason']})")

    print()
    print(f"Messages: {len(times)} | Average time: {sum(times) / len(times):.2f} s | "
          f"Needs human check: {reviews} | Fell back to offline rules: {fallbacks}")


if __name__ == "__main__":
    main()
