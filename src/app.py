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

app = Flask(__name__)
# session 签名用的密钥：从环境变量读（Replit 的 Secrets 里设置 SECRET_KEY）。
# 没设置时每次启动随机生成一个，代码里不写任何密钥。
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
db.init_db()


@app.context_processor
def inject_globals():
    with db.connect() as conn:
        credits = orbyte.credits_used_this_month(conn)
    return {"sim_today": db.today().isoformat(), "user": orbyte.get_current_user(),
            "role": current_role(), "roles": (OWNER, CLERK),
            "is_owner": current_role() == OWNER, "credits_used": credits, "db": db}


def describe_diff(quantity, received, unit):
    """例如：少收 2 箱（订单 15，实收 13）"""
    word = "少收" if received < quantity else "多收"
    amount = f"{abs(received - quantity)} {unit}".strip()
    return f"{word} {amount}（订单 {quantity}，实收 {received}）"


def decorate(row):
    """给订单加上：中文状态、剩余天数、颜色标记 flag，以及为什么是这个颜色 flag_reason。"""
    o = dict(row)
    o["status_label"] = db.STATUS_LABELS[o["status"]]
    days_left = (date.fromisoformat(o["delivery_date"]) - db.today()).days
    remind = o["remind_days_before"]

    if o["status"] in db.ISSUES:
        o["flag"] = "diff"  # 橙色
        o["days_text"] = "—"
        o["flag_reason"] = f"实收数量和订单不一致（{o['status_label']}），等店主处理，所以标橙"
    elif o["status"] == db.DELIVERED:
        o["flag"] = ""
        o["days_text"] = "—"
        o["flag_reason"] = "已送达，不再提醒，所以不标颜色"
    elif o["status"] == db.OVERDUE or days_left < 0:
        o["flag"] = "late"  # 红色
        o["days_text"] = f"逾期 {-days_left} 天"
        o["flag_reason"] = f"交货日期 {o['delivery_date']} 已过 {-days_left} 天还没到货，所以标红"
    elif days_left <= remind:
        o["flag"] = "soon"  # 黄色
        o["days_text"] = "今天到期" if days_left == 0 else f"还剩 {days_left} 天"
        when = "今天就是交货日" if days_left == 0 else f"距交货还剩 {days_left} 天"
        o["flag_reason"] = f"提前 {remind} 天提醒，{when}，所以标黄"
    else:
        o["flag"] = ""
        o["days_text"] = f"还剩 {days_left} 天"
        o["flag_reason"] = f"提前 {remind} 天提醒，距交货还剩 {days_left} 天，还没到提醒时间，所以不标颜色"

    # 实收数量和订单不一样时的说明文字
    o["diff_text"] = ""
    if o["received_quantity"] is not None and o["received_quantity"] != o["quantity"]:
        o["diff_text"] = describe_diff(o["quantity"], o["received_quantity"], o["unit"])

    # 迟到几天（准时或还没到货时是 0）
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


@app.route("/")
def index():
    # 刚快进过的话，取出这次快进触发的事件做摘要；pop 表示只显示一次，刷新后就不再显示
    ff_log_id = session.pop("ff_log_id", None)
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY delivery_date, id").fetchall()
        events = db.get_events_after(conn, ff_log_id) if ff_log_id else None
    return render_template("index.html", orders=[decorate(r) for r in rows], events=events)


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
    """装饰器：被它包住的路由只有店主能用。what 是给用户看的操作名称，比如"修改提醒规则"。
    店员访问时不执行原来的函数，而是写一条 permission_denied 日志，显示统一的提示页（403）。"""
    def decorator(view):
        @wraps(view)  # 保留原函数的名字，url_for 才能照常找到它
        def wrapper(*args, **kwargs):
            if current_role() != OWNER:
                order_id = kwargs.get("order_id")  # 订单相关的路由会带 order_id，日志就能关联到订单
                prefix = f"#{order_id} " if order_id else ""
                with db.connect() as conn:
                    orbyte.write_audit(conn, "permission_denied",
                                       f"{prefix}{current_role()}尝试{what}，已拒绝", order_id)
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
        flash("没有这个角色", "error")
    elif role != current_role():
        old = orbyte.get_current_user()
        session["role"] = role  # 模拟登录：真实 Orbyte 里身份来自单点登录，没有这个按钮
        new = orbyte.get_current_user()
        with db.connect() as conn:
            orbyte.write_audit(conn, "role_switched",
                               f"{old['name']}（{old['role']}）→ {new['name']}（{new['role']}）")
        flash(f"已切换为 {new['name']}（{new['role']}）", "ok")
    return redirect(back_url())


ORDER_FIELDS = ["supplier", "whatsapp", "product", "category", "quantity", "unit",
                "delivery_date", "expiry_date", "remind_days_before"]


def read_order_form(form):
    """把表单原样读出来（去掉首尾空格），出错时用来回填。"""
    data = {name: form.get(name, "").strip() for name in ORDER_FIELDS}
    data["auto_chase"] = form.get("auto_chase") == "1"
    return data


def validate_order(data):
    """检查表单，返回错误列表；没有错误就返回空列表。顺便把数字和日期转换好。"""
    errors = []

    if not data["product"]:
        errors.append("商品名不能为空")

    try:
        data["quantity"] = int(data["quantity"])
        if data["quantity"] <= 0:
            errors.append("数量必须大于 0")
    except ValueError:
        errors.append("数量必须是整数")

    if not data["delivery_date"]:
        errors.append("交货日期不能为空")
    else:
        try:
            date.fromisoformat(data["delivery_date"])
        except ValueError:
            errors.append("交货日期格式不对")

    if data["expiry_date"]:  # 保质期可以不填，填了就要是正确的日期
        try:
            date.fromisoformat(data["expiry_date"])
        except ValueError:
            errors.append("保质期格式不对")

    try:
        data["remind_days_before"] = int(data["remind_days_before"] or 1)
        if data["remind_days_before"] < 0:
            errors.append("提前提醒天数不能是负数")
    except ValueError:
        errors.append("提前提醒天数必须是整数")

    return errors


@app.route("/orders/new", methods=["GET", "POST"])
def new_order():
    if request.method == "GET":
        empty = {name: "" for name in ORDER_FIELDS}
        empty.update(remind_days_before=1, auto_chase=True)  # 默认提前 1 天提醒，默认开启自动催货
        return render_template("new.html", form=empty, errors=[])

    data = read_order_form(request.form)
    errors = validate_order(data)
    if errors:
        # 400 = 请求有误；把已填写的内容 data 传回去，表单不会被清空
        return render_template("new.html", form=data, errors=errors), 400

    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO orders (supplier, whatsapp, product, category, quantity, unit,
                   delivery_date, expiry_date, remind_days_before, auto_chase)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["supplier"], data["whatsapp"], data["product"], data["category"],
             data["quantity"], data["unit"], data["delivery_date"],
             data["expiry_date"] or None, data["remind_days_before"], 1 if data["auto_chase"] else 0),
        )
        order_id = cur.lastrowid
        # 日志：role 列记录"谁"，内容记录"新增了什么"
        orbyte.write_audit(conn, "order_created",
                   f"#{order_id} 向 {data['supplier'] or '（未填供应商）'} 订 {data['product']} "
                   f"{data['quantity']} {data['unit']}，交货 {data['delivery_date']}", order_id)
    flash(f"已新增订单 #{order_id}", "ok")
    return redirect(url_for("index"))


# 订单详情：/orders/2。<int:...> 只匹配数字，所以不会和上面的 /orders/new 冲突
@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    with db.connect() as conn:
        return render_detail(conn, get_order(conn, order_id))


def render_detail(conn, order_row, analysis=None, reply_text="", status=200):
    """渲染订单详情页。analysis 是刚做完的 AI 分析结果（没有就不显示）。"""
    logs = db.get_logs(conn, order_row["id"])  # 页面底部显示这张订单自己的日志
    return render_template("detail.html", o=decorate(order_row), logs=logs, analysis=analysis,
                           reply_text=reply_text, samples=load_sample_replies()), status


def load_sample_replies():
    """data/sample_replies.json 里的供应商回复样例，给演示用。文件有问题就不显示样例。"""
    try:
        with open(os.path.join(db.DATA_DIR, "sample_replies.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


# ---------- 读懂供应商回复（AI） ----------

def suggest(o, result):
    """根据解析结果给出建议（确定性规则，不是 AI）。返回 (建议列表, 确认后会做的修改说明或 None)。"""
    tips, changes = [], []
    intent = result["intent"]
    if o["status"] not in db.AWAITING:
        return [f"这张订单已经是「{db.STATUS_LABELS[o['status']]}」，不再根据回复修改。"], None

    if intent == "partial" and result["quantity"] is not None:
        short = o["quantity"] - result["quantity"]
        if short > 0:
            tips.append(f"供应商这次只能送 {result['quantity']} {o['unit']}，比订单少 {short} {o['unit']}。"
                        f"剩下的需要另外下单或跟进。")
        else:
            tips.append(f"供应商说送 {result['quantity']} {o['unit']}，不少于订单数量 {o['quantity']}。")
        changes.append(f"订购数量 {o['quantity']} → {result['quantity']} {o['unit']}")

    # 延期，或者少货的同时说了新日期（例如 "Jumaat baru boleh hantar, 12 kotak je"）：日期也一起改
    if intent in ("delay", "partial") and result["new_delivery_date"]:
        old = date.fromisoformat(o["delivery_date"])
        new = date.fromisoformat(result["new_delivery_date"])
        days = (new - old).days
        if days > 0:
            tips.append(f"新交货日期比原定晚 {days} 天（{o['delivery_date']} → {new.isoformat()}）。"
                        f"系统没有库存数据，请确认现有 {o['product']} 能撑到那天，否则可能缺货。")
        elif days < 0:
            tips.append(f"新交货日期比原定早 {-days} 天（{o['delivery_date']} → {new.isoformat()}）。")
        elif intent == "delay":
            tips.append("新交货日期和原定一样。")
        if new < db.today():
            tips.append("⚠ 这个日期已经过去了，可能解析错了，请核对原文。")
        if days != 0:
            changes.append(f"交货日期 {o['delivery_date']} → {new.isoformat()}")

    if changes:
        return tips, "，".join(changes)
    if intent == "price_change" and result["unit_price"] is not None:
        tips.append(f"供应商说单价改为 RM {result['unit_price']:g}。系统目前不保存单价，"
                    f"确认后只记录在日志里，请自行核对成本。")
        return tips, f"记录新单价 RM {result['unit_price']:g}（不修改订单字段）"
    if intent == "confirm":
        tips.append(f"供应商确认会按原定 {o['delivery_date']} 送货。")
        return tips, "记录供应商已确认（不修改订单字段）"
    return tips, None


@app.route("/orders/<int:order_id>/reply", methods=["POST"])
def analyze_reply(order_id):
    """粘贴供应商回复 → AI 分析。任何角色都可以；只分析、写日志，不修改订单。"""
    reply_text = request.form.get("reply_text", "").strip()
    with db.connect() as conn:
        o = get_order(conn, order_id)
        parsed = ai_parser.parse_reply(reply_text, o, db.today())
        tips, change = suggest(o, parsed["result"])
        parsed.update(tips=tips, change=change, can_apply=bool(change) and not parsed["needs_review"])
        r = parsed["result"]
        orbyte.write_audit(conn, "reply_analyzed",
                           f"#{order_id} {parsed['mode_note']}"
                           f"{'（' + parsed['mode_reason'] + '）' if parsed['mode_reason'] else ''} "
                           f"解析供应商回复「{reply_text[:200]}」→ "
                           f"intent={r['intent']}, confidence={r['confidence']:.2f}"
                           f"{'，需要人工确认' if parsed['needs_review'] else ''}", order_id)
        return render_detail(conn, o, analysis=parsed, reply_text=reply_text)


@app.route("/orders/<int:order_id>/reply/apply", methods=["POST"])
@owner_required("确认更新订单（根据供应商回复）")
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
        tips, change = suggest(o, result)
        if problems or result["confidence"] < ai_parser.CONFIDENCE_THRESHOLD or not change:
            flash("这条解析结果需要人工确认，订单没有修改。", "error")
            return redirect(url_for("order_detail", order_id=order_id))

        if result["intent"] == "partial":
            conn.execute("UPDATE orders SET quantity = ? WHERE id = ?", (result["quantity"], order_id))
        if result["intent"] in ("delay", "partial") and result["new_delivery_date"]:
            # 改了交货日期：如果之前已经逾期、而新日期还没到，就回到"待送达"
            new_status = db.PENDING if result["new_delivery_date"] >= db.today().isoformat() else o["status"]
            conn.execute("UPDATE orders SET delivery_date = ?, status = ? WHERE id = ?",
                         (result["new_delivery_date"], new_status, order_id))
        # price_change / confirm：不改订单字段，只写日志

        user = orbyte.get_current_user()
        orbyte.write_audit(conn, "reply_applied",
                           f"#{order_id} 店主 {user['name']} 确认更新：{change}。"
                           f"原文：「{reply_text[:200]}」。解析结果：{json.dumps(result, ensure_ascii=False)}",
                           order_id)
    flash(f"已更新订单：{change}", "ok")
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
@owner_required("修改提醒规则")  # 后端检查：就算绕过页面上禁用的输入框直接提交，也会被挡住
def update_rules(order_id):
    with db.connect() as conn:
        o = get_order(conn, order_id)
        try:
            remind = int(request.form["remind_days_before"])
            if remind < 0:
                raise ValueError
        except (KeyError, ValueError):
            flash("提前提醒天数要填 0 或以上的整数", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        auto = 1 if request.form.get("auto_chase") else 0
        conn.execute("UPDATE orders SET remind_days_before = ?, auto_chase = ? WHERE id = ?",
                     (remind, auto, order_id))
        orbyte.write_audit(conn, "rules_updated",
                   f"#{order_id} 提前提醒 {o['remind_days_before']}→{remind} 天，"
                   f"自动催货 {'开' if o['auto_chase'] else '关'}→{'开' if auto else '关'}", order_id)
    flash("提醒规则已保存", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


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
    数量不一致时改成 discrepancy，并给店主发一条 Orbyte 待办。返回结果字典。"""
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
    timing = "准时" if on_time else "迟到"
    result = {"order_id": order_id, "status": status, "received_quantity": received,
              "received_at": received_at.isoformat(), "on_time": on_time,
              "discrepancy": None, "approval": None}
    if status == db.DELIVERED:
        orbyte.write_audit(conn, "received",
                           f"#{order_id} {o['product']} 实收 {received} {o['unit']}，数量一致，{timing}", order_id)
    else:
        text = describe_diff(o["quantity"], received, o["unit"])
        orbyte.write_audit(conn, "discrepancy",
                           f"#{order_id} {o['supplier']} {o['product']}：{text}，{timing}", order_id)
        # 有差异：交给店主决定（人始终掌控），通过 Orbyte 待办通知
        result["discrepancy"] = text
        result["approval"] = orbyte.create_approval(conn, order_id, "处理数量差异", text)
    result["credits_used_this_month"] = orbyte.charge_credit(conn, f"收货核对 #{order_id}", order_id)
    return result


@app.route("/orders/<int:order_id>/receive", methods=["POST"])
def receive(order_id):
    """确认收货（网页表单）：店主和店员都可以做。"""
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if o["status"] not in db.AWAITING:
            flash("这张订单已经收过货了", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        received = parse_received_quantity(request.form.get("received_quantity"))
        if received is None:
            flash("实收数量要填 0 或以上的整数", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        result = check_delivery(conn, o, received)
    if result["discrepancy"]:
        flash(f"数量不一致：{result['discrepancy']}。已发送到店主的 Orbyte 待办", "error")
    else:
        flash("已确认收货，数量一致", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/api/delivery-check", methods=["POST"])
def api_delivery_check():
    """对外接口：其他 Orbyte 模块（例如仓库扫码）提交收货数量，返回核对结果（JSON）。
    请求：{"order_id": 2, "received_quantity": 13}。身份来自当前 Orbyte 登录（现在是模拟）。"""
    body = request.get_json(silent=True) or {}
    order_id = body.get("order_id")
    received = parse_received_quantity(body.get("received_quantity"))
    if not isinstance(order_id, int) or received is None:
        return jsonify(error="order_id 必须是整数，received_quantity 必须是 0 或以上的整数"), 400
    with db.connect() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if o is None:
            return jsonify(error=f"订单 #{order_id} 不存在"), 404
        if o["status"] not in db.AWAITING:
            return jsonify(error=f"订单 #{order_id} 已经收过货了", status=o["status"]), 409
        result = check_delivery(conn, o, received)
    return jsonify(result), 200


# 店主处理数量差异的三个选择：按钮的值 → (新状态, 日志动作, 日志说明)
RESOLUTIONS = {
    "accept": (db.DELIVERED, "discrepancy_accepted", "接受实收数量"),
    "restock": (db.RESTOCK_REQUESTED, "restock_requested", "要求供应商补货"),
    "negotiate": (db.NEGOTIATING, "negotiating", "标记为待协商"),
}


@app.route("/orders/<int:order_id>/resolve", methods=["POST"])
@owner_required("处理数量差异")
def resolve(order_id):
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if o["status"] not in db.ISSUES:
            flash("这张订单没有需要处理的数量差异", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        choice = request.form.get("choice")
        if choice not in RESOLUTIONS:
            flash("请选择一个处理方式", "error")
            return redirect(url_for("order_detail", order_id=order_id))

        new_status, action, label = RESOLUTIONS[choice]
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        text = describe_diff(o["quantity"], o["received_quantity"], o["unit"])
        orbyte.write_audit(conn, action, f"#{order_id} {o['product']}（{text}）：{label}", order_id)
    flash(f"已{label}", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


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
    t = db.advance_day()
    with db.connect() as conn:
        ff_log_id = orbyte.write_audit(conn, "time_forward", f"模拟日期 → {t.isoformat()}")
        rows = conn.execute("SELECT * FROM orders WHERE status IN (?, ?)",
                            (db.PENDING, db.OVERDUE)).fetchall()
        for o in rows:
            days_left = (date.fromisoformat(o["delivery_date"]) - t).days

            if days_left >= 0:
                # 还没到交货日期：进入提醒范围才提醒；同一天已经提醒过就跳过
                if days_left <= o["remind_days_before"] and not db.has_log(conn, o["id"], "reminder", t.isoformat()):
                    orbyte.write_audit(conn, "reminder",
                                       f"提醒店主：#{o['id']} {o['product']}预计 {o['delivery_date'][5:]} 送达",
                                       o["id"], actor=orbyte.SYSTEM)
                continue

            # 已经过了交货日期
            newly_overdue = o["status"] == db.PENDING
            if newly_overdue:
                conn.execute("UPDATE orders SET status = ? WHERE id = ?", (db.OVERDUE, o["id"]))
            if o["auto_chase"]:
                # 同一张订单同一天不重复催：先查今天有没有催过
                if not db.has_log(conn, o["id"], "auto_chase", t.isoformat()):
                    contact = orbyte.get_supplier_contact(o)
                    message = chase_message(o, contact["name"])
                    receipt = orbyte.send_message(contact, message)
                    orbyte.write_audit(conn, "auto_chase",
                                       f"#{o['id']} 已逾期 {-days_left} 天，催货消息已通过 Orbyte Connect 发往 "
                                       f"WhatsApp {receipt['to'] or '未填'}（模拟发送）：\n\n{message}",
                                       o["id"], actor=orbyte.SYSTEM)
                    orbyte.charge_credit(conn, f"自动催货 #{o['id']}", o["id"])
            elif newly_overdue:
                orbyte.write_audit(conn, "overdue", f"#{o['id']} {o['product']} 已逾期，按店主设置不自动催货",
                                   o["id"], actor=orbyte.SYSTEM)

    # 记住这次快进的日志编号，首页据此显示"这次快进触发了哪些事件"
    session["ff_log_id"] = ff_log_id
    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
@owner_required("重置演示数据")  # 会清空所有订单和日志，只能店主做
def reset():
    """演示前一键恢复：清空数据库，重新载入 data/seed_orders.json，模拟日期回到 2026-10-21。"""
    try:
        count = db.reset_db(current_role())
    except (OSError, ValueError) as e:
        # seed_orders.json 不见了或写错了：现有数据没有被删，把问题告诉用户
        flash(f"重置失败，现有数据没有改动。原因：{e}", "error")
        return redirect(back_url())
    session.pop("ff_log_id", None)  # 旧的快进摘要已经没有意义了
    flash(f"演示数据已重置：载入 {count} 张订单，模拟日期回到 {db.DEMO_START.isoformat()}", "ok")
    return redirect(url_for("index"))


@app.route("/logs")
def show_logs():
    # 网址里的 ?order=6 表示只看 #6 的日志；没填或填错就显示全部
    order_text = request.args.get("order", "").strip().lstrip("#")
    order_id = int(order_text) if order_text.isdigit() else None
    if order_text and order_id is None:
        flash("订单编号要填数字", "error")
    with db.connect() as conn:
        logs = db.get_logs(conn, order_id)
    return render_template("logs.html", logs=logs, order_id=order_id)


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
