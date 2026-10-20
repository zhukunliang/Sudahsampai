import os
from datetime import date

from flask import Flask, render_template

import db

app = Flask(__name__)
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


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
