# Sudahsampai

给马来西亚小商家用的采购跟进网页应用：记录向供应商下的订单，交货前提醒，逾期催货，收货核对数量，所有操作写入日志。

技术栈：Python 3.12 + Flask + SQLite + Jinja2 模板 + 简单 CSS（适配手机）。

## 本地运行

```bash
pip install -r requirements.txt
python src/app.py
```

打开 http://localhost:5000

## 部署到 Replit

1. Replit 首页 → **Create App** → **Import from GitHub** → 选 `zhukunliang/Sudahsampai`。
2. 点 **Run**，右侧 Webview 能看到页面。
3. 点 **Deploy** → 选 **Reserved VM**（`.replit` 里已配置），发布后得到公开网址。

> 用 Reserved VM 而不是 Autoscale：SQLite 是一个本地文件，Autoscale 的实例会被回收，数据会丢。

## 目录结构

```
src/app.py          Flask 入口和页面路由
src/db.py           数据库操作
src/templates/      HTML 模板
src/static/         CSS
data/               运行时生成的数据库文件；之后的合成数据 seed_orders.json 也放这里
requirements.txt    Python 依赖
.replit             Replit 运行和部署配置
```
