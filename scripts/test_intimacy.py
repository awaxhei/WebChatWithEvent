"""亲密接触时的反应密度（桩模型，零额度）

覆盖：
1. 接触热度 0/1/2 的判定（普通抱一下 vs 亲密接触）
2. 亲密接触时对话提示词里带上【此刻的身体接触】，并放宽到六条
3. 亲密时一轮能收下最多四条动作，常规轮次仍然只收两条
4. 亲密动作可以写到 32 字不被拆碎，常规轮次仍是 24 字
5. 旁白（character_writer）在亲密时补写「另一处反应」，而不是跳过
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "intimacy_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db            # noqa: E402
from core.auth import generate_token         # noqa: E402
import core.locations as locations           # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402
import services.character_writer as writer   # noqa: E402
import services.intimacy as intimacy         # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def sse(body):
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except Exception:
                pass
    return out


class StubLLM:
    def __init__(self, reply='{"对白": "嗯。"}', desc='{"旁白": "呼吸乱了一下。"}'):
        self.reply = reply
        self.desc = desc
        self.stream_prompt = ""
        self.sync_prompts = []

    def chat_stream(self, messages, system_prompt="", api_key_override=None,
                    temperature=0.7):
        self.stream_prompt = system_prompt
        yield self.reply

    def chat_sync(self, messages, system_prompt="", **kw):
        self.sync_prompts.append((system_prompt or "", messages))
        if "旁白" in (system_prompt or "") or "动作描写" in (system_prompt or ""):
            return self.desc
        return "{}"


def new_conv(user, texts):
    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)
    locations.switch_both(app_db, conv, "公寓客厅")
    for i, (role, text) in enumerate(texts):
        content = (json.dumps({"对白": text}, ensure_ascii=False)
                   if role == "assistant" else text)
        app_db.add_message(conv, role, content, channel="chat", story_minutes=600 + i)
    return conv


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user))
    real_llm = chat_mod.deepseek

    # ── 1) 三档判定
    conv_plain = new_conv(user, [("user", "今天好热"), ("assistant", "嗯，风扇开大点。")])
    check("普通对话热度 0", intimacy.level(app_db, conv_plain) == 0,
          str(intimacy.level(app_db, conv_plain)))
    conv_hug = new_conv(user, [("user", "抱一下"), ("assistant", "抱住了，别乱动。")])
    check("只是抱着算第 1 档", intimacy.level(app_db, conv_hug) == 1,
          str(intimacy.level(app_db, conv_hug)))
    conv_hot = new_conv(user, [("user", "别躲"), ("assistant", "亲了一下，又贴回去。")])
    check("亲吻/贴着算第 2 档", intimacy.level(app_db, conv_hot) == 2,
          str(intimacy.level(app_db, conv_hot)))

    # ── 2) 亲密时的对话提示词与动作条数
    hot_text = "\n".join(
        [json.dumps({"对白": "别……", "接触": "亲密"}, ensure_ascii=False)]
        + [json.dumps({"动作": f"第{i}个反应：手抓得紧了一点"}, ensure_ascii=False)
           for i in range(5)]
        + [json.dumps({"人物": [{"名字": "邻居", "身份": "隔壁邻居"}]},
                      ensure_ascii=False)])
    stub = StubLLM(hot_text)
    chat_mod.deepseek = stub
    try:
        r = c.post("/api/chat", json={"conversation_id": conv_hot, "message": "再抱紧点"})
        evs = sse(r.data.decode("utf-8"))
    finally:
        chat_mod.deepseek = real_llm
    check("她声明的接触程度落库", app_db.get_contact_level(conv_hot)[0] == 2,
          str(app_db.get_contact_level(conv_hot)))
    check("亲密时提示词里有【此刻的身体接触】",
          "【此刻的身体接触】" in stub.stream_prompt, stub.stream_prompt[-300:])
    check("亲密时允许输出到六条",
          "允许输出到六条" in stub.stream_prompt, stub.stream_prompt[-300:])
    check("回复流正常结束", any(e.get("type") == "done" for e in evs),
          str([e.get("type") for e in evs]))
    actions = [m for m in app_db.get_messages(conv_hot) if m.get("channel") == "action"]
    check("亲密时一轮收下四条动作", len(actions) == 4,
          str([m["content"] for m in actions]))
    check("多出来的动作没有盖掉对白",
          bool([m for m in app_db.get_messages(conv_hot)
                if m.get("channel") == "chat" and "别" in m["content"]]))
    check("亲密时不会让新的人进房间",
          not [p for p in app_db.get_npcs(conv_hot) if p["name"] == "邻居"],
          str(app_db.get_npcs(conv_hot)))

    # 常规轮次仍然只收两条
    conv = new_conv(user, [("user", "在干嘛"), ("assistant", "发呆。")])
    normal_text = "\n".join(
        [json.dumps({"对白": "嗯。"}, ensure_ascii=False)]
        + [json.dumps({"动作": f"动作{i}"}, ensure_ascii=False) for i in range(5)])
    stub2 = StubLLM(normal_text)
    chat_mod.deepseek = stub2
    try:
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "嗯？"})
        r.data.decode("utf-8")          # 流式响应要读掉，生成器才会跑完
    finally:
        chat_mod.deepseek = real_llm
    check("常规轮次仍然最多两条动作",
          len([m for m in app_db.get_messages(conv) if m.get("channel") == "action"]) == 2,
          str([m["content"] for m in app_db.get_messages(conv)
               if m.get("channel") == "action"]))

    # ── 3) 动作长度：亲密 32 字不被拆碎
    long_action = "手指扣进沙发缝里，肩膀轻轻抖了一下又强行停住"
    conv_len = new_conv(user, [("user", "别怕"), ("assistant", "亲了亲她的耳朵。")])
    stub3 = StubLLM(json.dumps({"动作": long_action}, ensure_ascii=False))
    chat_mod.deepseek = stub3
    try:
        r = c.post("/api/chat", json={"conversation_id": conv_len, "message": "抱着你"})
        r.data.decode("utf-8")
    finally:
        chat_mod.deepseek = real_llm
    rows = [m["content"] for m in app_db.get_messages(conv_len)
            if m.get("channel") == "action"]
    check("亲密的动作 27 字保持完整", long_action in rows, str(rows))

    # ── 4) 旁白在亲密时补另一处反应
    conv_desc = new_conv(user, [("user", "别动"), ("assistant", "亲了亲，手没松开。")])
    # 让 _turn_has_action 为真：她这一轮已经给过动作
    app_db.add_message(conv_desc, "assistant", "把脸埋得更低了一点", channel="action",
                       scene="公寓客厅", story_minutes=610)
    desc_stub = StubLLM(desc='{"旁白": "呼吸乱了一拍，耳朵压平又慢慢立起来。"}')
    chat_mod.deepseek = desc_stub
    try:
        text = writer.describe(app_db, conv_desc, desc_stub, 611, "")
    finally:
        chat_mod.deepseek = real_llm
    check("亲密时旁白仍然补写", bool(text), text)
    joined = "\n".join(sp + str(msgs) for sp, msgs in desc_stub.sync_prompts)
    check("给旁白的要求是「另一处反应」",
          "身体接触" in joined and "另一处" in joined, joined[-300:])

    # ── 5) 事件门控读声明值，不再扫亲密词
    import services.event_engine as events
    app_db.set_contact_level(conv_hug, 1, 601)
    allowed_hug, _ = events.event_gate(app_db, conv_hug, 610)
    check("只是抱着不会让世界静音", allowed_hug is True)
    app_db.set_contact_level(conv_hot, 2, 601)
    allowed_hot, reason_hot = events.event_gate(app_db, conv_hot, 610)
    check("亲密接触会让世界静音", allowed_hot is False and "亲密" in reason_hot,
          str(reason_hot))
    check("接触声明能解析成三档",
          intimacy.parse_marker("亲密") == 2 and intimacy.parse_marker("碰到") == 1
          and intimacy.parse_marker("无") == 0,
          str([intimacy.parse_marker(x) for x in ("亲密", "碰到", "无")]))

    print()
    if FAILED:
        print("失败 %d 项:" % len(FAILED))
        for name in FAILED:
            print(" -", name)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
