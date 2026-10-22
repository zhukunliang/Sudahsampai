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

## 合成数据和重置

- 合成订单在 `data/seed_orders.json`，队友可以直接编辑。必填字段：`product`、`quantity`（大于 0）、`delivery_date`（YYYY-MM-DD）。
  其他字段可以省略：`status` 默认 `pending`，`remind_days_before` 默认 1，`auto_chase` 默认 `true`。
  `status` 可选：`pending`、`overdue`、`delivered`、`discrepancy`、`restock_requested`、`negotiating`。
  是否准时不用填，有 `received_at` 时会自动和 `delivery_date` 比较。
- 日期都以 **2026-10-21** 为"今天"来写。
- 演示前用店主身份点顶部的 **重置演示数据**：清空数据库、重新载入这个文件、模拟日期回到 2026-10-21。
  如果 JSON 写错了，重置会失败并提示是第几张订单的问题，现有数据不会被删。

## 目录结构

```
src/app.py          Flask 入口和页面路由
src/db.py           数据库操作
src/templates/      HTML 模板
src/static/         CSS
data/               合成数据 seed_orders.json；运行时生成的数据库 sdc.db 和模拟日期 sim_state.json（不提交）
requirements.txt    Python 依赖
.replit             Replit 运行和部署配置
```

## License

Copyright 2026 Team Sudahsampai

本项目采用 [Apache License 2.0](LICENSE) 授权，完整条款见根目录的 `LICENSE` 文件。
