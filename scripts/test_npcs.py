"""
动态 NPC 探针（桩模型，零额度）

判定规则：**"玩家在跟谁说话"完全由模型判断**，没有任何正则去猜"句首称呼"。
本脚本覆盖：
- 模型说 addressed=true → 按 who 建 NPC（who 为空则按地点取名）
- 模型说 addressed=false → 不建人（含 "hey?" 这类口头禅的回归用例）
- 句子里提到现场某人 / 上一轮就是他在说话 → 直接找回，不花 token
- NPC 回话落库、进尤夏上下文、聊天链路里先 NPC 后尤夏
- 手动创建 / 退场 / 数量上限 / 历史误建数据清理

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


class Decide:
    """只回答"是不是在对第三人说话"的桩"""

    def __init__(self, verdict='{"addressed": false, "who": ""}'):
        self.verdict = verdict
        self.calls = 0
        self.system_prompt = ""
        self.kwargs = {}

    def chat_sync(self, messages, system_prompt="", **kw):
        self.calls += 1
        self.system_prompt = system_prompt
        self.kwargs = kw
        return self.verdict


class StubLLM(Decide):
    """NPC 回话走 chat_sync；尤夏走 chat_stream"""

    def __init__(self):
        super().__init__()
        self.stream_prompt = ""
        self.messages = []
        self.npc_reply = "加蛋两块。"

    def chat_sync(self, messages, system_prompt="", **kw):
        if "addressed" in (system_prompt or ""):
            return super().chat_sync(messages, system_prompt, **kw)
        self.system_prompt = system_prompt
        return self.npc_reply

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        self.stream_prompt = system_prompt
        self.messages = list(messages)
        yield '{"对白": "……嗯。"}'


def resolve(db, conv, location, message, verdict):
    """跑一次"谁是这句话的对象"，返回 (npc, 判别桩)"""
    stub = Decide(verdict)
    return npc.resolve_addressed(db, conv, location, message, stub), stub


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

    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)
    locations.switch(app_db, conv, "煎饼摊")
    app_db.set_player_location(conv, "煎饼摊")

    # ── 1) 模型说"在跟第三人说话" → 按 who 建 NPC
    boss, stub = resolve(app_db, conv, "煎饼摊", "老板，来个煎饼",
                         '{"addressed": true, "who": "老板"}')
    check("模型判定后生成 NPC", bool(boss) and boss["name"] == "老板", str(boss))
    check("确实问了模型一次", stub.calls == 1, str(stub.calls))
    check("判别调用关掉了思考", stub.kwargs.get("thinking") is False, str(stub.kwargs))
    check("判别调用限制输出长度", bool(stub.kwargs.get("max_tokens")), str(stub.kwargs))
    check("判别提示词里有现场名单", "现场已有的人" in stub.system_prompt, stub.system_prompt[:120])
    check("判别提示词里有最近的对话", "最近的对话" in stub.system_prompt, stub.system_prompt[:120])
    check("NPC 绑定在当前地点", boss and boss["location"] == "煎饼摊")
    check("NPC 有身份和人设", boss and boss["role"] and boss["persona"], str(boss))

    # ── 2) 提到现场某人的名字 → 直接找回，不再问模型
    found = npc.find_addressed(app_db, conv, "煎饼摊", "老板，加个蛋")
    check("按身份能找到他", bool(found) and found["id"] == boss["id"])
    other = npc.find_addressed(app_db, conv, "公寓客厅", "老板，加个蛋")
    check("换地方就不在场了", other is None, str(other))
    found2, again = resolve(app_db, conv, "煎饼摊", "老板，再来一份",
                            '{"addressed": false, "who": ""}')
    check("已有的人不会重复问模型", again.calls == 0, str(again.calls))
    check("也不会重复建人", found2 and found2["id"] == boss["id"], str(found2))
    check("现场只有一个人", len(app_db.get_npcs(conv, location="煎饼摊")) == 1,
          str(len(app_db.get_npcs(conv, location="煎饼摊"))))

    # ── 3) NPC 回话落库，通道是 npc
    llm = StubLLM()
    text = npc.reply(app_db, conv, boss, "加个蛋多少钱", llm)
    check("NPC 会回话", text == "加蛋两块。", text)
    rows = [m for m in app_db.get_messages(conv) if m["channel"] == "npc"]
    check("NPC 的话落库到 npc 通道", len(rows) == 1, str(len(rows)))
    payload = json.loads(rows[0]["content"])
    check("落库内容带名字", payload.get("name") == "老板" and payload.get("text") == "加蛋两块。",
          rows[0]["content"])
    check("现场还有谁", "老板" in npc.present_block(app_db, conv, "煎饼摊"))

    # ── 4) 聊天链路：先 NPC 后尤夏，尤夏看得到 NPC 说了什么
    chat_llm = StubLLM()
    real = chat_mod.deepseek
    chat_mod.deepseek = chat_llm
    try:
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "老板，来个加蛋的"})
        events = sse(r)
        kinds = [e.get("type") for e in events]
        check("聊天返回 SSE 且含 npc 事件", "npc" in kinds and "done" in kinds, str(kinds))
        npc_ev = next((e for e in events if e.get("type") == "npc"), {})
        check("npc 事件带名字与内容",
              npc_ev.get("name") == "老板" and npc_ev.get("content"), str(npc_ev))
        ctx = json.dumps(chat_llm.messages, ensure_ascii=False)
        check("尤夏的上下文里有 NPC 说的话", "老板" in ctx and "加蛋两块" in ctx, ctx[:200])
        check("尤夏的提示词里提示现场有谁", "现场还有" in chat_llm.stream_prompt,
              chat_llm.stream_prompt[-300:])
    finally:
        chat_mod.deepseek = real

    # ── 4b) 没点名时，上一轮是 NPC 说话 → 继续由他接
    follow = npc.find_addressed(app_db, conv, "煎饼摊", "加个蛋多少钱")
    check("没点名也能接上（上一轮是 NPC 在说话）",
          bool(follow) and follow["id"] == boss["id"], str(follow))
    check("明确叫尤夏时不会路由到 NPC",
          npc.find_addressed(app_db, conv, "煎饼摊", "尤夏，你吃辣吗") is None)

    # ── 5) 模型说"没在跟第三人说话" → 不建人（含口头禅回归）
    conv_h = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_h)
    locations.switch_both(app_db, conv_h, "街上")
    for phrase in ("hey?", "hey", "hi, 你好", "ok", "今天天气不错", "尤夏，你吃辣吗"):
        got, judge = resolve(app_db, conv_h, "街上", phrase, '{"addressed": false, "who": ""}')
        check(f"「{phrase}」不会生成 NPC", got is None, str(got))
        check(f"「{phrase}」确实过了模型判断", judge.calls == 1, str(judge.calls))
    check("一条人也没建出来", len(app_db.get_npcs(conv_h)) == 0,
          str([n["name"] for n in app_db.get_npcs(conv_h)]))

    # ── 5b) 模型给了"在跟第三人说话"但没给称呼 → 按地点取名
    made, _ = resolve(app_db, conv_h, "街上", "给我来个煎饼",
                      '{"addressed": true, "who": ""}')
    check("who 为空时按地点取名", bool(made) and made["name"] == "路人", str(made))

    # ── 5c) 历史误建的口头禅 NPC 会被退场
    stale = app_db.add_npc(conv_h, "hey", "hey", "街上", "")
    app_db.add_npc(conv_h, "摊主老李", "摊主", "街上", "")
    npc.find_addressed(app_db, conv_h, "街上", "hey?")
    stale_row = app_db.get_npc(stale)
    check("库里残留的口头禅 NPC 会被退场",
          bool(stale_row) and stale_row["status"] == "gone",
          str(dict(stale_row) if stale_row else None))

    # ── 6) 手动创建 / 退场
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

    # ── 7) 数量上限
    conv2 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv2)
    for i in range(npc.NPC_CAP):
        app_db.add_npc(conv2, f"路人{i}", "路人", "街上", "")
    overflow, _ = resolve(app_db, conv2, "街上", "老板，来个煎饼",
                          '{"addressed": true, "who": "老板"}')
    check("名册挤满时会先请最久没说话的退场，再生成新人",
          bool(overflow) and 0 < app_db.count_npcs(conv2) <= npc.NPC_CAP,
          f"count={app_db.count_npcs(conv2)}")
    gone = app_db.get_npcs(conv2, status="gone")
    check("退场的 N P C 记进了 gone", len(gone) >= 1, str(len(gone)))

    # ── 8) 同一地点超过上限时，最久没说话的先退场
    conv4 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv4)
    for i in range(npc.PER_LOCATION_CAP):
        app_db.add_npc(conv4, f"路人{i}", "路人", "街上", "")
    extra, _ = resolve(app_db, conv4, "街上", "老板，来个煎饼",
                       '{"addressed": true, "who": "老板"}')
    check("同地点有人数上限，超出时挤掉最久没说话的",
          bool(extra) and len(app_db.get_npcs(conv4, location="街上")) <= npc.PER_LOCATION_CAP,
          str(len(app_db.get_npcs(conv4, location="街上"))))

    # ── 9) 模型判出"没在跟第三人说话"时，聊天链路里也不出现 npc
    conv6 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv6)
    locations.switch_both(app_db, conv6, "煎饼摊")
    none_stub = StubLLM()
    chat_mod.deepseek = none_stub
    try:
        r = c.post("/api/chat", json={"conversation_id": conv6, "message": "我今天有点累"})
        events = sse(r)
        check("对着尤夏说话不会生成 NPC",
              "npc" not in [e.get("type") for e in events]
              and not app_db.get_npcs(conv6), str(app_db.get_npcs(conv6)))
    finally:
        chat_mod.deepseek = real

    # ── 10) 聊天链路里，模型说"在对第三人说话" → 建人并先说话
    conv7 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv7)
    locations.switch_both(app_db, conv7, "煎饼摊")
    detect_stub = StubLLM()
    detect_stub.verdict = '{"addressed": true, "who": "卖煎饼的"}'

    class ReplyAfterDetect(StubLLM):
        """判别返回 JSON，NPC 回话返回台词"""

        def chat_sync(self, messages, system_prompt="", **kw):
            if "addressed" in (system_prompt or ""):
                return super().chat_sync(messages, system_prompt, **kw)
            return "加蛋两块。"

    detect_stub = ReplyAfterDetect()
    detect_stub.verdict = '{"addressed": true, "who": "卖煎饼的"}'
    chat_mod.deepseek = detect_stub
    try:
        r = c.post("/api/chat", json={"conversation_id": conv7, "message": "给我来个煎饼，多加辣"})
        events = sse(r)
        kinds = [e.get("type") for e in events]
        check("没有句首称呼时由模型判别出第三人", "npc" in kinds, str(kinds))
        created = app_db.get_npcs(conv7, location="煎饼摊")
        check("判别出的称呼被建成 NPC",
              any(n["name"] == "卖煎饼的" for n in created), str([n["name"] for n in created]))
    finally:
        chat_mod.deepseek = real

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
