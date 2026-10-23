import json
import os
import secrets
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for

import ai_parser  # 读懂供应商回复（AI，失败时自动换规则模式）
import db
import orbyte_adapter as orbyte  # 用户、联系人、发消息、待办、点数、审计日志都走这一层
from i18n import LANGUAGES, current_language, rm, role_label, t, t_en

app = Flask(__name__)
# session 签名用的密钥：从环境变量读（Replit 的 Secrets 里设置 SECRET_KEY）。
# 没设置时每次启动随机生成一个，代码里不写任何密钥。
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
db.init_db()


@app.context_processor
def inject_globals():
    """每个模板都能用的东西：翻译函数 t、金额格式 rm、当前用户、credits、模拟日期等。"""
    with db.connect() as conn:
        credits = orbyte.credits_used_this_month(conn)
    return {"t": t, "rm": rm, "role_label": role_label, "languages": LANGUAGES, "lang": current_language(),
            "sim_today": db.today().isoformat(), "user": orbyte.get_current_user(),
            "role": current_role(), "roles": (OWNER, CLERK), "is_owner": current_role() == OWNER,
            "credits_used": credits, "db": db}


@app.route("/lang/<code>")
def set_language(code):
    """切换界面语言（EN / BM / 中文），存在 session 里，然后回到原来的页面。"""
    if code in LANGUAGES:
        session["lang"] = code
    return redirect(back_url())


# ---------- 订单的计算（金额、差异、颜色、原因） ----------

def money(quantity, unit_price):
    """数量 × 单价；没有单价就是 None。"""
    return round(quantity * unit_price, 2) if unit_price is not None else None


def describe_diff(quantity, received, unit, unit_price=None):
    """写日志用的英文说明，例如：Short 4 bottles (ordered 48, received 44) ≈ RM 25.60"""
    word = "Short" if received < quantity else "Over"
    n = abs(received - quantity)
    amount = money(n, unit_price)
    return (f"{word} {n} {unit} (ordered {quantity}, received {received})".replace("  ", " ")
            + (f" ≈ {rm(amount)}" if amount is not None else ""))


def diff_info(o):
    """实收数量和订单不一样时，返回 {kind: short/over, n, unit, amount, ordered, received}；一样就返回 None。"""
    if o["received_quantity"] is None or o["received_quantity"] == o["quantity"]:
        return None
    n = abs(o["received_quantity"] - o["quantity"])
    return {"kind": "short" if o["received_quantity"] < o["quantity"] else "over", "n": n, "unit": o["unit"],
            "amount": money(n, o["unit_price"]), "ordered": o["quantity"], "received": o["received_quantity"]}


def diff_label(d):
    """界面上显示的差异，例如 "Short 4 bottles ≈ RM 25.60"（跟着界面语言）。"""
    text = t(f"diff.{d['kind']}", n=d["n"], unit=d["unit"])
    return text + (f" ≈ {rm(d['amount'])}" if d["amount"] is not None else "")


def decorate(row):
    """给订单加上页面要用的信息：颜色 flag、剩余天数、为什么是这个颜色、差异、迟到天数、订单金额。
    文字都存成 (key, 参数)，模板里用 t() 翻译成当前语言。"""
    o = dict(row)
    days_left = (date.fromisoformat(o["delivery_date"]) - db.today()).days
    remind = o["remind_days_before"]
    o["days_left"] = days_left

    if o["status"] in db.ISSUES:
        o["flag"], o["due"], o["reason"] = "diff", None, ("reason.issue", {})
    elif o["status"] == db.DELIVERED:
        o["flag"], o["due"], o["reason"] = "done", None, ("reason.delivered", {})
    elif o["status"] == db.OVERDUE or days_left < 0:
        o["flag"] = "late"
        o["due"] = ("due.late", {"n": -days_left})
        o["reason"] = ("reason.late", {"date": o["delivery_date"], "n": -days_left})
    elif days_left <= remind:
        o["flag"] = "soon"
        o["due"] = ("due.today", {}) if days_left == 0 else ("due.in", {"n": days_left})
        o["reason"] = (("reason.soon_today", {"remind": remind}) if days_left == 0
                       else ("reason.soon", {"remind": remind, "n": days_left}))
    else:
        o["flag"] = "normal"
        o["due"] = ("due.in", {"n": days_left})
        o["reason"] = ("reason.normal", {"remind": remind, "n": days_left})

    o["diff"] = diff_info(o)
    o["undo"] = auto_undo_info(o)  # 上一次自动应用还能撤销时，订单页显示"已按你的规则自动更新"
    o["value"] = money(o["quantity"], o["unit_price"])
    o["late_days"] = 0
    if o["received_at"]:
        o["late_days"] = max(0, (date.fromisoformat(o["received_at"]) -
                                 date.fromisoformat(o["delivery_date"])).days)
    return o


def get_order(conn, order_id):
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        abort(404)
    return row


# ---------- 仪表盘 ----------

def build_dashboard(orders):
    """四张数字卡片和"今日待办"三组。每个待办项：(图标, 文字 key, 参数, 按钮 key, 链接)。"""
    must, know, waiting = [], [], []
    for o in orders:
        link = url_for("order_detail", order_id=o["id"]) + "#next"
        names = {"product": o["product"], "supplier": o["supplier"]}
        if o["status"] == db.DISCREPANCY:
            must.append(("package-x", "todo.discrepancy", dict(names, diff=diff_label(o["diff"])), "btn.resolve", link))
        elif o["flag"] == "late":
            must.append(("alarm-clock", "todo.overdue", dict(names, days=-o["days_left"]), "btn.paste_reply", link))
        elif o["flag"] == "soon":
            key = "todo.arriving_today" if o["days_left"] == 0 else "todo.arriving"
            button = "btn.confirm_receipt" if o["days_left"] == 0 else "btn.view"
            know.append(("truck", key, dict(names, days=o["days_left"]), button, link))
        elif o["status"] in db.WAITING:
            key = "todo.restock" if o["status"] == db.RESTOCK_REQUESTED else "todo.negotiating"
            waiting.append(("hourglass", key, names, "btn.follow_up", link))
    return {
        "needs_action": len(must),
        "arriving": len(know),
        "waiting": len(waiting),
        "groups": [("todo.must", "must", must), ("todo.know", "know", know), ("todo.waiting", "waiting", waiting)],
    }


@app.route("/")
def index():
    # 刚快进过的话，取出这次快进触发的事件做摘要；pop 表示只显示一次，刷新后就不再显示
    ff_log_id = session.pop("ff_log_id", None)
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY delivery_date, id").fetchall()
        events = db.get_events_after(conn, ff_log_id) if ff_log_id else None
        losses = db.losses_prevented_this_month(conn)
    orders = [decorate(r) for r in rows]
    return render_template("index.html", orders=orders, events=events, losses=losses,
                           dash=build_dashboard(orders))


# ---------- 角色和权限 ----------

OWNER = orbyte.OWNER
CLERK = orbyte.STAFF


def current_role():
    """当前登录用户的角色，由 Orbyte 接口层提供（现在是模拟登录）。"""
    return orbyte.get_current_user()["role"]


def back_url():
    """操作完回到上一页。只接受本站的地址，防止被人利用跳到别的网站。"""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return ref
    return url_for("index")


def owner_required(what):
    """装饰器：被它包住的路由只有店主能用。what 是操作名称的 key，比如 "action.change_rules"。
    店员访问时不执行原来的函数，而是写一条 permission_denied 日志，显示统一的提示页（403）。"""
    def decorator(view):
        @wraps(view)  # 保留原函数的名字，url_for 才能照常找到它
        def wrapper(*args, **kwargs):
            if current_role() != OWNER:
                order_id = kwargs.get("order_id")  # 订单相关的路由会带 order_id，日志就能关联到订单
                prefix = f"#{order_id} " if order_id else ""
                with db.connect() as conn:
                    orbyte.write_audit(conn, "permission_denied",
                                       f"{prefix}{orbyte.get_current_user()['name']} (staff) tried to "
                                       f"{t_en(what)} — denied", order_id)
                back = url_for("order_detail", order_id=order_id) if order_id else url_for("index")
                return render_template("denied.html", what=what, back=back), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


@app.route("/role", methods=["POST"])
def switch_role():
    """演示用：切换登录身份（模拟 Orbyte 登录）。每次切换都写日志。"""
    role = request.form.get("role")
    if role not in (OWNER, CLERK):
        flash(t("flash.no_role"), "error")
    elif role != current_role():
        old = orbyte.get_current_user()
        session["role"] = role  # 模拟登录：真实 Orbyte 里身份来自单点登录，没有这个按钮
        new = orbyte.get_current_user()
        with db.connect() as conn:
            orbyte.write_audit(conn, "role_switched",
                               f"{old['name']} ({t_en(role_key(old))}) → {new['name']} ({t_en(role_key(new))})")
        flash(t("flash.role", name=new["name"], role=role_label(new["role"])), "ok")
    return redirect(back_url())


def role_key(user):
    """当前用户角色对应的翻译 key。"""
    return "role.owner" if user["role"] == OWNER else "role.staff"


# ---------- 新增订单 ----------

ORDER_FIELDS = ["supplier", "whatsapp", "product", "category", "quantity", "unit", "unit_price",
                "delivery_date", "expiry_date", "remind_days_before"]


def read_order_form(form):
    """把表单原样读出来（去掉首尾空格），出错时用来回填。"""
    data = {name: form.get(name, "").strip() for name in ORDER_FIELDS}
    data["auto_chase"] = form.get("auto_chase") == "1"
    return data


def validate_order(data):
    """检查表单，返回错误列表（已经翻译成当前语言）；没有错误就返回空列表。顺便把数字和日期转换好。"""
    errors = []

    if not data["product"]:
        errors.append(t("err.product"))

    try:
        data["quantity"] = int(data["quantity"])
        if data["quantity"] <= 0:
            errors.append(t("err.qty_positive"))
    except ValueError:
        errors.append(t("err.qty_int"))

    if not data["delivery_date"]:
        errors.append(t("err.date_required"))
    else:
        try:
            date.fromisoformat(data["delivery_date"])
        except ValueError:
            errors.append(t("err.date_format"))

    if data["expiry_date"]:  # 保质期可以不填，填了就要是正确的日期
        try:
            date.fromisoformat(data["expiry_date"])
        except ValueError:
            errors.append(t("err.expiry"))

    try:
        data["remind_days_before"] = int(data["remind_days_before"] or 1)
        if data["remind_days_before"] < 0:
            errors.append(t("err.remind_negative"))
    except ValueError:
        errors.append(t("err.remind_int"))

    if data["unit_price"]:  # 单价可以不填，填了就要是 0 或以上的数字
        try:
            data["unit_price"] = round(float(data["unit_price"]), 2)
            if data["unit_price"] < 0:
                raise ValueError
        except ValueError:
            errors.append(t("err.price"))
    else:
        data["unit_price"] = None

    return errors


@app.route("/orders/new", methods=["GET", "POST"])
def new_order():
    if request.method == "GET":
        empty = {name: "" for name in ORDER_FIELDS}
        empty.update(remind_days_before=1, auto_chase=True)  # 默认提前 1 天提醒，默认开启自动催货
        return render_template("new.html", form=empty, errors=[])

    data = read_order_form(request.form)
    form_values = dict(data)  # 出错时原样回填用户填的内容
    errors = validate_order(data)
    if errors:
        # 400 = 请求有误；把已填写的内容传回去，表单不会被清空
        return render_template("new.html", form=form_values, errors=errors), 400

    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO orders (supplier, whatsapp, product, category, quantity, unit, unit_price,
                   delivery_date, expiry_date, remind_days_before, auto_chase)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["supplier"], data["whatsapp"], data["product"], data["category"],
             data["quantity"], data["unit"], data["unit_price"], data["delivery_date"],
             data["expiry_date"] or None, data["remind_days_before"], 1 if data["auto_chase"] else 0),
        )
        order_id = cur.lastrowid
        # 日志：role 列记录"谁"，内容记录"新增了什么"
        orbyte.write_audit(conn, "order_created",
                           f"#{order_id} Ordered {data['quantity']} {data['unit']} {data['product']} "
                           f"from {data['supplier'] or '(no supplier)'}, delivery {data['delivery_date']}", order_id)
    flash(t("flash.created", id=order_id), "ok")
    return redirect(url_for("index"))


# ---------- 订单详情 ----------

# 订单详情：/orders/2。<int:...> 只匹配数字，所以不会和上面的 /orders/new 冲突
@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    with db.connect() as conn:
        return render_detail(conn, get_order(conn, order_id))


def build_timeline(o, logs):
    """状态时间线：下单 → 提醒 → 催货 → 回复 → 收货 → 处理。每一步：(key, 是否完成, 日期)。
    日期取这一步第一次出现在日志里的模拟日期；合成数据没有日志的步骤用订单本身的日期。"""
    first = {}
    for log in reversed(logs):  # logs 是新的在前，倒过来才是按时间顺序
        first.setdefault(log["action"], log["sim_date"])

    def when(*actions):
        dates = [first[a] for a in actions if a in first]
        return min(dates) if dates else None

    received = when("received", "discrepancy") or o["received_at"]
    resolved = when("discrepancy_accepted", "restock_requested", "negotiating")
    if o["status"] == db.DELIVERED and not resolved:
        resolved = received
    return [
        ("tl.ordered", True, when("order_created")),
        ("tl.reminded", bool(when("reminder")), when("reminder")),
        ("tl.chased", bool(when("auto_chase")), when("auto_chase")),
        ("tl.replied", bool(when("reply_analyzed")), when("reply_analyzed")),
        ("tl.received", bool(received), received),
        ("tl.resolved", bool(resolved), resolved),
    ]


def render_detail(conn, order_row, analysis=None, reply_text="", status=200):
    """渲染订单详情页。analysis 是刚做完的回复分析结果（没有就不显示）。"""
    logs = db.get_logs(conn, order_row["id"])  # 页面底部显示这张订单自己的日志
    o = decorate(order_row)
    return render_template("detail.html", o=o, logs=logs, timeline=build_timeline(o, logs),
                           analysis=analysis, reply_text=reply_text, samples=load_sample_replies(),
                           diff_label=diff_label), status


def load_sample_replies():
    """供应商回复样例，给演示用。文件有问题就不显示样例。"""
    try:
        with open(os.path.join(db.DATA_DIR, "sample_replies.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


# ---------- 读懂供应商回复（AI） ----------

def suggest(o, result):
    """根据解析结果给出建议（确定性规则，不是 AI）。
    返回 (建议列表, 修改列表)，每一项都是 (key, 参数)，页面用 t() 翻译，日志用 t_en()。
    修改列表为空表示这条回复不会修改订单。"""
    tips, changes = [], []
    intent = result["intent"]
    if o["status"] not in db.AWAITING:
        return [("tip.closed", {"status": t(f"status.{o['status']}")})], []

    if intent == "partial" and result["quantity"] is not None:
        short = o["quantity"] - result["quantity"]
        if short > 0:
            amount = money(short, o["unit_price"])
            tips.append(("tip.partial_short", {"q": result["quantity"], "unit": o["unit"], "short": short,
                                               "money": f" ≈ {rm(amount)}" if amount is not None else ""}))
        else:
            tips.append(("tip.partial_ok", {"q": result["quantity"], "unit": o["unit"]}))
        changes.append(("change.qty", {"old": o["quantity"], "new": result["quantity"], "unit": o["unit"]}))

    # 延期，或者少货的同时说了新日期（例如 "Jumaat baru boleh hantar, 12 kotak je"）：日期也一起改
    if intent in ("delay", "partial") and result["new_delivery_date"]:
        old = date.fromisoformat(o["delivery_date"])
        new = date.fromisoformat(result["new_delivery_date"])
        days = (new - old).days
        params = {"n": abs(days), "old": o["delivery_date"], "new": new.isoformat()}
        if days > 0:
            tips.append(("tip.later", params))
        elif days < 0:
            tips.append(("tip.earlier", params))
        elif intent == "delay":
            tips.append(("tip.same", {}))
        if new < db.today():
            tips.append(("tip.past", {}))
        if days != 0:
            changes.append(("change.date", {"old": o["delivery_date"], "new": new.isoformat()}))

    if changes:
        return tips, changes
    if intent == "price_change" and result["unit_price"] is not None:
        new_price = result["unit_price"]
        old_price = o["unit_price"]
        if old_price is None:
            tips.append(("tip.price_new", {"new": rm(new_price)}))
        elif new_price == old_price:
            tips.append(("tip.price_same", {"new": rm(new_price)}))
        else:
            diff = new_price - old_price
            key = "tip.price_up" if diff > 0 else "tip.price_down"
            tips.append((key, {"diff": rm(abs(diff)), "unit": o["unit"] or "unit", "old": rm(old_price),
                               "new": rm(new_price), "total": rm(abs(diff) * o["quantity"])}))
        if new_price != old_price:
            changes.append(("change.price", {"old": rm(old_price) if old_price is not None else "—",
                                             "new": rm(new_price)}))
        return tips, changes
    if intent == "confirm":
        tips.append(("tip.confirm", {"date": o["delivery_date"]}))
        return tips, [("change.confirm", {})]
    return tips, []


def join_changes(changes, translate):
    return ", ".join(translate(key, **params) for key, params in changes)


def review_reason(parsed):
    """"需要人工确认"的原因（key, 参数），给页面显示。"""
    r = parsed["result"]
    if r["intent"] == "unclear":
        return ("analysis.reason_unclear", {})
    if r["confidence"] < ai_parser.CONFIDENCE_THRESHOLD:
        return ("analysis.reason_low", {"c": f"{r['confidence']:.2f}"})
    return ("analysis.reason_invalid", {})


@app.route("/orders/<int:order_id>/reply", methods=["POST"])
def analyze_reply(order_id):
    """粘贴供应商回复 → AI 分析。任何角色都可以。
    一般只分析、写日志，不修改订单；只有低风险的结果（见 is_low_risk）会按店主的规则自动应用，店主可以撤销。
    最后再生成一条给供应商的回复草稿。"""
    reply_text = request.form.get("reply_text", "").strip()
    with db.connect() as conn:
        o = get_order(conn, order_id)
        parsed = ai_parser.parse_reply(reply_text, o, db.today())
        tips, changes = suggest(o, parsed["result"])
        parsed.update(tips=tips, change=join_changes(changes, t),
                      can_apply=bool(changes) and not parsed["needs_review"],
                      review_reason=review_reason(parsed) if parsed["needs_review"] else None)
        r = parsed["result"]
        orbyte.write_audit(conn, "reply_analyzed",
                           f"#{order_id} {parsed['mode_note']}"
                           f"{' (' + parsed['mode_reason'] + ')' if parsed['mode_reason'] else ''} "
                           f"read supplier reply \"{reply_text[:200]}\" → "
                           f"intent={r['intent']}, confidence={r['confidence']:.2f}"
                           f"{', needs human check' if parsed['needs_review'] else ''}"
                           f"{'; sent to AI redacted (' + str(parsed['redacted_count']) + ' item(s) hidden)' if parsed['sent_redacted'] else ''}",
                           order_id)
        parsed["auto_applied"] = is_low_risk(o, parsed, changes)
        if parsed["auto_applied"]:
            auto_apply(conn, o, r, changes)
            parsed["can_apply"] = False
            o = get_order(conn, order_id)  # 重新读一次，页面显示更新后的订单
        parsed["draft"] = ai_parser.draft_reply(reply_text, r, o, db.today())
        return render_detail(conn, o, analysis=parsed, reply_text=reply_text)


@app.route("/orders/<int:order_id>/reply/apply", methods=["POST"])
@owner_required("action.apply_reply")
def apply_reply(order_id):
    """店主确认后才真正修改订单。表单把页面上显示的解析结果带回来，后端再校验一次。"""
    form = request.form
    raw = {
        "intent": form.get("intent"),
        "new_delivery_date": form.get("new_delivery_date") or None,
        "quantity": int(form["quantity"]) if form.get("quantity", "").isdigit() else None,
        "unit_price": _to_float(form.get("unit_price")),
        "confidence": _to_float(form.get("confidence")),
        "summary_en": form.get("summary_en", ""),
    }
    result, problems = ai_parser.validate(raw)
    reply_text = form.get("reply_text", "")
    with db.connect() as conn:
        o = get_order(conn, order_id)
        tips, changes = suggest(o, result)
        if problems or result["confidence"] < ai_parser.CONFIDENCE_THRESHOLD or not changes:
            flash(t("flash.reply_rejected"), "error")
            return redirect(url_for("order_detail", order_id=order_id))

        amount = apply_changes(conn, o, result)
        user = orbyte.get_current_user()
        orbyte.write_audit(conn, "reply_applied",
                           f"#{order_id} {user['name']} (owner) confirmed: {join_changes(changes, t_en)}. "
                           f"Original: \"{reply_text[:200]}\". Parsed: {json.dumps(result, ensure_ascii=False)}",
                           order_id, amount=amount)
    flash(t("flash.reply_applied", change=join_changes(changes, t)), "ok")
    return redirect(url_for("order_detail", order_id=order_id))


def apply_changes(conn, o, result):
    """按解析结果修改订单（店主确认时和自动应用时共用）。返回涨价多付的钱（记为"避免的损失"），没有就是 None。
    改完以后，之前那次自动应用就不能再撤销了（清空 auto_undo）。"""
    order_id = o["id"]
    amount = None
    if result["intent"] == "partial":
        conn.execute("UPDATE orders SET quantity = ? WHERE id = ?", (result["quantity"], order_id))
    if result["intent"] in ("delay", "partial") and result["new_delivery_date"]:
        # 改了交货日期：如果之前已经逾期、而新日期还没到，就回到"待送达"
        new_status = db.PENDING if result["new_delivery_date"] >= db.today().isoformat() else o["status"]
        conn.execute("UPDATE orders SET delivery_date = ?, status = ? WHERE id = ?",
                     (result["new_delivery_date"], new_status, order_id))
    if result["intent"] == "price_change":
        conn.execute("UPDATE orders SET unit_price = ? WHERE id = ?", (result["unit_price"], order_id))
        if o["unit_price"] is not None and result["unit_price"] > o["unit_price"]:
            amount = money(o["quantity"], result["unit_price"] - o["unit_price"])
    # confirm：不改订单字段，只写日志
    conn.execute("UPDATE orders SET auto_undo = NULL WHERE id = ?", (order_id,))
    return amount


# ---------- 自动应用低风险更新（店主可以在提醒规则里关掉，也可以撤销） ----------

AUTO_APPLY_CONFIDENCE = 0.8   # 把握度至少要这么高
AUTO_APPLY_MAX_DAYS_LATER = 2  # 延期最多晚这么多天才算低风险


def is_low_risk(o, parsed, changes):
    """这条回复能不能不等店主、直接自动应用。只有两种情况算低风险：
    - 供应商确认按原计划送货（confirm）；
    - 延期（delay），新日期比原定晚 1～2 天，而且不早于今天。
    还必须：这张订单开着"自动应用"、把握度 ≥ 0.8、不需要人工确认、确实有要改的内容。
    少货、涨价、看不懂、把握度低，一律留给店主确认。"""
    r = parsed["result"]
    if not o["auto_apply"] or parsed["needs_review"] or not changes or r["confidence"] < AUTO_APPLY_CONFIDENCE:
        return False
    if r["intent"] == "confirm":
        return True
    if r["intent"] == "delay" and r["new_delivery_date"]:
        new = date.fromisoformat(r["new_delivery_date"])
        days_later = (new - date.fromisoformat(o["delivery_date"])).days
        return 0 < days_later <= AUTO_APPLY_MAX_DAYS_LATER and new >= db.today()
    return False


def auto_apply(conn, o, result, changes):
    """自动应用，并记下撤销需要的信息：改之前和改之后的交货日期、状态，以及改了什么。"""
    before = {"delivery_date": o["delivery_date"], "status": o["status"]}
    apply_changes(conn, o, result)
    new = get_order(conn, o["id"])
    after = {"delivery_date": new["delivery_date"], "status": new["status"]}
    undo = {"before": before, "after": after, "changes": changes, "confidence": result["confidence"]}
    conn.execute("UPDATE orders SET auto_undo = ? WHERE id = ?", (json.dumps(undo), o["id"]))
    orbyte.write_audit(conn, "auto_applied",
                       f"#{o['id']} Applied automatically by the owner's rule (low risk: {result['intent']}, "
                       f"confidence {result['confidence']:.2f} ≥ {AUTO_APPLY_CONFIDENCE:.2f}): "
                       f"{join_changes(changes, t_en)}", o["id"], actor=orbyte.SYSTEM)


def auto_undo_info(o):
    """上一次自动应用的撤销信息。如果订单之后又变过（交货日期或状态和自动应用后的不一样），
    就不能再撤销，返回 None——免得把店主后来做的修改也一起撤掉。"""
    if not o["auto_undo"]:
        return None
    try:
        info = json.loads(o["auto_undo"])
    except ValueError:
        return None
    if any(o[key] != value for key, value in info["after"].items()):
        return None
    return info


@app.route("/orders/<int:order_id>/auto-undo", methods=["POST"])
@owner_required("action.undo_auto")
def undo_auto(order_id):
    """撤销自动应用：交货日期和状态改回原来的样子，写日志。只有店主能撤销。"""
    with db.connect() as conn:
        o = get_order(conn, order_id)
        info = auto_undo_info(o)
        if info is None:
            flash(t("flash.undo_unavailable"), "error")
        else:
            before = info["before"]
            conn.execute("UPDATE orders SET delivery_date = ?, status = ?, auto_undo = NULL WHERE id = ?",
                         (before["delivery_date"], before["status"], order_id))
            orbyte.write_audit(conn, "auto_undone",
                               f"#{order_id} {orbyte.get_current_user()['name']} (owner) undid the automatic "
                               f"update ({join_changes(info['changes'], t_en)}); delivery date back to "
                               f"{before['delivery_date']}, status back to {before['status']}", order_id)
            flash(t("flash.auto_undone"), "ok")
    return redirect(url_for("order_detail", order_id=order_id) + "#next")


@app.route("/orders/<int:order_id>/reply/send", methods=["POST"])
def send_reply(order_id):
    """把回复草稿（可能已经改过）发给供应商：模拟发送，写日志。
    任何角色都可以发：发消息不会修改订单，而且每次都是人点了"发送"才发。"""
    text = request.form.get("message", "").strip()
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if not text or len(text) > ai_parser.MAX_REPLY_LENGTH:
            flash(t("flash.reply_empty"), "error")
        else:
            contact = orbyte.get_supplier_contact(o)
            receipt = orbyte.send_message(contact, text)
            orbyte.write_audit(conn, "reply_sent",
                               f"#{order_id} Reply sent via Orbyte Connect to WhatsApp "
                               f"{receipt['to'] or '(none)'} (simulated):\n\n{text}", order_id)
            flash(t("flash.reply_sent", supplier=contact["name"]), "ok")
    return redirect(url_for("order_detail", order_id=order_id))


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@app.route("/order/<int:order_id>")
def old_order_url(order_id):
    """旧网址 /order/2（单数）已经发出去过，永久跳转（301）到新网址 /orders/2，旧链接不会失效。"""
    return redirect(url_for("order_detail", order_id=order_id), code=301)


@app.route("/orders/<int:order_id>/rules", methods=["POST"])
@owner_required("action.change_rules")  # 后端检查：就算绕过页面上禁用的输入框直接提交，也会被挡住
def update_rules(order_id):
    with db.connect() as conn:
        o = get_order(conn, order_id)
        try:
            remind = int(request.form["remind_days_before"])
            if remind < 0:
                raise ValueError
        except (KeyError, ValueError):
            flash(t("flash.rules_invalid"), "error")
            return redirect(url_for("order_detail", order_id=order_id))
        auto = 1 if request.form.get("auto_chase") else 0
        auto_apply_on = 1 if request.form.get("auto_apply") else 0
        conn.execute("UPDATE orders SET remind_days_before = ?, auto_chase = ?, auto_apply = ? WHERE id = ?",
                     (remind, auto, auto_apply_on, order_id))
        on_off = {0: "off", 1: "on"}
        orbyte.write_audit(conn, "rules_updated",
                           f"#{order_id} Remind {o['remind_days_before']}→{remind} day(s) before, "
                           f"auto-chase {on_off[o['auto_chase']]}→{on_off[auto]}, "
                           f"auto-apply low-risk updates {on_off[o['auto_apply']]}→{on_off[auto_apply_on]}", order_id)
    flash(t("flash.rules_saved"), "ok")
    return redirect(url_for("order_detail", order_id=order_id))


# ---------- 收货核对 ----------

def parse_received_quantity(value):
    """实收数量必须是 0 或以上的整数；不合格返回 None。"""
    try:
        received = int(value)
    except (TypeError, ValueError):
        return None
    return received if received >= 0 else None


def check_delivery(conn, o, received):
    """收货核对（网页表单和 POST /api/delivery-check 共用这一段）：
    记录实收数量和到货日期、判断是否准时、扣 1 Orbyte credit；
    数量不一致时改成 discrepancy，记下少收的货值（避免的损失），并给店主发一条 Orbyte 待办。"""
    order_id = o["id"]
    # 用模拟日期记录到货时间，并和交货日期比较，记下是否准时（以后算供应商准时率）
    received_at = db.today()
    on_time = received_at <= date.fromisoformat(o["delivery_date"])
    status = db.DELIVERED if received == o["quantity"] else db.DISCREPANCY
    conn.execute(
        """UPDATE orders SET status = ?, received_quantity = ?, received_at = ?, on_time = ?
           WHERE id = ?""",
        (status, received, received_at.isoformat(), 1 if on_time else 0, order_id),
    )
    timing = "on time" if on_time else "late"
    result = {"order_id": order_id, "status": status, "received_quantity": received,
              "received_at": received_at.isoformat(), "on_time": on_time,
              "discrepancy": None, "approval": None}
    if status == db.DELIVERED:
        orbyte.write_audit(conn, "received",
                           f"#{order_id} {o['product']}: received {received} {o['unit']}, quantity matches, {timing}",
                           order_id)
    else:
        text = describe_diff(o["quantity"], received, o["unit"], o["unit_price"])
        short = o["quantity"] - received
        loss = money(short, o["unit_price"]) if short > 0 else None  # 只有少收才算避免的损失
        orbyte.write_audit(conn, "discrepancy", f"#{order_id} {o['supplier']} {o['product']}: {text}, {timing}",
                           order_id, amount=loss)
        # 有差异：交给店主决定（人始终掌控），通过 Orbyte 待办通知
        result["discrepancy"] = text
        result["approval"] = orbyte.create_approval(conn, order_id, "Resolve quantity mismatch", text)
    result["credits_used_this_month"] = orbyte.charge_credit(conn, f"delivery check #{order_id}", order_id)
    return result


@app.route("/orders/<int:order_id>/receive", methods=["POST"])
def receive(order_id):
    """确认收货（网页表单）：店主和店员都可以做。"""
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if o["status"] not in db.AWAITING:
            flash(t("flash.already_received"), "error")
            return redirect(url_for("order_detail", order_id=order_id))
        received = parse_received_quantity(request.form.get("received_quantity"))
        if received is None:
            flash(t("flash.qty_invalid"), "error")
            return redirect(url_for("order_detail", order_id=order_id))
        result = check_delivery(conn, o, received)
        updated = decorate(get_order(conn, order_id))
    if result["discrepancy"]:
        flash(t("flash.received_diff", diff=diff_label(updated["diff"])), "error")
    else:
        flash(t("flash.received_ok"), "ok")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/api/delivery-check", methods=["POST"])
def api_delivery_check():
    """对外接口：其他 Orbyte 模块（例如仓库扫码）提交收货数量，返回核对结果（JSON）。
    请求：{"order_id": 2, "received_quantity": 13}。身份来自当前 Orbyte 登录（现在是模拟）。"""
    body = request.get_json(silent=True) or {}
    order_id = body.get("order_id")
    received = parse_received_quantity(body.get("received_quantity"))
    if not isinstance(order_id, int) or received is None:
        return jsonify(error="order_id must be an integer and received_quantity an integer of 0 or more"), 400
    with db.connect() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if o is None:
            return jsonify(error=f"Order #{order_id} does not exist"), 404
        if o["status"] not in db.AWAITING:
            return jsonify(error=f"Order #{order_id} has already been received", status=o["status"]), 409
        result = check_delivery(conn, o, received)
    return jsonify(result), 200


# ---------- 处理差异 ----------

# 店主处理数量差异的三个选择：按钮的值 → (新状态, 日志动作, 日志说明, 提示 key)
RESOLUTIONS = {
    "accept": (db.DELIVERED, "discrepancy_accepted", "accepted the received quantity", "flash.resolved_accept"),
    "restock": (db.RESTOCK_REQUESTED, "restock_requested", "asked the supplier to restock", "flash.resolved_restock"),
    "negotiate": (db.NEGOTIATING, "negotiating", "marked for negotiation", "flash.resolved_negotiate"),
}


@app.route("/orders/<int:order_id>/resolve", methods=["POST"])
@owner_required("action.resolve")
def resolve(order_id):
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if o["status"] not in db.ISSUES:
            flash(t("flash.no_issue"), "error")
            return redirect(url_for("order_detail", order_id=order_id))
        choice = request.form.get("choice")
        if choice not in RESOLUTIONS:
            flash(t("flash.choose"), "error")
            return redirect(url_for("order_detail", order_id=order_id))

        new_status, action, label, message = RESOLUTIONS[choice]
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        text = describe_diff(o["quantity"], o["received_quantity"], o["unit"], o["unit_price"])
        orbyte.write_audit(conn, action, f"#{order_id} {o['product']} ({text}): owner {label}", order_id)
    flash(t(message), "ok")
    return redirect(url_for("order_detail", order_id=order_id))


# ---------- 快进 1 天 ----------

def chase_message(o, supplier):
    """生成礼貌的英文 + 马来文催货消息。supplier 是从 Orbyte 联系人读到的供应商名。"""
    amount = f"{o['quantity']} {o['unit']}".strip()
    english = (
        f"Hello {supplier}, this is a friendly reminder about our order #{o['id']}: "
        f"{amount} of {o['product']}, originally scheduled for delivery on {o['delivery_date']}. "
        f"We have not received it yet. Could you please let us know the new delivery date? Thank you!"
    )
    malay = (
        f"Salam sejahtera {supplier}, ini peringatan mesra mengenai pesanan kami #{o['id']}: "
        f"{o['product']} sebanyak {amount}, yang dijadualkan untuk dihantar pada {o['delivery_date']}. "
        f"Kami masih belum menerimanya. Boleh maklumkan tarikh penghantaran yang baharu? Terima kasih!"
    )
    return f"{english}\n\n{malay}"


@app.route("/time/forward", methods=["POST"])
def time_forward():
    """模拟日期加 1 天，然后检查还没到货的订单：
    - 待送达、进入提醒范围 → 写 reminder 日志
    - 过了交货日期 → 改成逾期；开了自动催货就通过 Orbyte Connect 发催货消息、扣 1 credit，
      没开就写一条说明
    - 已经逾期的订单，开了自动催货的话每天再催一次
    - 同一张订单同一天不重复提醒、不重复催（先用 has_log 查当天有没有写过）"""
    t_day = db.advance_day()
    with db.connect() as conn:
        ff_log_id = orbyte.write_audit(conn, "time_forward", f"Simulated date → {t_day.isoformat()}")
        rows = conn.execute("SELECT * FROM orders WHERE status IN (?, ?)",
                            (db.PENDING, db.OVERDUE)).fetchall()
        for o in rows:
            days_left = (date.fromisoformat(o["delivery_date"]) - t_day).days

            if days_left >= 0:
                # 还没到交货日期：进入提醒范围才提醒；同一天已经提醒过就跳过
                if days_left <= o["remind_days_before"] and not db.has_log(conn, o["id"], "reminder", t_day.isoformat()):
                    orbyte.write_audit(conn, "reminder",
                                       f"Reminder to owner: #{o['id']} {o['product']} is expected on "
                                       f"{o['delivery_date'][5:]}", o["id"], actor=orbyte.SYSTEM)
                continue

            # 已经过了交货日期
            newly_overdue = o["status"] == db.PENDING
            if newly_overdue:
                conn.execute("UPDATE orders SET status = ? WHERE id = ?", (db.OVERDUE, o["id"]))
            if o["auto_chase"]:
                # 同一张订单同一天不重复催：先查今天有没有催过
                if not db.has_log(conn, o["id"], "auto_chase", t_day.isoformat()):
                    contact = orbyte.get_supplier_contact(o)
                    message = chase_message(o, contact["name"])
                    receipt = orbyte.send_message(contact, message)
                    orbyte.write_audit(conn, "auto_chase",
                                       f"#{o['id']} {-days_left} day(s) overdue. Chase message sent via Orbyte "
                                       f"Connect to WhatsApp {receipt['to'] or '(none)'} (simulated):\n\n{message}",
                                       o["id"], actor=orbyte.SYSTEM)
                    orbyte.charge_credit(conn, f"automatic chase #{o['id']}", o["id"])
            elif newly_overdue:
                orbyte.write_audit(conn, "overdue",
                                   f"#{o['id']} {o['product']} is overdue; auto-chase is off by owner's setting",
                                   o["id"], actor=orbyte.SYSTEM)

    # 记住这次快进的日志编号，首页据此显示"这次快进触发了哪些事件"
    session["ff_log_id"] = ff_log_id
    return redirect(url_for("index"))


# ---------- 重置演示数据 ----------

@app.route("/reset", methods=["POST"])
@owner_required("action.reset")  # 会清空所有订单和日志，只能店主做
def reset():
    """演示前一键恢复：清空数据库，重新载入合成数据，模拟日期回到 2026-10-21。"""
    try:
        count = db.reset_db(current_role())
    except (OSError, ValueError) as e:
        # 合成数据文件不见了或写错了：现有数据没有被删。具体原因打印到服务器日志，方便队友修
        print(f"Demo reset failed: {e}")
        flash(t("flash.reset_failed"), "error")
        return redirect(back_url())
    session.pop("ff_log_id", None)  # 旧的快进摘要已经没有意义了
    flash(t("flash.reset_ok", n=count, date=db.DEMO_START.isoformat()), "ok")
    return redirect(url_for("index"))


# ---------- 活动日志 ----------

@app.route("/logs")
def show_logs():
    # 网址里的 ?order=6 表示只看 #6 的日志；没填或填错就显示全部
    order_text = request.args.get("order", "").strip().lstrip("#")
    order_id = int(order_text) if order_text.isdigit() else None
    if order_text and order_id is None:
        flash(t("flash.order_filter"), "error")
    with db.connect() as conn:
        logs = db.get_logs(conn, order_id)
    return render_template("logs.html", logs=logs, order_id=order_id)


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
