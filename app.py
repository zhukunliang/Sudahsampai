import os
from datetime import date, timedelta

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

import db

app = Flask(__name__)
# 只用于 flash 消息和角色切换的 cookie 签名，演示用；部署时可在 Replit Secrets 里设置 SECRET_KEY
app.secret_key = os.environ.get("SECRET_KEY", "sdc-demo-only")
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
    o["expires_on"] = ""
    if o["status"] == db.ARRIVED:
        diff = o["actual_quantity"] - o["quantity"]
        if diff:
            o["diff_text"] = f"少到 {-diff}" if diff < 0 else f"多到 {diff}"
        arrived_on = date.fromisoformat(o["delivered_at"][:10])
        o["expires_on"] = (arrived_on + timedelta(days=o["shelf_life_days"])).isoformat()
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


@app.route("/orders/new", methods=["GET", "POST"])
def new_order():
    if request.method == "GET":
        form = {
            "supplier": "", "whatsapp": "", "product": "", "quantity": "",
            "delivery_date": (db.today() + timedelta(days=3)).isoformat(),
            "shelf_life_days": 30, "remind_days_before": 1, "auto_chase": True,
        }
        return render_template("new.html", form=form)

    f = request.form
    try:
        values = (
            f["supplier"].strip(), f["whatsapp"].strip(), f["product"].strip(),
            int(f["quantity"]), date.fromisoformat(f["delivery_date"]).isoformat(),
            int(f["shelf_life_days"]), int(f["remind_days_before"]),
            1 if f.get("auto_chase") else 0,
        )
    except (KeyError, ValueError):
        flash("表单有误，请检查数字和日期", "error")
        return render_template("new.html", form=f), 400
    if not all(values[:3]):
        flash("供应商、WhatsApp、商品不能为空", "error")
        return render_template("new.html", form=f), 400

    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO orders (supplier, whatsapp, product, quantity, delivery_date,
                   shelf_life_days, remind_days_before, auto_chase)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        order_id = cur.lastrowid
        db.add_log(conn, current_role(), "新增订单",
                   f"#{order_id} {values[0]}：{values[2]} × {values[3]}，交货 {values[4]}")
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


@app.route("/logs")
def show_logs():
    with db.connect() as conn:
        logs = conn.execute("SELECT * FROM logs ORDER BY id DESC").fetchall()
    return render_template("logs.html", logs=logs)


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
