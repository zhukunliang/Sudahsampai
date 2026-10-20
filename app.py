import os

from flask import Flask

app = Flask(__name__)


@app.route("/")
def index():
    return "SDC 第一版：Flask 已运行"


if __name__ == "__main__":
    # Replit 需要监听 0.0.0.0，端口优先用环境变量 PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
