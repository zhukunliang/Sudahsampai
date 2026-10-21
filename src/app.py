import os
import secrets
from datetime import date

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

import db

app = Flask(__name__)
# session 签名用的密钥：从环境变量读（Replit 的 Secrets 里设置 SECRET_KEY）。
# 没设置时每次启动随机生成一个，代码里不写任何密钥。
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
db.init_db()


@app.context_processor
def inject_globals():
    return {"sim_today": db.today().isoformat(), "role": current_role(), "roles": (OWNER, CLERK)}


def decorate(row):
    """给订单加上剩余天数和颜色标记：逾期标红，快到期标黄。"""
    o = dict(row)
    days_left = (date.fromisoformat(o["delivery_date"]) - db.today()).days
    o["days_left"] = days_left
    if o["status"] == db.ARRIVED:
        o["flag"] = ""
        o["days_text"] = "—"
    elif o["status"] == db.OVERDUE or days_left < 0:
        o["flag"] = "late"
        o["days_text"] = f"逾期 {-days_left} 天"
    elif days_left <= o["remind_days_before"]:
        o["flag"] = "soon"
        o["days_text"] = "今天到期" if days_left == 0 else f"还剩 {days_left} 天"
    else:
        o["flag"] = ""
        o["days_text"] = f"还剩 {days_left} 天"

    o["diff_text"] = ""
    if o["status"] == db.ARRIVED:
        diff = o["actual_quantity"] - o["quantity"]
        if diff:
            o["diff_text"] = f"少到 {-diff}" if diff < 0 else f"多到 {diff}"
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


OWNER = "店主"
CLERK = "店员"


def current_role():
    return session.get("role", OWNER)


@app.route("/role", methods=["POST"])
def switch_role():
    role = request.form.get("role")
    if role in (OWNER, CLERK) and role != current_role():
        old = current_role()
        session["role"] = role
        with db.connect() as conn:
            db.add_log(conn, role, "切换角色", f"{old} → {role}")
        flash(f"已切换为{role}", "ok")
    return redirect(request.referrer or url_for("index"))


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
        db.add_log(conn, current_role(), "新增订单",
                   f"#{order_id} 向 {data['supplier'] or '（未填供应商）'} 订 {data['product']} "
                   f"{data['quantity']} {data['unit']}，交货 {data['delivery_date']}")
    flash(f"已新增订单 #{order_id}", "ok")
    return redirect(url_for("index"))


@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    with db.connect() as conn:
        o = decorate(get_order(conn, order_id))
    return render_template("detail.html", o=o)


@app.route("/orders/<int:order_id>/rules", methods=["POST"])
def update_rules(order_id):
    role = current_role()
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if role != OWNER:
            db.add_log(conn, role, "拒绝修改规则", f"#{order_id} 店员无权修改提醒规则")
            flash("店员不能修改提醒规则，请切换到店主", "error")
            return render_template("detail.html", o=decorate(o)), 403
        try:
            remind = int(request.form["remind_days_before"])
        except (KeyError, ValueError):
            flash("提前提醒天数要填数字", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        auto = 1 if request.form.get("auto_chase") else 0
        conn.execute("UPDATE orders SET remind_days_before = ?, auto_chase = ? WHERE id = ?",
                     (remind, auto, order_id))
        db.add_log(conn, role, "修改规则",
                   f"#{order_id} 提前提醒 {o['remind_days_before']}→{remind} 天，"
                   f"自动催货 {'开' if o['auto_chase'] else '关'}→{'开' if auto else '关'}")
    flash("提醒规则已保存", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<int:order_id>/receive", methods=["POST"])
def receive(order_id):
    role = current_role()
    with db.connect() as conn:
        o = get_order(conn, order_id)
        if o["status"] == db.ARRIVED:
            flash("这张订单已经收过货了", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        try:
            actual = int(request.form["actual_quantity"])
        except (KeyError, ValueError):
            flash("实际数量要填数字", "error")
            return redirect(url_for("order_detail", order_id=order_id))
        delivered_at = db.now().strftime("%Y-%m-%d %H:%M")
        conn.execute(
            "UPDATE orders SET status = ?, actual_quantity = ?, delivered_at = ? WHERE id = ?",
            (db.ARRIVED, actual, delivered_at, order_id),
        )
        db.add_log(conn, role, "确认收货", f"#{order_id} {o['product']} 实到 {actual}（订购 {o['quantity']}）")
        diff = actual - o["quantity"]
        if diff:
            text = f"少到 {-diff}" if diff < 0 else f"多到 {diff}"
            db.add_log(conn, role, "发现差异", f"#{order_id} {o['supplier']} {o['product']}：{text}")
            flash(f"已收货，数量不一致：{text}", "error")
        else:
            flash("已收货，数量一致", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/time/forward", methods=["POST"])
def time_forward():
    """模拟时间快进 1 天，然后检查待送达订单：进入提醒窗口的写提醒，过了交货日期的改成逾期。"""
    role = current_role()
    db.set_offset(db.get_offset() + 1)
    t = db.today()
    reminders = overdue = 0
    with db.connect() as conn:
        db.add_log(conn, role, "时间快进", f"模拟日期推进到 {t.isoformat()}")
        rows = conn.execute("SELECT * FROM orders WHERE status = ?", (db.PENDING,)).fetchall()
        for o in rows:
            days_left = (date.fromisoformat(o["delivery_date"]) - t).days
            if days_left < 0:
                conn.execute("UPDATE orders SET status = ? WHERE id = ?", (db.OVERDUE, o["id"]))
                db.add_log(conn, "系统", "发现逾期",
                           f"#{o['id']} {o['supplier']} {o['product']} 应于 {o['delivery_date']} 送达，已逾期 {-days_left} 天")
                overdue += 1
            elif days_left <= o["remind_days_before"]:
                when = "今天到期" if days_left == 0 else f"{days_left} 天后到期"
                chase = "，已开启自动催货（第二版发送 WhatsApp）" if o["auto_chase"] else ""
                db.add_log(conn, "系统", "到期提醒",
                           f"#{o['id']} {o['supplier']} {o['product']} {when}{chase}")
                reminders += 1
    flash(f"已快进到 {t.isoformat()}：{reminders} 条到期提醒，{overdue} 张新逾期", "ok")
    return redirect(request.referrer or url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    db.reset_db()
    flash("演示数据已重置", "ok")
    return redirect(url_for("index"))


@app.route("/logs")
def show_logs():
    with db.connect() as conn:
        logs = conn.execute("SELECT * FROM logs ORDER BY id DESC").fetchall()
    return render_template("logs.html", logs=logs)


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
