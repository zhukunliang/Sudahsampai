import os

from flask import Flask

import db

app = Flask(__name__)
db.init_db()


@app.route("/")
def index():
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    return f"SDC 第一版：数据库里有 {count} 张订单"


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
