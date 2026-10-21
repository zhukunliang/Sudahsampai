import os
import secrets
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

import db

app = Flask(__name__)
# session 签名用的密钥：从环境变量读（Replit 的 Secrets 里设置 SECRET_KEY）。
# 没设置时每次启动随机生成一个，代码里不写任何密钥。
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
db.init_db()


@app.context_processor
def inject_globals():
    return {"sim_today": db.today().isoformat(), "role": current_role(), "roles": (OWNER, CLERK),
            "is_owner": current_role() == OWNER, "db": db}


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
        o["flag_reason"] = "实收数量和订单不一致，需要店主处理，所以显示橙色。"
    elif o["status"] == db.DELIVERED:
        o["flag"] = ""
        o["days_text"] = "—"
        o["flag_reason"] = "已经送达，不再提醒，所以不标颜色。"
    elif o["status"] == db.OVERDUE or days_left < 0:
        o["flag"] = "late"  # 红色
        o["days_text"] = f"逾期 {-days_left} 天"
        o["flag_reason"] = f"交货日期 {o['delivery_date']} 已经过了 {-days_left} 天还没收到货，所以显示红色。"
    elif days_left <= remind:
        o["flag"] = "soon"  # 黄色
        o["days_text"] = "今天到期" if days_left == 0 else f"还剩 {days_left} 天"
        when = "今天就是交货日期" if days_left == 0 else f"离交货日期还有 {days_left} 天"
        o["flag_reason"] = f"{when}，已进入「提前 {remind} 天提醒」的范围，所以显示黄色。"
    else:
        o["flag"] = ""
        o["days_text"] = f"还剩 {days_left} 天"
        o["flag_reason"] = f"离交货日期还有 {days_left} 天，还没到「提前 {remind} 天提醒」的范围，所以不标颜色。"

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
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY delivery_date, id").fetchall()
    return render_template("index.html", orders=[decorate(r) for r in rows])


# ---------- 角色和权限 ----------

OWNER = "店主"
CLERK = "店员"


def current_role():
    """当前角色存在 session（签名过的 cookie）里。还没选过时默认店主，方便演示；
    万一读到不认识的值，按权限最小的店员处理。"""
    role = session.get("role", OWNER)
    return role if role in (OWNER, CLERK) else CLERK


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
                    db.add_log(conn, current_role(), "permission_denied",
                               f"{prefix}{current_role()}尝试{what}，已拒绝", order_id)
                back = url_for("order_detail", order_id=order_id) if order_id else url_for("index")
                return render_template("denied.html", what=what, back=back), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


@app.route("/role", methods=["POST"])
def switch_role():
    """切换角色（模拟登录）。每次切换都写日志。"""
    role = request.form.get("role")
    if role not in (OWNER, CLERK):
        flash("没有这个角色", "error")
    elif role != current_role():
        old = current_role()
        session["role"] = role
        with db.connect() as conn:
            db.add_log(conn, role, "role_switched", f"{old} → {role}")
        flash(f"已切换为{role}", "ok")
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
        db.add_log(conn, current_role(), "order_created",
                   f"#{order_id} 向 {data['supplier'] or '（未填供应商）'} 订 {data['product']} "
                   f"{data['quantity']} {data['unit']}，交货 {data['delivery_date']}", order_id)
    flash(f"已新增订单 #{order_id}", "ok")
    return redirect(url_for("index"))


@app.route("/order/<int:order_id>")
def order_detail(order_id):
    with db.connect() as conn:
        o = decorate(get_order(conn, order_id))
        logs = db.get_logs(conn, order_id)  # 页面底部显示这张订单自己的日志
    return render_template("detail.html", o=o, logs=logs)


@app.route("/order/<int:order_id>/rules", methods=["POST"])
@owner_required("修改提醒规则")  # 后端检查：就算绕过页面上禁用的输入框直接提交，也会被挡住
def update_rules(order_id):
    role = current_role()
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
        db.add_log(conn, role, "rules_updated",
                   f"#{order_id} 提前提醒 {o['remind_days_before']}→{remind} 天，"
                   f"自动催货 {'开' if o['auto_chase'] else '关'}→{'开' if auto else '关'}", order_id)
    flash("提醒规则已保存", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/order/<int:order_id>/receive", methods=["POST"])
def receive(order_id):
    """确认收货：店主和店员都可以做。"""
    role = current_role()
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if o["status"] not in db.AWAITING:
            flash("这张订单已经收过货了", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        try:
            received = int(request.form["received_quantity"])
            if received < 0:
                raise ValueError
        except (KeyError, ValueError):
            flash("实收数量要填 0 或以上的整数", "error")
            return redirect(url_for("order_detail", order_id=order_id))

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
        if status == db.DELIVERED:
            db.add_log(conn, role, "received",
                       f"#{order_id} {o['product']} 实收 {received} {o['unit']}，数量一致，{timing}", order_id)
            flash("已确认收货，数量一致", "ok")
        else:
            text = describe_diff(o["quantity"], received, o["unit"])
            db.add_log(conn, role, "discrepancy",
                       f"#{order_id} {o['supplier']} {o['product']}：{text}，{timing}", order_id)
            flash("已记录收货，但数量不一致，请店主处理", "error")
    return redirect(url_for("order_detail", order_id=order_id))


# 店主处理数量差异的三个选择：按钮的值 → (新状态, 日志动作, 日志说明)
RESOLUTIONS = {
    "accept": (db.DELIVERED, "discrepancy_accepted", "接受实收数量"),
    "restock": (db.RESTOCK_REQUESTED, "restock_requested", "要求供应商补货"),
    "negotiate": (db.NEGOTIATING, "negotiating", "标记为待协商"),
}


@app.route("/order/<int:order_id>/resolve", methods=["POST"])
@owner_required("处理数量差异")
def resolve(order_id):
    role = current_role()
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
        db.add_log(conn, role, action, f"#{order_id} {o['product']}（{text}）：{label}", order_id)
    flash(f"已{label}", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/time/forward", methods=["POST"])
def time_forward():
    """模拟时间快进 1 天，然后检查待送达订单：进入提醒窗口的写提醒，过了交货日期的改成逾期。"""
    role = current_role()
    db.set_offset(db.get_offset() + 1)
    t = db.today()
    reminders = overdue = 0
    with db.connect() as conn:
        db.add_log(conn, role, "time_forward", f"模拟日期推进到 {t.isoformat()}")
        rows = conn.execute("SELECT * FROM orders WHERE status = ?", (db.PENDING,)).fetchall()
        for o in rows:
            days_left = (date.fromisoformat(o["delivery_date"]) - t).days
            if days_left < 0:
                conn.execute("UPDATE orders SET status = ? WHERE id = ?", (db.OVERDUE, o["id"]))
                db.add_log(conn, "系统", "overdue",
                           f"#{o['id']} {o['supplier']} {o['product']} 应于 {o['delivery_date']} 送达，"
                           f"已逾期 {-days_left} 天", o["id"])
                if o["auto_chase"]:
                    # 只记录"该催货了"，真正发 WhatsApp 是第二版的功能
                    db.add_log(conn, "系统", "chase",
                               f"#{o['id']} 已逾期且开启了自动催货：需要通过 WhatsApp {o['whatsapp']} "
                               f"催 {o['supplier']}（发送功能第二版实现）", o["id"])
                overdue += 1
            elif days_left <= o["remind_days_before"]:
                when = "今天到期" if days_left == 0 else f"{days_left} 天后到期"
                db.add_log(conn, "系统", "reminder",
                           f"#{o['id']} {o['supplier']} {o['product']} {when}", o["id"])
                reminders += 1
    flash(f"已快进到 {t.isoformat()}：{reminders} 条到期提醒，{overdue} 张新逾期", "ok")
    return redirect(back_url())


@app.route("/reset", methods=["POST"])
@owner_required("重置演示数据")  # 会清空所有订单和日志，只能店主做
def reset():
    db.reset_db()
    flash("演示数据已重置", "ok")
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
