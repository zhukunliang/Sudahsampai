import os
from datetime import date, timedelta

from flask import Flask, flash, redirect, render_template, request, url_for

import db

app = Flask(__name__)
# 只用于 flash 消息和角色切换的 cookie 签名，演示用；部署时可在 Replit Secrets 里设置 SECRET_KEY
app.secret_key = os.environ.get("SECRET_KEY", "sdc-demo-only")
db.init_db()


@app.context_processor
def inject_globals():
    return {"sim_today": db.today().isoformat()}


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
    return o


@app.route("/")
def index():
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY delivery_date, id").fetchall()
    return render_template("index.html", orders=[decorate(r) for r in rows])


def current_role():
    return "店主"


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


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
