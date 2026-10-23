import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

# 项目根目录 = src/ 的上一级；数据库等运行时文件都放在 data/ 里
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
# 数据库和模拟日期文件的位置可以用环境变量改（测试时指向临时文件，不碰真实数据）；
# 没设置时用 data/ 下的默认位置
DB_PATH = os.environ.get("SDC_DB_PATH") or os.path.join(DATA_DIR, "sdc.db")
# 当前模拟日期单独存一个小文件，数据库保持只有 2 张表
SIM_PATH = os.environ.get("SDC_SIM_PATH") or os.path.join(DATA_DIR, "sim_state.json")
# 合成订单数据（队友可以直接编辑这个文件）
SEED_PATH = os.path.join(DATA_DIR, "seed_orders.json")
# 演示的起始日期：第一次启动和重置后，模拟日期都是这一天
DEMO_START = date(2026, 10, 21)
# 马来西亚时间（Asia/Kuala_Lumpur）。马来西亚没有夏令时，全年都是 UTC+8，
# 所以直接写固定的 +8 小时，不需要额外安装时区数据库（Windows 上默认没有）
MALAYSIA_TZ = timezone(timedelta(hours=8), "MYT")

# 订单状态：数据库里存英文代码，页面上用 locales.json 里的 status.<代码> 翻译
PENDING = "pending"                      # 待送达
OVERDUE = "overdue"                      # 逾期
DELIVERED = "delivered"                  # 已送达（数量一致，或店主接受了实收数量）
DISCREPANCY = "discrepancy"              # 实收数量和订单不一致，等店主处理
RESTOCK_REQUESTED = "restock_requested"  # 店主已要求供应商补货
NEGOTIATING = "negotiating"              # 店主标记为待协商

STATUSES = (PENDING, OVERDUE, DELIVERED, DISCREPANCY, RESTOCK_REQUESTED, NEGOTIATING)
# 还没收货、可以"确认收货"的状态
AWAITING = (PENDING, OVERDUE)
# 收货时数量有问题、需要店主处理的状态
ISSUES = (DISCREPANCY, RESTOCK_REQUESTED, NEGOTIATING)
# 等供应商行动的状态（补货、协商）
WAITING = (RESTOCK_REQUESTED, NEGOTIATING)

# 表结构每改一次就把这个数字加 1，启动时发现版本不同就重建
SCHEMA_VERSION = 5

# 日志动作 → (标签文字的 key, 标签颜色)。同一类动作用同一种颜色；不在表里的动作显示灰色"其他"
ACTION_TAGS = {
    "order_created": ("tag.created", "blue"),
    "reminder": ("tag.reminder", "yellow"),
    "overdue": ("tag.reminder", "yellow"),
    "auto_chase": ("tag.chase", "red"),
    "received": ("tag.received", "green"),
    "discrepancy": ("tag.discrepancy", "orange"),
    "discrepancy_accepted": ("tag.discrepancy", "orange"),
    "restock_requested": ("tag.discrepancy", "orange"),
    "negotiating": ("tag.discrepancy", "orange"),
    "permission_denied": ("tag.denied", "purple"),
    "role_switched": ("tag.role", "gray-blue"),
    "demo_reset": ("tag.reset", "gray"),
    "approval_created": ("tag.todo", "orange"),
    "credit_charged": ("tag.credit", "gray-blue"),
    "reply_analyzed": ("tag.ai", "teal"),
    "reply_applied": ("tag.applied", "teal"),
    "auto_applied": ("tag.auto", "teal"),
    "auto_undone": ("tag.undo", "gray"),
    "reply_sent": ("tag.reply_sent", "blue"),
    "feedback_submitted": ("tag.feedback", "blue"),
    "restock_message": ("tag.restock_sent", "orange"),
}
OTHER_TAG = ("tag.other", "gray")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,              -- 供应商名
    whatsapp TEXT NOT NULL,              -- WhatsApp 号码
    product TEXT NOT NULL,               -- 商品名
    category TEXT NOT NULL DEFAULT '',   -- 类别
    quantity INTEGER NOT NULL,           -- 数量
    unit TEXT NOT NULL DEFAULT '',       -- 单位（boxes、bags、kg……）
    unit_price REAL,                     -- 单价（RM），可以不填
    delivery_date TEXT NOT NULL,         -- 交货日期 YYYY-MM-DD
    expiry_date TEXT,                    -- 保质期到期日 YYYY-MM-DD，可以不填
    remind_days_before INTEGER NOT NULL DEFAULT 1, -- 提前几天提醒
    auto_chase INTEGER NOT NULL DEFAULT 0, -- 是否自动催货 0/1
    status TEXT NOT NULL DEFAULT 'pending', -- 见上面的状态列表
    received_quantity INTEGER,           -- 实际到货数量
    received_at TEXT,                    -- 实际到货日期（模拟日期）YYYY-MM-DD
    on_time INTEGER,                     -- 是否准时：1 准时 / 0 迟到 / 空 = 还没到货
    auto_apply INTEGER NOT NULL DEFAULT 1, -- 是否自动应用低风险的供应商回复 0/1（店主可改，默认开）
    auto_undo TEXT                       -- 上一次自动应用的撤销信息（JSON：改之前、改之后、改了什么）；没有就为空
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,             -- 真实时间 YYYY-MM-DD HH:MM:SS
    sim_date TEXT NOT NULL,              -- 当时的模拟日期 YYYY-MM-DD
    role TEXT NOT NULL,                  -- 角色
    action TEXT NOT NULL,                -- 动作（英文代码，见 ACTION_TAGS）
    detail TEXT NOT NULL,                -- 内容
    order_id INTEGER,                    -- 跟哪张订单有关；和订单无关的日志为空
    amount REAL                          -- 这件事避免的损失（RM）：少收的货值、涨价的差额；没有就为空
);

-- 用户反馈（/feedback 页面）。不收集姓名、电话等个人信息。
-- 这是真实用户的调研数据，不是演示数据：重置演示数据时不清空，也不预先填任何内容。
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_at TEXT NOT NULL,          -- 真实时间（马来西亚时间）YYYY-MM-DD HH:MM:SS
    shop_type TEXT NOT NULL,             -- 店铺类型代码，见 app.py 的 SHOP_TYPES
    hours_per_week REAL NOT NULL,        -- 每周跟进供应商大约多少小时
    most_useful TEXT NOT NULL,           -- 最有用的功能代码，见 app.py 的 FEATURES
    improve TEXT NOT NULL DEFAULT '',    -- 最想改进的地方（可以不填）
    willing_to_pay TEXT NOT NULL         -- 愿不愿意每月付费：yes / maybe / no
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 模拟时间 ----------

def today():
    """当前模拟日期。完全不看电脑的真实日期；文件不存在或读不懂时就是演示起始日。"""
    try:
        with open(SIM_PATH, encoding="utf-8") as f:
            return date.fromisoformat(json.load(f)["sim_date"])
    except (OSError, ValueError, KeyError):
        return DEMO_START


def set_today(d):
    with open(SIM_PATH, "w", encoding="utf-8") as f:
        json.dump({"sim_date": d.isoformat()}, f)


def advance_day():
    """模拟日期加 1 天，返回新的日期。"""
    set_today(today() + timedelta(days=1))
    return today()


# ---------- 日志 ----------

def add_log(conn, role, action, detail, order_id=None, amount=None):
    """写一条日志：同时记下真实时间和当时的模拟日期。amount 是这件事避免的损失（RM）。返回日志 id。"""
    cur = conn.execute(
        """INSERT INTO logs (timestamp, sim_date, role, action, detail, order_id, amount)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        # 不管服务器在哪个时区（Replit 是 UTC），都记成马来西亚时间
        (datetime.now(MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S"), today().isoformat(),
         role, action, detail, order_id, amount),
    )
    return cur.lastrowid


def losses_prevented_this_month(conn):
    """本模拟月避免的损失（RM）：发现少收时的货值 + 店主确认的涨价差额，都记在日志的 amount 里。"""
    month = today().isoformat()[:7]
    total = conn.execute("SELECT SUM(amount) FROM logs WHERE amount IS NOT NULL AND sim_date LIKE ?",
                         (month + "%",)).fetchone()[0]
    return total or 0.0


def has_log(conn, order_id, action, sim_date):
    """这张订单在这个模拟日期是不是已经有过这种日志（用来防止同一天重复催货）。"""
    row = conn.execute(
        "SELECT 1 FROM logs WHERE order_id = ? AND action = ? AND sim_date = ? LIMIT 1",
        (order_id, action, sim_date),
    ).fetchone()
    return row is not None


def with_tags(rows):
    """给每条日志贴上标签文字和颜色。"""
    logs = []
    for r in rows:
        log = dict(r)
        log["tag"], log["color"] = ACTION_TAGS.get(log["action"], OTHER_TAG)
        logs.append(log)
    return logs


def get_logs(conn, order_id=None):
    """按时间倒序取日志；给了 order_id 就只取这张订单的。
    id 是按写入顺序自增的，所以 id 越大越新。"""
    if order_id is None:
        rows = conn.execute("SELECT * FROM logs ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM logs WHERE order_id = ? ORDER BY id DESC",
                            (order_id,)).fetchall()
    return with_tags(rows)


# 快进时会触发的事件
EVENT_ACTIONS = ("reminder", "overdue", "auto_chase")


def get_events_after(conn, log_id):
    """取某条日志之后产生的提醒、逾期、催货事件，按发生顺序排列（给快进摘要用）。"""
    rows = conn.execute(
        "SELECT * FROM logs WHERE id > ? AND action IN (?, ?, ?) ORDER BY id",
        (log_id, *EVENT_ACTIONS),
    ).fetchall()
    return with_tags(rows)


# ---------- 合成数据 ----------
# data/seed_orders.json 里有 7 张虚构订单，日期以 2026-10-21 为"今天"，
# 打开就有黄、红、橙、正常各种状态。

def load_seed_orders():
    """读 data/seed_orders.json，检查每张订单，转换成可以直接插入数据库的元组。
    文件有问题时抛出 ValueError，并说明是第几张订单的什么问题——这时还什么都没删。"""
    with open(SEED_PATH, encoding="utf-8") as f:
        items = json.load(f)  # JSON 格式写错时 json 会抛 ValueError，带行号
    rows = []
    for i, o in enumerate(items, start=1):
        try:
            delivery = date.fromisoformat(o["delivery_date"])
            status = o.get("status", PENDING)
            if status not in STATUSES:
                raise ValueError(f"status 只能是 {', '.join(STATUSES)}")
            if int(o["quantity"]) <= 0:
                raise ValueError("quantity 必须大于 0")
            unit_price = o.get("unit_price")
            if unit_price is not None and (isinstance(unit_price, bool) or float(unit_price) < 0):
                raise ValueError("unit_price 必须是 0 或以上的数字")
            received_at = o.get("received_at")
            # 是否准时不用手填：有到货日期就和交货日期比较
            on_time = None if not received_at else int(date.fromisoformat(received_at) <= delivery)
            if o.get("expiry_date"):
                date.fromisoformat(o["expiry_date"])
            rows.append((
                o.get("supplier", ""), o.get("whatsapp", ""), o["product"], o.get("category", ""),
                int(o["quantity"]), o.get("unit", ""),
                float(unit_price) if unit_price is not None else None,
                delivery.isoformat(), o.get("expiry_date"),
                int(o.get("remind_days_before", 1)), 1 if o.get("auto_chase", True) else 0,
                status, o.get("received_quantity"), received_at, on_time,
            ))
        except KeyError as e:
            raise ValueError(f"seed_orders.json 第 {i} 张订单缺少字段 {e}")
        except (ValueError, TypeError) as e:
            raise ValueError(f"seed_orders.json 第 {i} 张订单有问题：{e}")
    return rows


def insert_orders(conn, rows):
    """插入合成订单。已经是"少收"状态的订单，补写一条带金额的差异日志，
    这样仪表盘上的"避免的损失"一打开就有数字。"""
    conn.executemany(
        """INSERT INTO orders (supplier, whatsapp, product, category, quantity, unit, unit_price,
               delivery_date, expiry_date, remind_days_before, auto_chase, status,
               received_quantity, received_at, on_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    for o in conn.execute("SELECT * FROM orders WHERE status IN (?, ?, ?) AND received_quantity < quantity",
                          ISSUES).fetchall():
        short = o["quantity"] - o["received_quantity"]
        amount = round(short * o["unit_price"], 2) if o["unit_price"] is not None else None
        add_log(conn, "系统", "discrepancy",
                f"#{o['id']} {o['supplier']} {o['product']}: short {short} {o['unit']} "
                f"(ordered {o['quantity']}, received {o['received_quantity']})"
                f"{f' ≈ RM {amount:.2f}' if amount is not None else ''} (synthetic history)",
                o["id"], amount)


def init_db():
    with connect() as conn:
        # SQLite 自带的 user_version 用来记表结构版本。和代码里的不一样，
        # 说明表是旧结构：演示数据直接丢掉，按新结构重建
        if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            conn.execute("DROP TABLE IF EXISTS orders")
            conn.execute("DROP TABLE IF EXISTS logs")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
            # 第一次启动（或数据库丢了）：从 JSON 载入合成数据，日期从演示起始日开始
            rows = load_seed_orders()
            set_today(DEMO_START)
            insert_orders(conn, rows)
            add_log(conn, "系统", "seeded", f"First start: loaded {len(rows)} synthetic orders")


def reset_db(role):
    """重置演示数据：清空订单和日志，重新载入 seed_orders.json，模拟日期回到演示起始日。
    返回载入的订单数。JSON 有问题时抛 ValueError，现有数据保持不动。"""
    rows = load_seed_orders()  # 先读、先检查，没问题再删
    set_today(DEMO_START)
    with connect() as conn:
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM logs")
        conn.execute("DELETE FROM sqlite_sequence")  # 让订单编号重新从 #1 开始
        insert_orders(conn, rows)
        add_log(conn, role, "demo_reset",
                f"Demo data reset: loaded {len(rows)} orders, simulated date back to {DEMO_START.isoformat()}")
    return len(rows)
