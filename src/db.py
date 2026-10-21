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

PENDING = "待送达"
ARRIVED = "已到货"
OVERDUE = "逾期"

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
    status TEXT NOT NULL DEFAULT '待送达', -- 待送达 / 已到货 / 逾期
    actual_quantity INTEGER,             -- 实际到货数量
    delivered_at TEXT                    -- 实际送达时间
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
    """7 张虚构订单，日期相对于当前模拟日期，打开就有黄、红、正常各种状态。"""
    t = today()
    d = lambda n: (t + timedelta(days=n)).isoformat()
    rows = [
        # 供应商, WhatsApp, 商品, 类别, 数量, 单位, 交货日期, 保质期, 提前提醒, 自动催货, 状态, 实到, 送达时间
        ("Kedai Borong Ah Seng", "+60 12-000 1001", "大米 10kg", "主食", 20, "包", d(5), d(365), 2, 1,
         PENDING, None, None),
        ("Segar Sayur Sdn Bhd", "+60 12-000 1002", "青菜", "蔬菜", 15, "箱", d(1), d(6), 2, 1,
         PENDING, None, None),
        ("Mewah Minyak Trading", "+60 12-000 1003", "食用油 5L", "油和调料", 30, "桶", d(2), d(540), 3, 0,
         PENDING, None, None),
        ("Roti Pagi Bakery", "+60 12-000 1004", "白面包", "烘焙", 40, "条", d(-2), d(2), 1, 1,
         OVERDUE, None, None),
        ("Telur Emas Farm", "+60 12-000 1005", "鸡蛋", "蛋奶", 25, "盘", d(-1), d(20), 1, 0,
         ARRIVED, 25, (t - timedelta(days=1)).isoformat() + " 09:30"),
        ("Susu Segar Dairy", "+60 12-000 1006", "鲜奶 1L", "蛋奶", 48, "瓶", d(-3), d(4), 1, 1,
         ARRIVED, 44, (t - timedelta(days=3)).isoformat() + " 10:15"),
        ("Gula Manis Supplies", "+60 12-000 1007", "白糖 1kg", "油和调料", 50, "包", d(9), None, 3, 0,
         PENDING, None, None),
    ]
    conn.executemany(
        """INSERT INTO orders (supplier, whatsapp, product, category, quantity, unit,
               delivery_date, expiry_date, remind_days_before, auto_chase, status,
               actual_quantity, delivered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    add_log(conn, "系统", "初始化", f"写入 {len(rows)} 张合成订单")


def init_db():
    with connect() as conn:
        # 旧版本的 orders 表没有 category 列：演示数据直接丢掉，按新结构重建
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(orders)")]
        if columns and "category" not in columns:
            conn.execute("DROP TABLE orders")
            conn.execute("DROP TABLE IF EXISTS logs")
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
