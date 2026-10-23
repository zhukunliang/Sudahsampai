"""读懂供应商回复：把一条 WhatsApp 回复解析成固定格式的 JSON。

供应商回复常常是马来文、中文、英文混在一起的口语（"Jumaat baru sampai"、"only 13 kotak"），
关键词规则很难覆盖，所以这一步用大模型。但 AI 只负责"读懂"，不负责"决定"：
- 结果必须符合固定的 JSON schema，后端还会再校验一遍；
- 校验不过、confidence < 0.6 或看不懂时，标记"需要人工确认"；
- 真正修改订单永远要店主点"确认"（在 app.py 里）。

模型通过 OpenAI 兼容接口调用，地址、密钥、模型名全部来自环境变量（Replit Secrets）：
    AI_BASE_URL  例如 DeepSeek：https://api.deepseek.com
                 或 RunPod 公共接口：https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1
    AI_API_KEY   密钥（代码里不出现任何密钥）
    AI_MODEL     例如 deepseek-chat 或 glm-5.3
两个可选的：
    AI_MAX_TOKENS        模型最多输出多少 token，默认 400（会"先思考再回答"的模型要调大）
    AI_REASONING_EFFORT  例如 low；设置了才传给模型，没设置就不传（DeepSeek 不需要）
换成别的 OpenAI 兼容服务只要改这些环境变量，代码不用动。

三个变量没设齐、请求超过 15 秒或出错时，自动换成下面的规则解析器（关键词 + 正则），
页面上标注"规则模式"，Demo 不会因为 API 出问题而中断。
"""
import json
import os
import re
from datetime import date, timedelta

AI_ENV_VARS = ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL")
REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_MAX_TOKENS = 400
INTENTS = ("delay", "partial", "price_change", "confirm", "unclear")
CONFIDENCE_THRESHOLD = 0.6
MAX_REPLY_LENGTH = 1000  # 太长的文本截断前先拒绝，避免意外的大额调用

# 模型必须按这个结构返回（结构化输出）。null 用 anyOf 表示"可以为空"。
SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "new_delivery_date": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "quantity": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "unit_price": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "confidence": {"type": "number"},
        "summary_en": {"type": "string"},
    },
    "required": ["intent", "new_delivery_date", "quantity", "unit_price", "confidence", "summary_en"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You read WhatsApp replies from suppliers to a small shop in Malaysia and extract structured facts.
Replies are informal and often mix Malay, Chinese and English.

Intents:
- delay: the delivery will arrive later than planned (give new_delivery_date if a day is mentioned).
- partial: the supplier can only send part of the order now (give quantity = how many units will come).
- price_change: the unit price is changing (give unit_price as a number in RM, no currency sign).
- confirm: the supplier confirms the order / delivery as planned.
- unclear: you cannot tell what the supplier means. Use this rather than guessing.

Dates:
- "today" is the shop's simulated date given in the message. Convert relative dates against it.
- A weekday name (Malay: Isnin, Selasa, Rabu, Khamis, Jumaat, Sabtu, Ahad; Chinese: 星期一/周一/礼拜一 ...;
  English: Monday ...) means the NEXT such day strictly after today.
- esok / 明天 / tomorrow = today + 1; lusa / 后天 = today + 2.
- Output dates as YYYY-MM-DD. Use null when no date is stated.

Use null for any field the reply does not state. confidence is 0 to 1: how sure you are about the intent
and the extracted values. summary_en is one short English sentence describing what the supplier said.

Reply with one JSON object only, with exactly these keys:
{"intent": "delay|partial|price_change|confirm|unclear", "new_delivery_date": "YYYY-MM-DD or null",
 "quantity": integer or null, "unit_price": number or null, "confidence": number 0-1, "summary_en": "..."}"""

# 记住当前这个 AI 服务支不支持 json_schema 结构化输出（vLLM 支持；DeepSeek 只支持 json_object）。
# None = 还没试过；第一次被拒绝后记成 False，以后直接用 json_object。
_schema_supported = {"value": None}


# ---------- 对外入口 ----------

def parse_reply(text, order, today):
    """解析一条供应商回复。order 是订单（dict 或 sqlite Row），today 是模拟日期（date）。
    返回 {"result": {...}, "mode": "ai"|"rules", "mode_note": 页面上显示的标签,
          "mode_reason": 为什么用了规则模式（写进日志，不含环境变量名）, "needs_review": bool, "problems": [...]}"""
    text = (text or "").strip()
    if not text:
        return _package(_unclear("The reply is empty."), "rules", "empty reply", ["回复是空的"])
    if len(text) > MAX_REPLY_LENGTH:
        return _package(_unclear("The reply is too long to analyse."), "rules", "reply too long",
                        [f"回复超过 {MAX_REPLY_LENGTH} 个字"])

    if not all(os.environ.get(name) for name in AI_ENV_VARS):
        result, mode, reason = rule_parse(text, order, today), "rules", "AI not configured"
    else:
        try:
            result, mode, reason = _call_model(text, order, today), "ai", ""
        except Exception as e:  # 超时、网络、密钥、格式不对……任何问题都换成规则模式，Demo 不中断
            result, mode = rule_parse(text, order, today), "rules"
            reason = "AI timed out" if "Timeout" in type(e).__name__ else f"AI error ({type(e).__name__})"

    clean, problems = validate(result)
    return _package(clean, mode, reason, problems)


def provider_name():
    """AI 服务商的名字，从地址里认出来；认不出来返回 None。"""
    url = os.environ.get("AI_BASE_URL", "").lower()
    if "runpod.ai" in url:
        return "RunPod"
    if "deepseek" in url:
        return "DeepSeek"
    return None


def ai_label():
    """页面上显示的模式名称，例如 "AI: glm-5.3 (RunPod)"。只用模型名和服务商名，
    不显示环境变量名、地址或密钥。"""
    model = os.environ.get("AI_MODEL", "").strip() or "model"
    provider = provider_name()
    return f"AI: {model} ({provider})" if provider else f"AI: {model}"


def _package(result, mode, reason, problems):
    needs_review = bool(problems) or result["confidence"] < CONFIDENCE_THRESHOLD or result["intent"] == "unclear"
    if result["confidence"] < CONFIDENCE_THRESHOLD and not problems:
        problems = [f"把握度 {result['confidence']:.2f} 低于 {CONFIDENCE_THRESHOLD}"]
    if result["intent"] == "unclear" and "看不懂供应商的意思" not in problems:
        problems = problems + ["看不懂供应商的意思"]
    note = ai_label() if mode == "ai" else "Offline rules (AI unavailable)"
    return {"result": result, "mode": mode, "mode_note": note, "mode_reason": reason,
            "needs_review": needs_review, "problems": problems}


# ---------- AI 模式 ----------

def _call_model(text, order, today):
    """通过 OpenAI 兼容接口调用模型，要求按 SCHEMA 返回 JSON。出任何问题都抛异常，由 parse_reply 兜底。
    先用 json_schema（格式最严格）；服务不支持时（例如 DeepSeek）自动改用 json_object，
    字段对不对交给 validate() 检查。"""
    from openai import BadRequestError, OpenAI  # 放在这里导入：没装这个包时，规则模式照样能用

    client = OpenAI(
        base_url=os.environ["AI_BASE_URL"],
        api_key=os.environ["AI_API_KEY"],
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=0,  # 超时就直接换规则模式，不让用户多等
    )
    user_message = (
        f"Today (simulated date): {today.isoformat()} ({today.strftime('%A')})\n"
        f"Order: #{order['id']} {order['product']}, {order['quantity']} {order['unit']}, "
        f"planned delivery {order['delivery_date']}, supplier {order['supplier']}\n\n"
        f"Supplier reply:\n{text}"
    )
    options = {"max_tokens": max_tokens_setting()}  # 每次都传
    effort = os.environ.get("AI_REASONING_EFFORT", "").strip()
    if effort:
        options["reasoning_effort"] = effort  # 只有设置了才传，没设置就完全不传（DeepSeek 照常工作）

    def ask(response_format):
        return client.chat.completions.create(
            model=os.environ["AI_MODEL"],
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message}],
            response_format=response_format,
            temperature=0,  # 解析任务不需要创意，每次结果尽量一致
            **options,
        )

    response = None
    if _schema_supported["value"] is not False:
        try:
            # 结构化输出：要求模型严格按 SCHEMA 生成 JSON（vLLM 会用受约束解码来保证格式）
            response = ask({"type": "json_schema",
                            "json_schema": {"name": "supplier_reply", "schema": SCHEMA, "strict": True}})
            _schema_supported["value"] = True
        except BadRequestError:
            if _schema_supported["value"] is True:
                raise  # 以前支持、这次报 400，说明是别的问题，不是格式不支持
            _schema_supported["value"] = False  # 这个服务不支持 json_schema，记住，以后不再试
    if response is None:
        response = ask({"type": "json_object"})  # 只保证返回 JSON；字段由 validate() 检查
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("response cut off")
    # 有的服务会在 JSON 外面包 ```json 代码块，或者前后加几句话：先把 JSON 提取出来。
    # 提取不出来就返回 None，validate() 会把它当成解析失败，标记"需要人工确认"
    return extract_json(choice.message.content)


def max_tokens_setting():
    """AI_MAX_TOKENS 环境变量；没设置或不是正整数时用默认的 400。"""
    value = os.environ.get("AI_MAX_TOKENS", "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else DEFAULT_MAX_TOKENS


def extract_json(content):
    """从模型的回答里取出 JSON 对象（dict）。依次尝试：
    1. 整段就是 JSON；2. ```json ... ``` 代码块里的内容；3. 第一个 { 到最后一个 } 之间的内容。
    都不行（或者取出来的不是对象）就返回 None。"""
    if not isinstance(content, str):
        return None
    text = content.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


# ---------- 后端校验（AI 和规则的结果都要过这一关） ----------

def validate(raw):
    """检查解析结果是否符合约定的格式。返回 (干净的结果, 问题列表)。
    有问题的字段会被清空成 None，intent 不认识就当 unclear。"""
    problems = []
    if not isinstance(raw, dict):
        return _unclear("The result was not a JSON object."), ["返回的不是 JSON 对象"]

    intent = raw.get("intent")
    if intent not in INTENTS:
        problems.append(f"intent 不合法：{intent!r}")
        intent = "unclear"

    new_date = raw.get("new_delivery_date")
    if new_date is not None:
        try:
            new_date = date.fromisoformat(new_date).isoformat()
        except (TypeError, ValueError):
            problems.append(f"new_delivery_date 不是 YYYY-MM-DD：{new_date!r}")
            new_date = None

    quantity = raw.get("quantity")
    if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0):
        problems.append(f"quantity 不是 0 或以上的整数：{quantity!r}")
        quantity = None

    price = raw.get("unit_price")
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0):
        problems.append(f"unit_price 不是 0 或以上的数字：{price!r}")
        price = None

    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        problems.append(f"confidence 不在 0 到 1 之间：{confidence!r}")
        confidence = 0.0

    summary = raw.get("summary_en")
    if not isinstance(summary, str) or not summary.strip():
        problems.append("summary_en 是空的")
        summary = ""

    # 每种意图必须带上它需要的字段，否则没法据此更新订单
    needed = {"delay": ("new_delivery_date", new_date), "partial": ("quantity", quantity),
              "price_change": ("unit_price", price)}
    if intent in needed and needed[intent][1] is None:
        problems.append(f"intent 是 {intent}，但缺少 {needed[intent][0]}")

    clean = {"intent": intent, "new_delivery_date": new_date, "quantity": quantity,
             "unit_price": float(price) if price is not None else None,
             "confidence": float(confidence), "summary_en": summary.strip()}
    return clean, problems


def _unclear(summary):
    return {"intent": "unclear", "new_delivery_date": None, "quantity": None, "unit_price": None,
            "confidence": 0.0, "summary_en": summary}


# ---------- 规则模式（备选）：关键词 + 正则 ----------

WEEKDAYS = {  # Python 的 weekday()：星期一 = 0 … 星期日 = 6
    0: ("isnin", "monday", "星期一", "周一", "礼拜一"),
    1: ("selasa", "tuesday", "星期二", "周二", "礼拜二"),
    2: ("rabu", "wednesday", "星期三", "周三", "礼拜三"),
    3: ("khamis", "thursday", "星期四", "周四", "礼拜四"),
    4: ("jumaat", "jumat", "friday", "星期五", "周五", "礼拜五"),
    5: ("sabtu", "saturday", "星期六", "周六", "礼拜六"),
    6: ("ahad", "sunday", "星期日", "星期天", "周日", "礼拜天"),
}

KEYWORDS = {
    # 延期：要晚一点 / 某天才能送
    "delay": ("lambat", "lewat", "delay", "late", "tunda", "tangguh", "postpone", "baru boleh hantar",
              "baru sampai", "tak dapat hantar", "tidak dapat hantar", "next week", "minggu depan",
              "迟", "晚点", "改天", "延迟", "送不了"),
    # 少货：只能先送一部分
    "partial": ("je", "sahaja", "saja", "only", "kurang", "tak cukup", "tidak cukup", "separuh", "sebahagian",
                "只有", "先送", "不够"),
    # 涨价（"RM 数字" 在 find_price 里另外认）
    "price_change": ("naik harga", "harga naik", "harga baru", "new price", "price increase",
                     "涨价", "加价", "价格"),
    # 确认：只有在上面三种信号都没有时才算
    "confirm": ("ok", "okay", "confirm", "on time", "no problem", "boleh hantar macam biasa",
                "准时", "没问题", "确认"),
}


def has_keyword(lowered, words):
    """文本里有没有出现这些词。英文 / 马来文按整个单词匹配（"je" 不会误中 "jemput"），中文按字面匹配。"""
    for word in words:
        if word.isascii():
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
                return True
        elif word in lowered:
            return True
    return False


def rule_parse(text, order, today):
    """备选：不用 AI。先把所有信号都找出来（延期 / 少货 / 涨价 / 确认，以及日期、数量、价格），再决定意图：
    - 有涨价信号 → price_change；有少货信号 → partial；有延期信号 → delay
      （同一句话里又延期又少货时算 partial，日期和数量一起带上）；
    - 只有出现"确认"类词语、并且没有任何上面的信号时，才算 confirm；
    - 其他情况一律 unclear，把握度 < 0.6，必须转人工。"""
    lowered = text.lower()
    new_date = find_date(lowered, today)
    price = find_price(lowered)
    quantity = find_quantity(lowered, order)

    later_date = new_date is not None and new_date > order["delivery_date"]
    signals = {
        "price_change": has_keyword(lowered, KEYWORDS["price_change"]) or price is not None
                        or (has_keyword(lowered, ("harga", "price")) and has_keyword(lowered, ("naik", "up"))),
        "partial": has_keyword(lowered, KEYWORDS["partial"])
                   or (quantity is not None and quantity < order["quantity"]),
        "delay": has_keyword(lowered, KEYWORDS["delay"]) or later_date,  # 说的日期比原定晚，也算延期
    }

    result = {"intent": "unclear", "new_delivery_date": None, "quantity": None, "unit_price": None,
              "confidence": 0.3, "summary_en": "The reply could not be understood by the offline rules."}
    mixed = sum(signals.values()) > 1  # 同时有好几种信号：把握度稍微降低

    if signals["price_change"]:
        result.update(intent="price_change", unit_price=price,
                      confidence=(0.65 if mixed else 0.7) if price is not None else 0.5,
                      summary_en=f"Supplier says the unit price changes"
                                 f"{' to RM ' + format(price, 'g') if price is not None else ''}.")
    elif signals["partial"]:
        when = f" by {new_date}" if new_date else ""
        result.update(intent="partial", quantity=quantity, new_delivery_date=new_date,
                      confidence=(0.65 if mixed else 0.7) if quantity is not None else 0.5,
                      summary_en=f"Supplier can only deliver "
                                 f"{str(quantity) + ' ' + (order['unit'] or 'units') if quantity is not None else 'part of the order'}"
                                 f"{when} for now.")
    elif signals["delay"]:
        result.update(intent="delay", new_delivery_date=new_date, confidence=0.7 if new_date else 0.5,
                      summary_en=f"Supplier says the delivery is delayed{' to ' + new_date if new_date else ''}.")
    elif has_keyword(lowered, KEYWORDS["confirm"]):
        result.update(intent="confirm", new_delivery_date=new_date, confidence=0.7,
                      summary_en="Supplier confirms the delivery as planned.")
    return result


def find_date(lowered, today):
    """找日期：YYYY-MM-DD、dd/mm、明天/后天、星期几（取今天之后最近的那一天）。找不到返回 None。"""
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lowered)
    if m:
        try:
            return date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", lowered)
    if m:
        try:
            return date(today.year, int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    if "lusa" in lowered or "后天" in lowered:
        return (today + timedelta(days=2)).isoformat()
    if "esok" in lowered or "明天" in lowered or "tomorrow" in lowered:
        return (today + timedelta(days=1)).isoformat()
    for weekday, names in WEEKDAYS.items():
        if any(name in lowered for name in names):
            days_ahead = (weekday - today.weekday()) % 7 or 7  # 今天就是星期五时，指下星期五
            return (today + timedelta(days=days_ahead)).isoformat()
    return None


def find_price(lowered):
    """找价格：RM 58 / rm58.50。找不到返回 None。"""
    m = re.search(r"rm\s*(\d+(?:\.\d+)?)", lowered)
    return float(m.group(1)) if m else None


def find_quantity(lowered, order):
    """找数量：优先找"数字 + 单位"（订单单位或常见单位）。去掉价格和日期后再找。找不到返回 None。"""
    cleaned = re.sub(r"rm\s*\d+(?:\.\d+)?", " ", lowered)
    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}", " ", cleaned)
    units = {u for u in (str(order["unit"] or "").lower(), "kotak", "box", "boxes", "carton", "ctn",
                         "botol", "bottle", "bottles", "pcs", "unit", "units", "tin", "bag", "beg", "kg",
                         "箱", "瓶", "包", "桶", "条", "盘")} - {""}
    unit_pattern = "|".join(re.escape(u) for u in sorted(units, key=len, reverse=True))
    m = re.search(rf"(\d+)\s*(?:{unit_pattern})", cleaned)
    if m:
        return int(m.group(1))
    return None
