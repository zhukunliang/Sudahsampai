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
from unittest import mock

# ---- 第 1 步：在导入 app 之前，先把数据库路径指向临时文件夹 ----
# db.py 在导入时读取这两个环境变量，所以顺序很重要：先设置，再导入
TEMP_DIR = tempfile.mkdtemp(prefix="sudahsampai-test-")
os.environ["SDC_DB_PATH"] = os.path.join(TEMP_DIR, "test.db")
os.environ["SDC_SIM_PATH"] = os.path.join(TEMP_DIR, "sim_state.json")

# 让 Python 能找到 src/ 里的 app.py 和 db.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import ai_parser  # noqa: E402
import db  # noqa: E402  （必须在设置环境变量之后导入）
from app import app  # noqa: E402

OWNER = "店主"
STAFF = "店员"


def tearDownModule():
    """所有测试跑完后删除临时文件夹。"""
    gc.collect()  # 关掉还没被回收的数据库连接，Windows 上文件才删得掉
    shutil.rmtree(TEMP_DIR, ignore_errors=True)


class AppTestCase(unittest.TestCase):
    """共用的准备工作和小工具（本身没有测试）。"""

    def setUp(self):
        """每个测试前：清空数据，模拟日期回到 2026-10-21，准备一个浏览器（test_client）。"""
        with db.connect() as conn:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM logs")
            conn.execute("DELETE FROM feedback")
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


class SudahsampaiTest(AppTestCase):

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


class AutoApplyTest(AppTestCase):
    """自动应用低风险更新、撤销、回复草稿。用假的模型代替真实 AI：不联网、不花钱。"""

    FAKE_AI_ENV = {"AI_BASE_URL": "http://localhost:9/v1", "AI_API_KEY": "test-key-not-real",
                   "AI_MODEL": "test-model"}

    def analyze(self, order_id, intent, confidence=0.9, new_date=None, quantity=None, unit_price=None):
        """模拟"AI 读出了这样的结果"，然后像页面一样提交"分析回复"。草稿模型故意报错，走模板。"""
        result = {"intent": intent, "new_delivery_date": new_date, "quantity": quantity,
                  "unit_price": unit_price, "confidence": confidence, "summary_en": "Test reply."}

        def broken_draft(*args):
            raise ConnectionError("no AI in tests")
        with mock.patch.dict(os.environ, self.FAKE_AI_ENV), \
             mock.patch.object(ai_parser, "_call_model", lambda *a: dict(result)), \
             mock.patch.object(ai_parser, "_call_draft_model", broken_draft):
            return self.client.post(f"/orders/{order_id}/reply", data={"reply_text": "Test reply from supplier"})

    # ---------- 低风险：自动应用 ----------

    def test_small_delay_with_high_confidence_is_applied_automatically(self):
        order_id = self.add_order(delivery_date="2026-10-22")
        response = self.analyze(order_id, "delay", 0.9, new_date="2026-10-24")  # 晚 2 天
        self.assertEqual(self.get_order(order_id)["delivery_date"], "2026-10-24")
        self.assertEqual(len(self.logs("auto_applied")), 1)
        self.assertIn("2026-10-22 → 2026-10-24", self.logs("auto_applied")[0]["detail"])
        self.assertIn("Updated automatically by your rule", response.get_data(as_text=True))

    def test_confirm_with_high_confidence_is_applied_automatically(self):
        order_id = self.add_order(delivery_date="2026-10-22")
        self.analyze(order_id, "confirm", 0.85)
        self.assertEqual(len(self.logs("auto_applied")), 1)
        self.assertEqual(self.get_order(order_id)["delivery_date"], "2026-10-22")  # confirm 不改字段

    # ---------- 高风险或规则不允许：不自动应用，订单不变 ----------

    def test_risky_or_unsure_results_wait_for_the_owner(self):
        cases = {
            "partial": dict(intent="partial", confidence=0.95, quantity=10),
            "price_change": dict(intent="price_change", confidence=0.95, unit_price=25.0),
            "delay_3_days": dict(intent="delay", confidence=0.95, new_date="2026-10-25"),
            "delay_earlier": dict(intent="delay", confidence=0.95, new_date="2026-10-21"),
            "low_confidence": dict(intent="delay", confidence=0.79, new_date="2026-10-23"),
            "unclear": dict(intent="unclear", confidence=0.9),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                order_id = self.add_order(delivery_date="2026-10-22", quantity=15, unit_price=20.0)
                response = self.analyze(order_id, **kwargs)
                order = self.get_order(order_id)
                self.assertEqual(order["delivery_date"], "2026-10-22")
                self.assertEqual(order["quantity"], 15)
                self.assertEqual(order["unit_price"], 20.0)
                self.assertIsNone(order["auto_undo"])
                self.assertNotIn("Updated automatically by your rule", response.get_data(as_text=True))
        self.assertEqual(self.logs("auto_applied"), [])

    def test_rule_switched_off_means_no_automatic_update(self):
        order_id = self.add_order(delivery_date="2026-10-22", auto_apply=0)
        self.analyze(order_id, "delay", 0.95, new_date="2026-10-23")
        self.assertEqual(self.get_order(order_id)["delivery_date"], "2026-10-22")
        self.assertEqual(self.logs("auto_applied"), [])

    def test_owner_can_switch_the_rule_off_but_staff_cannot(self):
        order_id = self.add_order()
        self.switch_to(STAFF)
        self.client.post(f"/orders/{order_id}/rules", data={"remind_days_before": "1", "auto_chase": "1"})
        self.assertEqual(self.get_order(order_id)["auto_apply"], 1)  # 店员改不了
        self.switch_to(OWNER)
        self.client.post(f"/orders/{order_id}/rules", data={"remind_days_before": "1", "auto_chase": "1"})
        self.assertEqual(self.get_order(order_id)["auto_apply"], 0)  # 没勾选 = 关
        self.assertIn("auto-apply low-risk updates on→off", self.logs("rules_updated")[-1]["detail"])

    # ---------- 撤销 ----------

    def test_owner_can_undo_an_automatic_update(self):
        order_id = self.add_order(delivery_date="2026-10-22")
        self.analyze(order_id, "delay", 0.9, new_date="2026-10-23")
        self.client.post(f"/orders/{order_id}/auto-undo")
        order = self.get_order(order_id)
        self.assertEqual(order["delivery_date"], "2026-10-22")
        self.assertEqual(order["status"], db.PENDING)
        self.assertIsNone(order["auto_undo"])
        self.assertEqual(len(self.logs("auto_undone")), 1)
        self.assertIn("back to 2026-10-22", self.logs("auto_undone")[0]["detail"])

    def test_undo_brings_an_overdue_order_back_to_overdue(self):
        db.set_today(db.DEMO_START)  # 10-21；订单 10-20 就该到了
        order_id = self.add_order(delivery_date="2026-10-20", status=db.OVERDUE)
        self.analyze(order_id, "delay", 0.9, new_date="2026-10-22")
        self.assertEqual(self.get_order(order_id)["status"], db.PENDING)
        self.client.post(f"/orders/{order_id}/auto-undo")
        order = self.get_order(order_id)
        self.assertEqual((order["delivery_date"], order["status"]), ("2026-10-20", db.OVERDUE))

    def test_staff_cannot_undo(self):
        order_id = self.add_order(delivery_date="2026-10-22")
        self.analyze(order_id, "delay", 0.9, new_date="2026-10-23")
        self.switch_to(STAFF)
        response = self.client.post(f"/orders/{order_id}/auto-undo")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.get_order(order_id)["delivery_date"], "2026-10-23")  # 没被撤销
        self.assertEqual(self.logs("auto_undone"), [])
        self.assertEqual(len(self.logs("permission_denied")), 1)

    def test_cannot_undo_after_the_order_changed(self):
        order_id = self.add_order(delivery_date="2026-10-22")
        self.analyze(order_id, "delay", 0.9, new_date="2026-10-23")
        self.client.post(f"/orders/{order_id}/receive", data={"received_quantity": "15"})  # 之后已经收货
        self.client.post(f"/orders/{order_id}/auto-undo")
        order = self.get_order(order_id)
        self.assertEqual((order["delivery_date"], order["status"]), ("2026-10-23", db.DELIVERED))
        self.assertEqual(self.logs("auto_undone"), [])

    # ---------- 回复草稿 ----------

    def test_reply_draft_is_shown_and_can_be_sent(self):
        order_id = self.add_order(quantity=15, unit="箱")
        page = self.analyze(order_id, "partial", 0.9, quantity=12).get_data(as_text=True)
        self.assertIn("remaining 3 箱", page)       # 模板草稿：问剩下的什么时候补
        self.assertIn("baki 3 箱", page)            # 马来文
        self.assertIn("Send reply (simulated)", page)

        self.switch_to(STAFF)  # 发消息不改订单，店员也可以发
        self.client.post(f"/orders/{order_id}/reply/send", data={"message": "Edited reply, thanks!"})
        sent = self.logs("reply_sent")
        self.assertEqual(len(sent), 1)
        self.assertIn("Edited reply, thanks!", sent[0]["detail"])
        self.assertEqual(self.get_order(order_id)["quantity"], 15)  # 订单没变

    def test_empty_reply_is_not_sent(self):
        order_id = self.add_order()
        self.client.post(f"/orders/{order_id}/reply/send", data={"message": "   "})
        self.assertEqual(self.logs("reply_sent"), [])


class FeedbackTest(AppTestCase):
    """/feedback 页面：不预先填任何内容；填好才保存，并写日志。"""

    GOOD = {"shop_type": "grocery", "hours_per_week": "3.5", "most_useful": "ai_reading",
            "improve": "Show supplier on-time rate", "willing_to_pay": "maybe"}

    def feedback_rows(self):
        with db.connect() as conn:
            return conn.execute("SELECT * FROM feedback ORDER BY id").fetchall()

    def test_form_starts_empty(self):
        page = self.client.get("/feedback").get_data(as_text=True)
        self.assertNotIn("selected", page)   # 下拉框没有预先选好的
        self.assertNotIn("checked", page)    # 单选框一个都没选
        self.assertRegex(page, r'name="hours_per_week"[^>]*value=""')  # 小时数是空的
        self.assertEqual(self.feedback_rows(), [])  # 数据库里也没有预先填的数据

    def test_valid_feedback_is_saved_and_logged(self):
        response = self.client.post("/feedback", data=self.GOOD)
        self.assertEqual(response.status_code, 302)
        rows = self.feedback_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["shop_type"], rows[0]["hours_per_week"], rows[0]["most_useful"],
                          rows[0]["improve"], rows[0]["willing_to_pay"]),
                         ("grocery", 3.5, "ai_reading", "Show supplier on-time rate", "maybe"))
        log = self.logs("feedback_submitted")
        self.assertEqual(len(log), 1)
        self.assertIn("3.5 hour(s) a week", log[0]["detail"])

    def test_improvement_is_optional(self):
        self.client.post("/feedback", data=dict(self.GOOD, improve=""))
        self.assertEqual(len(self.feedback_rows()), 1)

    def test_invalid_feedback_is_not_saved(self):
        cases = {
            "no shop type": dict(self.GOOD, shop_type=""),
            "made-up shop type": dict(self.GOOD, shop_type="casino"),
            "negative hours": dict(self.GOOD, hours_per_week="-1"),
            "too many hours": dict(self.GOOD, hours_per_week="200"),
            "hours not a number": dict(self.GOOD, hours_per_week="a lot"),
            "no feature": dict(self.GOOD, most_useful=""),
            "no pay answer": dict(self.GOOD, willing_to_pay=""),
            "text too long": dict(self.GOOD, improve="x" * 1001),
        }
        for name, data in cases.items():
            with self.subTest(name):
                response = self.client.post("/feedback", data=data)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.feedback_rows(), [])
        self.assertEqual(self.logs("feedback_submitted"), [])

    def test_demo_reset_keeps_feedback(self):
        self.client.post("/feedback", data=self.GOOD)
        self.client.post("/reset")  # 默认是店主
        self.assertEqual(len(self.feedback_rows()), 1)


if __name__ == "__main__":
    unittest.main()
