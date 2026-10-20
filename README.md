# SDC

小店进货跟踪的第一版演示：订单、到期提醒、收货核对、操作日志。

技术栈：Python + Flask + SQLite + 简单 HTML（适配手机）。

## 本地运行

```bash
pip install -r requirements.txt
python app.py
```

打开 http://localhost:5000

## 部署到 Replit

1. Replit 首页 → **Create App** → **Import from GitHub** → 选 `zhukunliang/SDC`。
2. 点 **Run**，右侧 Webview 能看到页面。
3. 点 **Deploy** → 选 **Reserved VM**（`.replit` 里已配置），发布后得到公开网址。

> 用 Reserved VM 而不是 Autoscale：SQLite 是一个本地文件，Autoscale 的实例会被回收，数据会丢。

## 文件

- `app.py`：Flask 路由
- `db.py`：数据库表结构和合成数据
- `templates/`：页面
- `static/style.css`：样式
