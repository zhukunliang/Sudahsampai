"""界面多语言：所有界面文字都在 src/locales.json 里，代码和模板用 t("key") 取。

- 默认英文；用户点 EN / BM / 中文 后，选择存在 session 里。
- 某种语言缺了某个词，就用英文；英文也没有，就直接显示 key（方便发现漏了哪个词）。
- 日志内容是记录下来的数据，统一用英文写（t_en），不随界面语言变化。
"""
import json
import os

from flask import has_request_context, session

LANGUAGES = {"en": "EN", "ms": "BM", "zh": "中文"}  # 代码 → 切换按钮上显示的名字
DEFAULT_LANGUAGE = "en"

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales.json"), encoding="utf-8") as f:
    STRINGS = json.load(f)

# 系统内部用中文存角色（数据库、session 都是这几个值），显示时翻译成当前语言
ROLE_KEYS = {"店主": "role.owner", "店员": "role.staff", "系统": "role.system"}


def current_language():
    if has_request_context():
        lang = session.get("lang")
        if lang in LANGUAGES:
            return lang
    return DEFAULT_LANGUAGE


def translate(lang, key, **params):
    """取 lang 语言里 key 对应的文字，再把 {名字} 换成 params 里的值。"""
    text = STRINGS.get(lang, {}).get(key) or STRINGS[DEFAULT_LANGUAGE].get(key) or key
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError, ValueError):  # 翻译里的占位符写错了：退回英文，页面不报错
        return STRINGS[DEFAULT_LANGUAGE].get(key, key).format(**params)


def t(key, **params):
    """当前界面语言的文字。"""
    return translate(current_language(), key, **params)


def t_en(key, **params):
    """英文文字（写日志用）。"""
    return translate(DEFAULT_LANGUAGE, key, **params)


def role_label(role):
    """把内部角色值（店主 / 店员 / 系统）翻译成当前语言。"""
    return t(ROLE_KEYS[role]) if role in ROLE_KEYS else role


def rm(amount):
    """金额格式：RM 25.60。"""
    return f"RM {amount:,.2f}"
