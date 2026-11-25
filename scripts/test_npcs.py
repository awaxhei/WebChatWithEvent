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
    """只回答"是不是在对第三人说话"的桩（addressed=true 时补一句依据）"""

    def __init__(self, verdict='{"addressed": false, "who": ""}'):
        self.verdict = _with_why(verdict)
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


def _with_why(verdict, why="老板，来个煎饼"):
    """判别返回里补一句依据：真实的模型必须给 why，否则不会被采纳"""
    try:
        data = json.loads(verdict)
    except Exception:
        return verdict
    data.setdefault("why", why if data.get("addressed") else "")
    return json.dumps(data, ensure_ascii=False)


def resolve(db, conv, location, message, verdict, why="老板，来个煎饼"):
    """跑一次"谁是这句话的对象"，返回 (npc, 判别桩)

    真实的判别会给出 why（依据原话）；测试里默认给一句，需要时显式传空。
    """
    stub = Decide(verdict)
    if "why" not in verdict and why is not None:
        stub.verdict = json.dumps(
            {**json.loads(verdict), "why": why}, ensure_ascii=False)
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
    app_db.set_location_private(conv, "煎饼摊", False)
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

    # ── 4b) 没点名时：交给模型判断"是不是在继续跟他说"（不再用机械规则）
    follow, follow_stub = resolve(app_db, conv, "煎饼摊", "加个蛋多少钱",
                                  '{"addressed": true, "who": "老板"}')
    check("没点名时由模型判断是否继续跟 NPC 说",
          bool(follow) and follow["id"] == boss["id"], str(follow))
    check("这种「接着说」会问一次模型", follow_stub.calls == 1, str(follow_stub.calls))
    check("明确叫尤夏时不会路由到 NPC",
          npc.find_addressed(app_db, conv, "煎饼摊", "尤夏，你吃辣吗") is None)
    # 模型说"不是在跟第三人说话"就不找他
    nothing, _ = resolve(app_db, conv, "煎饼摊", "我们等会儿去买点菜吧",
                         '{"addressed": false, "who": ""}')
    check("模型说不是找 NPC 就不路由", nothing is None, str(nothing))

    # ── 5) 模型说"没在跟第三人说话" → 不建人（含口头禅回归）
    conv_h = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_h)
    locations.switch_both(app_db, conv_h, "街上")
    app_db.set_location_private(conv_h, "街上", False)
    for phrase in ("hey?", "hey", "hi, 你好", "ok", "今天天气不错", "尤夏，你吃辣吗"):
        got, judge = resolve(app_db, conv_h, "街上", phrase, '{"addressed": false, "who": ""}')
        check(f"「{phrase}」不会生成 NPC", got is None, str(got))
        check(f"「{phrase}」确实过了模型判断", judge.calls == 1, str(judge.calls))
    check("一条人也没建出来", len(app_db.get_npcs(conv_h)) == 0,
          str([n["name"] for n in app_db.get_npcs(conv_h)]))

    # ── 5b) 模型给了"在跟第三人说话"却没给称呼 → 不编人（以前会兜底叫「路人」）
    made, _ = resolve(app_db, conv_h, "街上", "给我来个煎饼",
                      '{"addressed": true, "who": ""}')
    check("who 为空时不编造路人", made is None, str(made))
    check("名册里没有「路人」",
          not [n for n in app_db.get_npcs(conv_h) if n["name"] == "路人"],
          str([n["name"] for n in app_db.get_npcs(conv_h)]))

    # ── 5c) 历史误建的口头禅 NPC 会被退场
    stale = app_db.add_npc(conv_h, "hey", "hey", "街上", "")
    app_db.add_npc(conv_h, "摊主老李", "摊主", "街上", "")
    npc.find_addressed(app_db, conv_h, "街上", "hey?")
    stale_row = app_db.get_npc(stale)
    check("库里残留的口头禅 NPC 会被退场",
          bool(stale_row) and stale_row["status"] == "gone",
          str(dict(stale_row) if stale_row else None))

    # ── 5d) 模型把 who 填成「你」「尤夏」「青梅」时，不许建出同名 NPC
    # 以前 who 直接拿去建人，于是库里出现了一个叫「尤夏」的 NPC，
    # 之后每一句都被它接走（试玩里 3 轮连续出现）。
    conv_r = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_r)
    locations.switch_both(app_db, conv_r, "煎饼摊")
    app_db.set_location_private(conv_r, "煎饼摊", False)
    for bogus in ("你", "您", "尤夏", "青梅"):
        got, _ = resolve(app_db, conv_r, "煎饼摊", "随便说点什么吧",
                         json.dumps({"addressed": True, "who": bogus}, ensure_ascii=False))
        names = [n["name"] for n in app_db.get_npcs(conv_r, location="煎饼摊")]
        check(f"who=「{bogus}」不会建出 NPC", got is None and not names, str(names))
    names = [n["name"] for n in app_db.get_npcs(conv_r, location="煎饼摊")]
    check("代词和口头禅都不会变成 NPC", not names, str(names))

    # 库里残留的同名 NPC 会被退场
    conv_r2 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_r2)
    locations.switch_both(app_db, conv_r2, "街上")
    app_db.set_location_private(conv_r2, "街上", False)
    stale2 = app_db.add_npc(conv_r2, "尤夏", "尤夏", "街上", "")
    npc.find_addressed(app_db, conv_r2, "街上", "嗯")
    row2 = app_db.get_npc(stale2)
    check("库里残留的「尤夏」NPC 会被退场",
          bool(row2) and row2["status"] == "gone",
          str(dict(row2) if row2 else None))
    spawned = npc.spawn_named(app_db, conv_r2, "街上", "青梅")
    check("spawn_named 也不会建出「青梅」", spawned is None, str(spawned))

    # ── 5e) 私人空间里没有第三人（尤夏的房间、青梅的房间、公寓玄关……）
    conv_home = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_home)
    locations.switch_both(app_db, conv_home, "青梅的房间")
    home_stub = Decide('{"addressed": true, "who": "老板"}')
    got_home = npc.resolve_addressed(app_db, conv_home, "青梅的房间", "老板，来个煎饼",
                                     home_stub)
    check("私人空间里不会冒出 NPC", got_home is None, str(got_home))
    check("私人空间不花判别 token", home_stub.calls == 0, str(home_stub.calls))
    check("私人空间没建出任何人", not app_db.get_npcs(conv_home),
          str(app_db.get_npcs(conv_home)))
    check("私人空间也不提示现场有谁",
          npc.present_block(app_db, conv_home, "青梅的房间") == "")

    # ── 5f) 手机里没有 NPC：人不在同一地点，只能发消息
    conv_phone = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_phone)
    locations.switch(app_db, conv_phone, "公寓玄关")          # 她在玄关
    app_db.set_player_location(conv_phone, "青梅的房间")       # 你在自己房间
    # 库里已经站着一个（历史数据）：也不该在手机里开口
    ghost = app_db.add_npc(conv_phone, "路人", "路人", "青梅的房间", "")
    phone_stub = StubLLM()
    phone_stub.verdict = _with_why('{"addressed": true, "who": "路人"}')
    chat_mod.deepseek = phone_stub
    try:
        r = c.post("/api/chat", json={"conversation_id": conv_phone, "message": "你在干嘛"})
        events = sse(r)
        kinds = [e.get("type") for e in events]
        check("手机里不出现 npc 事件", "npc" not in kinds, str(kinds))
        check("手机里还是有她的回复", "done" in kinds, str(kinds))
        check("手机里她不知道「现场还有」谁",
              "【现场还有】路人" not in phone_stub.stream_prompt,
              phone_stub.stream_prompt[-200:])
        check("手机通道要求活蹦乱跳、可以连发",
              "活蹦乱跳" in phone_stub.stream_prompt and "连发" in phone_stub.stream_prompt,
              phone_stub.stream_prompt[-260:])
        check("手机里那条旧 NPC 没有说话",
              not [m for m in app_db.get_messages(conv_phone) if m["channel"] == "npc"],
              str([m["channel"] for m in app_db.get_messages(conv_phone)]))
        check("手机里也没多出新 NPC", len(app_db.get_npcs(conv_phone)) == 1,
              str([n["name"] for n in app_db.get_npcs(conv_phone)]))
    finally:
        chat_mod.deepseek = real
    app_db.set_npc_status(ghost, "gone")

    # ── 5g) 当面 + 公共场合仍然可以有第三人
    conv_street = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_street)
    locations.switch_both(app_db, conv_street, "街上")
    app_db.set_location_private(conv_street, "街上", False)
    street_stub = Decide('{"addressed": true, "who": "卖红薯的"}')
    got_street = npc.resolve_addressed(app_db, conv_street, "街上", "老板，红薯怎么卖",
                                       street_stub)
    check("公共场合仍然会生成 NPC",
          bool(got_street) and got_street["name"] == "卖红薯的", str(got_street))

    # ── 5h) 昵称靠上下文判断，不靠词表：模型读完对话说"这是在叫她"就不建人
    conv_sweet = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_sweet)
    locations.switch_both(app_db, conv_sweet, "食堂")
    for word, message in (("小可爱", "小可爱"), ("小美女", "嘿嘿尤夏小美女"),
                          ("宝贝", "宝贝，张嘴"), ("老婆", "老婆我回来了")):
        # 模型看了上下文，判成"在叫她"——这就是它该做的事，不需要黑名单
        sweet = Decide(json.dumps({"addressed": False, "who": word, "why": ""},
                                  ensure_ascii=False))
        got_sweet = npc.resolve_addressed(app_db, conv_sweet, "食堂", message, sweet)
        names = [n["name"] for n in app_db.get_npcs(conv_sweet)]
        check(f"「{message}」被判成在叫她，不建人", got_sweet is None and not names,
              str(names))

    # 说"在对第三人说话"却引不出依据 → 不建（宁可不建，也不凭空造人）
    no_evidence = Decide(json.dumps({"addressed": True, "who": "小可爱", "why": ""},
                                    ensure_ascii=False))
    check("引不出依据就不建人",
          npc.resolve_addressed(app_db, conv_sweet, "食堂", "小可爱", no_evidence) is None)
    check("这种误判也不会留下 NPC", not app_db.get_npcs(conv_sweet),
          str([n["name"] for n in app_db.get_npcs(conv_sweet)]))

    # 判别拿到的是够长的上下文（她上一句怎么应的都看得见），不是 4 条
    conv_ctx = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_ctx)
    locations.switch_both(app_db, conv_ctx, "食堂")
    for i in range(6):
        app_db.add_message(conv_ctx, "user", f"旧话{i}", channel="chat")
        app_db.add_message(conv_ctx, "assistant",
                           json.dumps({"对白": f"回应{i}"}, ensure_ascii=False), channel="chat")
    ctx_stub = Decide(json.dumps({"addressed": False, "who": "", "why": ""}, ensure_ascii=False))
    npc.detect_addressed(app_db, conv_ctx, "食堂", "食堂在哪", ctx_stub)
    check("判别的上下文超过 4 条", "旧话0" in ctx_stub.system_prompt, ctx_stub.system_prompt[-400:])
    check("判别的上下文带着她自己的回应", "回应0" in ctx_stub.system_prompt)
    check("判别知道此刻在哪、两人在不在同一处",
          "食堂" in ctx_stub.system_prompt and "在一起" in ctx_stub.system_prompt,
          ctx_stub.system_prompt[:300])
    check("判别要求给出依据（why）", '"why"' in ctx_stub.system_prompt, ctx_stub.system_prompt[:200])

    # 点了她的名字、又叫了别的称呼 → 当成在跟她说话，不建人
    named = Decide('{"addressed": true, "who": "学姐", "why": "学姐那边"}')
    check("句子里点了尤夏就不再建第三人",
          npc.resolve_addressed(app_db, conv_sweet, "食堂", "尤夏，学姐那边在排队吗",
                                named) is None)
    # 但现场真有第三人的时候，点名找他还是可以的
    other_stub = Decide('{"addressed": true, "who": "打饭阿姨"}')
    got_other = npc.resolve_addressed(app_db, conv_sweet, "食堂", "阿姨，来一份番茄炒蛋",
                                      other_stub)
    check("公共场合叫到别人仍然会生成", bool(got_other) and got_other["name"] == "打饭阿姨",
          str(got_other))

    # 清理只认"结构上不可能是人"的名字（代词、口头禅、路人），不再认昵称词表：
    # 昵称是模型读上下文判断的事，写死词表永远写不全。
    keep_sweet = app_db.add_npc(conv_sweet, "小美女", "小美女", "食堂", "")
    conn_pronoun = app_db.add_npc(conv_sweet, "尤夏", "尤夏", "食堂", "")
    npc.find_addressed(app_db, conv_sweet, "食堂", "嗯")
    row_keep = app_db.get_npc(keep_sweet)
    row_pronoun = app_db.get_npc(conn_pronoun)
    check("昵称类名字不再被词表误杀（交给模型判断）",
          bool(row_keep) and row_keep["status"] == "active",
          str(dict(row_keep) if row_keep else None))
    check("结构上不可能是人的名字仍然会被退场",
          bool(row_pronoun) and row_pronoun["status"] == "gone",
          str(dict(row_pronoun) if row_pronoun else None))
    app_db.set_npc_status(keep_sweet, "gone")

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
    locations.resolve(app_db, conv2, "街上")
    app_db.set_location_private(conv2, "街上", False)      # 地点要先登记（默认公共）
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
    locations.resolve(app_db, conv4, "街上")
    app_db.set_location_private(conv4, "街上", False)
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
    app_db.set_location_private(conv6, "煎饼摊", False)
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
    app_db.set_location_private(conv7, "煎饼摊", False)
    detect_stub = StubLLM()
    detect_stub.verdict = _with_why('{"addressed": true, "who": "卖煎饼的"}')

    class ReplyAfterDetect(StubLLM):
        """判别返回 JSON，NPC 回话返回台词"""

        def chat_sync(self, messages, system_prompt="", **kw):
            if "addressed" in (system_prompt or ""):
                return super().chat_sync(messages, system_prompt, **kw)
            return "加蛋两块。"

    detect_stub = ReplyAfterDetect()
    detect_stub.verdict = _with_why('{"addressed": true, "who": "卖煎饼的"}')
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
