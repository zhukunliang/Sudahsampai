"""Sudahsampai 的自动化测试。

运行（在项目根目录 SDC 下）：
    python -m unittest discover tests

测试用的数据库和模拟日期文件放在一个临时文件夹里，测完删除，不会碰 data/sdc.db。
每个测试开始前清空数据，自己插入需要的订单，所以不依赖 data/seed_orders.json 的内容。
"""
import gc
import os
import shutil
import sys
import tempfile
import unittest

# ---- 第 1 步：在导入 app 之前，先把数据库路径指向临时文件夹 ----
# db.py 在导入时读取这两个环境变量，所以顺序很重要：先设置，再导入
TEMP_DIR = tempfile.mkdtemp(prefix="sudahsampai-test-")
os.environ["SDC_DB_PATH"] = os.path.join(TEMP_DIR, "test.db")
os.environ["SDC_SIM_PATH"] = os.path.join(TEMP_DIR, "sim_state.json")

# 让 Python 能找到 src/ 里的 app.py 和 db.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import db  # noqa: E402  （必须在设置环境变量之后导入）
from app import app  # noqa: E402

OWNER = "店主"
STAFF = "店员"


def tearDownModule():
    """所有测试跑完后删除临时文件夹。"""
    gc.collect()  # 关掉还没被回收的数据库连接，Windows 上文件才删得掉
    shutil.rmtree(TEMP_DIR, ignore_errors=True)


class SudahsampaiTest(unittest.TestCase):

    def setUp(self):
        """每个测试前：清空数据，模拟日期回到 2026-10-21，准备一个浏览器（test_client）。"""
        with db.connect() as conn:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM logs")
            conn.execute("DELETE FROM sqlite_sequence")
        db.set_today(db.DEMO_START)
        self.client = app.test_client()

    # ---------- 小工具 ----------

    def add_order(self, **fields):
        """直接往数据库插一张订单，返回订单号。没给的字段用默认值。"""
        order = {
            "supplier": "Segar Sayur Sdn Bhd", "whatsapp": "+60 12-000 1002",
            "product": "青菜", "category": "蔬菜", "quantity": 15, "unit": "箱",
            "delivery_date": "2026-10-22", "expiry_date": None,
            "remind_days_before": 1, "auto_chase": 1, "status": db.PENDING,
            "received_quantity": None, "received_at": None, "on_time": None,
        }
        order.update(fields)
        columns = ", ".join(order)
        marks = ", ".join("?" * len(order))
        with db.connect() as conn:
            cur = conn.execute(f"INSERT INTO orders ({columns}) VALUES ({marks})", list(order.values()))
            return cur.lastrowid

    def get_order(self, order_id):
        with db.connect() as conn:
            return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()

    def logs(self, action):
        with db.connect() as conn:
            return conn.execute("SELECT * FROM logs WHERE action = ? ORDER BY id", (action,)).fetchall()

    def switch_to(self, role):
        self.client.post("/role", data={"role": role})

    # ---------- 1. 新增订单：数量填 0 被拒绝 ----------

    def test_new_order_rejects_zero_quantity(self):
        response = self.client.post("/orders/new", data={
            "supplier": "Kedai Test", "product": "菠菜", "quantity": "0",
            "unit": "箱", "delivery_date": "2026-10-25", "remind_days_before": "1",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Quantity must be greater than 0", response.get_data(as_text=True))  # 界面默认英文
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    # ---------- 2. 确认收货：数量一致 → delivered ----------

    def test_receive_matching_quantity_marks_delivered(self):
        order_id = self.add_order(quantity=15)
        response = self.client.post(f"/orders/{order_id}/receive", data={"received_quantity": "15"})
        self.assertEqual(response.status_code, 302)

        order = self.get_order(order_id)
        self.assertEqual(order["status"], db.DELIVERED)
        self.assertEqual(order["received_quantity"], 15)
        self.assertEqual(order["received_at"], "2026-10-21")  # 用的是模拟日期
        self.assertEqual(order["on_time"], 1)                  # 交货日期 10-22，10-21 收到算准时
        self.assertEqual(len(self.logs("received")), 1)

    # ---------- 3. 确认收货：数量不一致 → discrepancy + 日志 ----------

    def test_receive_different_quantity_marks_discrepancy_and_logs(self):
        order_id = self.add_order(quantity=15)
        self.client.post(f"/orders/{order_id}/receive", data={"received_quantity": "13"})

        order = self.get_order(order_id)
        self.assertEqual(order["status"], db.DISCREPANCY)
        self.assertEqual(order["received_quantity"], 13)

        logs = self.logs("discrepancy")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["order_id"], order_id)
        self.assertIn("Short 2 箱 (ordered 15, received 13)", logs[0]["detail"])  # 日志统一用英文写

    # ---------- 4. 店员改提醒规则 → 403 + permission_denied ----------

    def test_staff_cannot_change_rules(self):
        order_id = self.add_order(remind_days_before=1, auto_chase=1)
        self.switch_to(STAFF)
        response = self.client.post(f"/orders/{order_id}/rules",
                                    data={"remind_days_before": "5"})  # 不勾自动催货

        self.assertEqual(response.status_code, 403)
        order = self.get_order(order_id)
        self.assertEqual(order["remind_days_before"], 1)  # 规则没被改
        self.assertEqual(order["auto_chase"], 1)

        denied = self.logs("permission_denied")
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["role"], STAFF)
        self.assertEqual(denied[0]["order_id"], order_id)
        self.assertEqual(len(self.logs("rules_updated")), 0)

    # ---------- 5. 店员处理数量差异 → 403 ----------

    def test_staff_cannot_resolve_discrepancy(self):
        order_id = self.add_order(quantity=48, status=db.DISCREPANCY,
                                  received_quantity=44, received_at="2026-10-21", on_time=1)
        self.switch_to(STAFF)
        response = self.client.post(f"/orders/{order_id}/resolve", data={"choice": "accept"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.get_order(order_id)["status"], db.DISCREPANCY)  # 状态没变
        self.assertEqual(len(self.logs("permission_denied")), 1)

    # ---------- 6. 快进：逾期 + 自动催货，同一天不重复催 ----------

    def test_fast_forward_overdue_auto_chase_once_per_day(self):
        order_id = self.add_order(delivery_date="2026-10-22", auto_chase=1)

        self.client.post("/time/forward")  # → 10-22，正好是交货日，还不算逾期
        self.assertEqual(self.get_order(order_id)["status"], db.PENDING)
        self.client.post("/time/forward")  # → 10-23，过了交货日期
        self.assertEqual(db.today().isoformat(), "2026-10-23")

        self.assertEqual(self.get_order(order_id)["status"], db.OVERDUE)
        chases = self.logs("auto_chase")
        self.assertEqual(len(chases), 1)
        self.assertEqual(chases[0]["order_id"], order_id)
        self.assertEqual(chases[0]["sim_date"], "2026-10-23")
        self.assertIn("Segar Sayur Sdn Bhd", chases[0]["detail"])  # 催货消息里有供应商名

        # 把日期拨回 10-22 再快进一次，又回到 10-23：同一天不能再催第二次
        db.set_today(db.DEMO_START.replace(day=22))
        self.client.post("/time/forward")
        self.assertEqual(db.today().isoformat(), "2026-10-23")
        self.assertEqual(len(self.logs("auto_chase")), 1)


if __name__ == "__main__":
    unittest.main()
