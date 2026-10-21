import json
import os
import sqlite3
from datetime import date, datetime, timedelta

# 项目根目录 = src/ 的上一级；数据库等运行时文件都放在 data/ 里
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "sdc.db")
# 模拟时间的偏移天数单独存文件，数据库保持只有 2 张表
SIM_PATH = os.path.join(DATA_DIR, "sim_state.json")

# 订单状态：数据库里存英文代码，页面上显示中文
PENDING = "pending"                      # 待送达
OVERDUE = "overdue"                      # 逾期
DELIVERED = "delivered"                  # 已送达（数量一致，或店主接受了实收数量）
DISCREPANCY = "discrepancy"              # 实收数量和订单不一致，等店主处理
RESTOCK_REQUESTED = "restock_requested"  # 店主已要求供应商补货
NEGOTIATING = "negotiating"              # 店主标记为待协商

STATUS_LABELS = {
    PENDING: "待送达",
    OVERDUE: "逾期",
    DELIVERED: "已送达",
    DISCREPANCY: "数量有差异",
    RESTOCK_REQUESTED: "等待补货",
    NEGOTIATING: "待协商",
}
# 还没收货、可以"确认收货"的状态
AWAITING = (PENDING, OVERDUE)
# 收货时数量有问题、需要店主处理的状态
ISSUES = (DISCREPANCY, RESTOCK_REQUESTED, NEGOTIATING)

# 表结构每改一次就把这个数字加 1，启动时发现版本不同就重建
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,              -- 供应商名
    whatsapp TEXT NOT NULL,              -- WhatsApp 号码
    product TEXT NOT NULL,               -- 商品名
    category TEXT NOT NULL DEFAULT '',   -- 类别
    quantity INTEGER NOT NULL,           -- 数量
    unit TEXT NOT NULL DEFAULT '',       -- 单位（包、箱、kg……）
    delivery_date TEXT NOT NULL,         -- 交货日期 YYYY-MM-DD
    expiry_date TEXT,                    -- 保质期到期日 YYYY-MM-DD，可以不填
    remind_days_before INTEGER NOT NULL DEFAULT 1, -- 提前几天提醒
    auto_chase INTEGER NOT NULL DEFAULT 0, -- 是否自动催货 0/1
    status TEXT NOT NULL DEFAULT 'pending', -- 见上面的状态列表
    received_quantity INTEGER,           -- 实际到货数量
    received_at TEXT,                    -- 实际到货日期（模拟日期）YYYY-MM-DD
    on_time INTEGER                      -- 是否准时：1 准时 / 0 迟到 / 空 = 还没到货
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,                  -- 时间（模拟时间）
    role TEXT NOT NULL,                  -- 角色
    action TEXT NOT NULL,                -- 动作
    content TEXT NOT NULL                -- 内容
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 模拟时间 ----------

def get_offset():
    try:
        with open(SIM_PATH, encoding="utf-8") as f:
            return int(json.load(f).get("offset_days", 0))
    except (OSError, ValueError):
        return 0


def set_offset(days):
    with open(SIM_PATH, "w", encoding="utf-8") as f:
        json.dump({"offset_days": days}, f)


def today():
    return date.today() + timedelta(days=get_offset())


def now():
    return datetime.now() + timedelta(days=get_offset())


# ---------- 日志 ----------

def add_log(conn, role, action, content):
    conn.execute(
        "INSERT INTO logs (time, role, action, content) VALUES (?, ?, ?, ?)",
        (now().strftime("%Y-%m-%d %H:%M"), role, action, content),
    )


# ---------- 合成数据 ----------

def seed(conn):
    """7 张虚构订单，日期相对于当前模拟日期，打开就有黄、红、橙、正常各种状态。"""
    t = today()
    d = lambda n: (t + timedelta(days=n)).isoformat()
    rows = [
        # 供应商, WhatsApp, 商品, 类别, 数量, 单位, 交货日期, 保质期, 提前提醒, 自动催货,
        #   状态, 实收数量, 实收日期, 是否准时
        ("Kedai Borong Ah Seng", "+60 12-000 1001", "大米 10kg", "主食", 20, "包", d(5), d(365), 2, 1,
         PENDING, None, None, None),
        ("Segar Sayur Sdn Bhd", "+60 12-000 1002", "青菜", "蔬菜", 15, "箱", d(1), d(6), 2, 1,
         PENDING, None, None, None),
        ("Mewah Minyak Trading", "+60 12-000 1003", "食用油 5L", "油和调料", 30, "桶", d(2), d(540), 3, 0,
         PENDING, None, None, None),
        ("Roti Pagi Bakery", "+60 12-000 1004", "白面包", "烘焙", 40, "条", d(-2), d(2), 1, 1,
         OVERDUE, None, None, None),
        ("Telur Emas Farm", "+60 12-000 1005", "鸡蛋", "蛋奶", 25, "盘", d(-1), d(20), 1, 0,
         DELIVERED, 25, d(-1), 1),
        # 交货日期前天，昨天才到，而且少了 4 瓶：迟到 + 数量差异，等店主处理
        ("Susu Segar Dairy", "+60 12-000 1006", "鲜奶 1L", "蛋奶", 48, "瓶", d(-2), d(4), 1, 1,
         DISCREPANCY, 44, d(-1), 0),
        ("Gula Manis Supplies", "+60 12-000 1007", "白糖 1kg", "油和调料", 50, "包", d(9), None, 3, 0,
         PENDING, None, None, None),
    ]
    conn.executemany(
        """INSERT INTO orders (supplier, whatsapp, product, category, quantity, unit,
               delivery_date, expiry_date, remind_days_before, auto_chase, status,
               received_quantity, received_at, on_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    add_log(conn, "系统", "初始化", f"写入 {len(rows)} 张合成订单")


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
            seed(conn)


def reset_db():
    """清空并重新生成合成数据，模拟时间归零。"""
    set_offset(0)
    with connect() as conn:
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM logs")
        conn.execute("DELETE FROM sqlite_sequence")
        seed(conn)
