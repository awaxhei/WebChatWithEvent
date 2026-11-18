"""
真实端到端验证 v7：她能不能读到随机事件、会不会原地打转、会不会自己动手
（消耗少量 API 额度：deepseek-flash + 思考模式）

用法：python scripts/e2e_autonomy.py
"""
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "v7_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app                     # noqa: E402
from core.auth import generate_token    # noqa: E402
import core.locations as locations      # noqa: E402
import services.autonomy as autonomy    # noqa: E402

db = database.ChatDB()


def read_sse(resp):
    out = []
    for raw in resp.response:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        for part in line.split("\n"):
            part = part.strip()
            if part.startswith("data: "):
                try:
                    out.append(json.loads(part[6:]))
                except Exception:
                    pass
    return out


def main():
    app.config["TESTING"] = True
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(1))

    conv = db.create_conversation(1)
    locations.ensure_defaults(db, conv)
    db.add_message(conv, "user", "我回来了", channel="chat")
    db.add_message(conv, "assistant", json.dumps({"对白": "「哦。」"}, ensure_ascii=False), channel="chat")
    # 一条"刚发生"的随机事件：她应该能看见并去处理
    db.add_event(conv, "random", "水槽旧水管爆开", "水柱从水槽下面喷出来，厨房地上全是水",
                 "active", story_time="第一天 07:12", tier="high")
    db.set_last_user_at(conv, time.time())

    now = 432  # 第一天 07:12
    print("=" * 66)
    print("【第 1 轮】她看得到刚爆的水管吗？（真实对话）")
    r = c.post("/api/chat", json={"conversation_id": conv, "message": "厨房那边什么声音？"})
    reply = "".join(e.get("content", "") for e in read_sse(r) if e.get("type") == "chunk")
    print(f"  尤夏：{reply.strip()[:160]}")

    print("=" * 66)
    print("【第 2 轮】连续 5 次自主行动（每次都让时间往前走一点）")
    seen = []
    acts = 0
    for i in range(5):
        db.set_last_user_at(conv, time.time() - autonomy.AUTONOMY_MIN_INTERVAL_SECONDS - 1)
        acted = autonomy.maybe_act(db, conv, app.config.get("deepseek") or _deepseek(),
                                   now + i * 5, "情境：青梅在屋里，水槽刚爆过",
                                   idle=True)
        if not acted:
            print(f"  #{i + 1}  —— 她没动（none 或与之前重复被丢弃）")
            continue
        if acted["action"] == "act":
            acts += 1
        seen.append(acted.get("text", ""))
        print(f"  #{i + 1}  [{acted['action']}] {acted.get('text') or acted.get('location')}")

    print("-" * 66)
    print(f"动手次数: {acts} / 5    不重复条数: {len(set(seen))} / {len(seen)}")
    activity, _at = db.get_yuxia_activity(conv)
    print(f"此刻在做的: {activity or '（无）'}")
    print("事件表（最近 5 条）:")
    for e in db.get_recent_events(conv, limit=5):
        print(f"  [{e['event_type']}] {e['title']} @{e['story_time']}")
    print("临时库:", database.DB_PATH)


def _deepseek():
    """复用 app 里注入的客户端实例"""
    import routes.chat_routes as chat_mod
    return chat_mod.deepseek


if __name__ == "__main__":
    main()
