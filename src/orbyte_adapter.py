"""Orbyte 接口层（Adapter）。

Sudahsampai 设计成 Orbyte 生态里的一个模块：用户、联系人、发消息、审批待办、点数钱包、审计日志
这些"平台能力"都应该由 Orbyte 提供，而不是我们自己做一套。

app.py 只通过下面 6 个函数使用这些能力：读取当前用户、发消息、建待办、扣点数、写日志都不直接做。
（唯一的例外是演示用的"切换角色"按钮会写 session，真实 Orbyte 里身份来自单点登录，没有这个按钮。）
现在 6 个函数全部是【模拟实现】，数据来自本地 SQLite 数据库或 Flask session；
接口的名字、参数、返回值是我们对 Orbyte API 的假设（见 docs/api.md）。
接入真实 Orbyte 时，只需要改这个文件，app.py 不用动。
"""
from datetime import datetime

from flask import session

import db

OWNER = "店主"
STAFF = "店员"
SYSTEM = "系统"

# 模拟的 Orbyte 账号（虚构人物）。演示时用页面上的"角色"按钮切换登录身份。
MOCK_USERS = {
    OWNER: {"id": "orbyte-user-001", "name": "Ah Seng", "role": OWNER},
    STAFF: {"id": "orbyte-user-002", "name": "Mei Ling", "role": STAFF},
}

# 每次自动催货、每次收货核对扣的 Orbyte credits 数
CREDIT_PER_ACTION = 1


# ---------- 1. People：当前登录用户 ----------

def get_current_user():
    """返回当前登录的 Orbyte 用户 {id, name, role}。
    模拟：从 session 读演示用的角色；还没选过时默认店主；读到不认识的值按权限最小的店员处理。
    将来替换为真实 Orbyte API 调用：用 Orbyte 单点登录的令牌查询当前用户和角色（Orbyte People）。"""
    role = session.get("role", OWNER)
    return MOCK_USERS.get(role, MOCK_USERS[STAFF])


def find_owner():
    """这家店的店主（审批待办发给谁）。模拟：固定是 Ah Seng。
    将来替换为真实 Orbyte API 调用：在 Orbyte People 里按角色查询。"""
    return MOCK_USERS[OWNER]


# ---------- 2. People / Contacts：供应商联系方式 ----------

def get_supplier_contact(order):
    """返回订单对应供应商的联系方式 {name, whatsapp}。
    模拟：直接读本地订单里存的供应商名和 WhatsApp 号码。
    将来替换为真实 Orbyte API 调用：按供应商 ID 从 Orbyte 的联系人目录读取，避免每张订单各存一份。"""
    return {"name": order["supplier"] or "Supplier", "whatsapp": order["whatsapp"] or ""}


# ---------- 3. Connect：发消息 ----------

def send_message(contact, text, channel="whatsapp"):
    """给联系人发消息，返回发送回执 {status, channel, to, sent_at}。
    模拟：不真的发出去，只返回 status="simulated" 的回执；消息全文由调用方写进审计日志。
    将来替换为真实 Orbyte API 调用：通过 Orbyte Connect 的 WhatsApp 通道发送。"""
    return {
        "status": "simulated",
        "channel": channel,
        "to": contact["whatsapp"],
        "sent_at": datetime.now(db.MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------- 4. Work：审批待办 ----------

def create_approval(conn, order_id, title, detail):
    """给店主创建一条待办，请他审批（例如处理数量差异），返回 {id, assignee, title}。
    模拟：写一条 approval_created 审计日志当作"待办已送达"。
    将来替换为真实 Orbyte API 调用：在 Orbyte Work 里给店主建审批任务，店主在 Orbyte 待办里处理。"""
    owner = find_owner()
    approval_id = f"approval-{order_id}-{db.today().isoformat()}"
    write_audit(conn, "approval_created",
                f"#{order_id} Sent to {owner['name']}'s Orbyte to-do: {title}. {detail}", order_id)
    return {"id": approval_id, "assignee": owner["name"], "title": title}


# ---------- 5. 点数钱包：扣 credits ----------

def charge_credit(conn, reason, order_id=None):
    """扣 CREDIT_PER_ACTION 个 Orbyte credit，并写入审计日志；返回本月已用的 credits。
    模拟：每扣一次写一条 credit_charged 日志，"本月已用"就是本模拟月里这种日志的条数。
    不涉及任何真实付款。
    将来替换为真实 Orbyte API 调用：从这家店的 Orbyte 点数钱包里扣点，余额由 Orbyte 管理。"""
    write_audit(conn, "credit_charged", f"Charged {CREDIT_PER_ACTION} Orbyte credit: {reason}", order_id)
    return credits_used_this_month(conn)


def credits_used_this_month(conn):
    """本模拟月已用的 credits（页面角落显示）。
    将来替换为真实 Orbyte API 调用：查询 Orbyte 点数钱包的本月用量。"""
    month = db.today().isoformat()[:7]  # 例如 "2026-10"
    count = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE action = 'credit_charged' AND sim_date LIKE ?", (month + "%",)
    ).fetchone()[0]
    return count * CREDIT_PER_ACTION


# ---------- 6. 审计日志 ----------

def write_audit(conn, action, detail, order_id=None, actor=None, amount=None):
    """写一条审计日志，返回日志 id。actor 不填时用当前登录用户的角色；系统自动做的事填 SYSTEM。
    amount 是这件事避免的损失（RM），没有就不填。
    模拟：写进本地 SQLite 的 logs 表。
    将来替换为真实 Orbyte API 调用：写入 Orbyte 统一的审计日志，和其他 Orbyte 模块的记录放在一起。"""
    if actor is None:
        actor = get_current_user()["role"]
    return db.add_log(conn, actor, action, detail, order_id, amount)
