"""
动态 NPC 探针（桩模型，零额度）

覆盖：喊"老板"自动生成 NPC、按名字/身份搭话、NPC 回话落库与进上下文、
手动创建 / 退场、数量上限

用法：python scripts/test_npcs.py
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "npc_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db                      # noqa: E402
from core.auth import generate_token                   # noqa: E402
import core.locations as locations                     # noqa: E402
import routes.chat_routes as chat_mod                  # noqa: E402
import services.npc as npc                             # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class StubLLM:
    """NPC 走 chat_sync；尤夏走 chat_stream。两者都记下来"""

    def __init__(self):
        self.system_prompt = ""
        self.messages = []
        self.user_msg = ""

    def chat_sync(self, messages, system_prompt="", **kw):
        self.system_prompt = system_prompt
        self.user_msg = messages[0]["content"] if messages else ""
        return "加蛋两块。"

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        self.system_prompt = system_prompt
        self.messages = list(messages)
        yield '{"对白": "……嗯。"}'


def client_for(user_id):
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user_id))
    return c


def sse(resp):
    out = []
    for raw in resp.response:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        for part in line.split("\n"):
            if part.strip().startswith("data: "):
                try:
                    out.append(json.loads(part.strip()[6:]))
                except Exception:
                    pass
    return out


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = client_for(user)

    # ── 1) 喊"老板"自动生出 NPC
    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)
    locations.switch(app_db, conv, "煎饼摊")
    app_db.set_player_location(conv, "煎饼摊")

    boss = npc.spawn_placeholder(app_db, conv, "煎饼摊", "老板，来个煎饼")
    check("喊老板会生出 NPC", bool(boss) and boss["name"] == "老板", str(boss))
    check("NPC 绑定在当前地点", boss and boss["location"] == "煎饼摊")
    check("NPC 有身份和人设", boss and boss["role"] and boss["persona"], str(boss))

    # ── 2) 按身份 / 名字都能搭话
    found = npc.find_addressed(app_db, conv, "煎饼摊", "老板，加个蛋")
    check("按身份能找到他", bool(found) and found["id"] == boss["id"])
    other = npc.find_addressed(app_db, conv, "公寓客厅", "老板，加个蛋")
    check("换地方就不在场了", other is None, str(other))

    # ── 3) NPC 回话落库，通道是 npc
    text = npc.reply(app_db, conv, boss, "加个蛋多少钱", StubLLM())
    check("NPC 会回话", text == "加蛋两块。", text)
    rows = [m for m in app_db.get_messages(conv) if m["channel"] == "npc"]
    check("NPC 的话落库到 npc 通道", len(rows) == 1, str(len(rows)))
    payload = json.loads(rows[0]["content"])
    check("落库内容带名字", payload.get("name") == "老板" and payload.get("text") == "加蛋两块。",
          rows[0]["content"])
    check("现场还有谁", "老板" in npc.present_block(app_db, conv, "煎饼摊"))

    # ── 4) 聊天链路：先 NPC 后尤夏，尤夏看得到 NPC 说了什么
    llm = StubLLM()
    real = chat_mod.deepseek
    chat_mod.deepseek = llm
    try:
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "老板，来个加蛋的"})
        events = sse(r)
        kinds = [e.get("type") for e in events]
        check("聊天返回 SSE 且含 npc 事件", "npc" in kinds and "done" in kinds, str(kinds))
        npc_ev = next((e for e in events if e.get("type") == "npc"), {})
        check("npc 事件带名字与内容",
              npc_ev.get("name") == "老板" and npc_ev.get("content"), str(npc_ev))
        ctx = json.dumps(llm.messages, ensure_ascii=False)
        check("尤夏的上下文里有 NPC 说的话", "老板" in ctx and "加蛋两块" in ctx, ctx[:200])
        check("尤夏的提示词里提示现场有谁", "现场还有" in llm.system_prompt,
              llm.system_prompt[-300:])
    finally:
        chat_mod.deepseek = real

    # ── 4b) 尤夏接话之后，玩家下一句没点名，也应该继续由 NPC 接
    follow = npc.find_addressed(app_db, conv, "煎饼摊", "加个蛋多少钱")
    check("没点名也能接上（上一轮是 NPC 在说话）",
          bool(follow) and follow["id"] == boss["id"], str(follow))
    check("明确叫尤夏时不会路由到 NPC",
          npc.find_addressed(app_db, conv, "煎饼摊", "尤夏，你吃辣吗") is None)

    # ── 5) 手动创建 / 退场
    r = c.post(f"/api/npcs/{conv}", json={"name": "煎饼摊老板娘", "role": "老板娘",
                                          "location": "煎饼摊"})
    created = (r.get_json() or {}).get("npc") or {}
    check("可以手动加 NPC", r.status_code == 200 and created.get("name") == "煎饼摊老板娘",
          str(r.status_code))
    r = c.get(f"/api/npcs/{conv}")
    listing = (r.get_json() or {}).get("npcs") or []
    check("列表能看到两个", len(listing) == 2, str(len(listing)))

    r = c.post(f"/api/npcs/{conv}/{boss['id']}/dismiss")
    check("可以让 NPC 退场", r.status_code == 200 and (r.get_json() or {}).get("success"))
    check("退场后不再出现在现场",
          npc.find_addressed(app_db, conv, "煎饼摊", "老板，来个煎饼") is None)

    # ── 6) 数量上限
    conv2 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv2)
    for i in range(npc.NPC_CAP):
        app_db.add_npc(conv2, f"路人{i}", "路人", "街上", "")
    overflow = npc.spawn_placeholder(app_db, conv2, "街上", "老板，来个煎饼")
    check("名册挤满时会先请最久没说话的退场，再生成新人",
          bool(overflow) and 0 < app_db.count_npcs(conv2) <= npc.NPC_CAP,
          f"count={app_db.count_npcs(conv2)}")
    gone = app_db.get_npcs(conv2, status="gone")
    check("退场的 N P C 记进了 gone", len(gone) >= 1, str(len(gone)))

    # ── 7) 任意称呼都能当场出现（不限于固定词表）
    conv3 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv3)
    locations.switch_both(app_db, conv3, "街上")
    fortune = npc.spawn_placeholder(app_db, conv3, "街上", "算命先生，给我看看手相")
    check("任意称呼可以生成 NPC",
          bool(fortune) and fortune["name"] == "算命先生", str(fortune))
    check("身份拆得对", fortune and fortune["role"] == "算命先生", str(fortune))
    hunter = npc.spawn_placeholder(app_db, conv3, "街上", "@收废品的老王 这堆多少钱")
    check("@名字 也能生成", bool(hunter) and hunter["name"] == "收废品的老王", str(hunter))
    check("普通陈述不会生成 NPC",
          npc.spawn_placeholder(app_db, conv3, "街上", "今天天气不错") is None)
    check("叫尤夏不会生成 NPC",
          npc.spawn_placeholder(app_db, conv3, "街上", "尤夏，你吃辣吗") is None)

    # ── 8) 同一地点超过上限时，最久没说话的先退场
    conv4 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv4)
    for i in range(npc.PER_LOCATION_CAP):
        app_db.add_npc(conv4, f"路人{i}", "路人", "街上", "")
    extra = npc.spawn_placeholder(app_db, conv4, "街上", "老板，来个煎饼")
    check("同地点有人数上限，超出时挤掉最久没说话的",
          bool(extra) and len(app_db.get_npcs(conv4, location="街上")) <= npc.PER_LOCATION_CAP,
          str(len(app_db.get_npcs(conv4, location="街上"))))

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
