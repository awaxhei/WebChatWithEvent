"""实体 / 手机 NPC / 事件门控 / 导出 / 时钟 的探针（桩模型，零额度）

覆盖这次改动的四个层面：
1. 她说到的物件、人物真的落库（props / npcs），手机里第三人发消息带说话人
2. 手机里可以和联系人说话：联系人真的回一条，落库带 speaker
3. 偶然事件看氛围：亲密时刻 / 冷却期内 / 她睡着时都不安排；事件里的东西和人落地
4. 会话导出、场景时钟的实时同步字段、口头禅额度
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "entities_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db            # noqa: E402
from core.auth import generate_token         # noqa: E402
import core.clock as clock                   # noqa: E402
import core.locations as locations           # noqa: E402
import core.tics as tics                     # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402
import services.event_engine as events       # noqa: E402
import services.npc as npc                   # noqa: E402
import services.state_engine as state        # noqa: E402

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
    """对话 chat_stream 按设定输出；小调用 chat_sync 按提示词区分"""

    def __init__(self, reply='{"对白": "嗯。"}', event=None):
        self.reply = reply
        self.event = event
        self.stream_prompt = ""
        self.phone_replies = 0

    def chat_stream(self, messages, system_prompt="", api_key_override=None,
                    temperature=0.7):
        self.stream_prompt = system_prompt
        yield self.reply

    def chat_sync(self, messages, system_prompt="", **kw):
        sp = system_prompt or ""
        # NPC 手机回复（NPC_PHONE_PROMPT）
        if "手机发消息" in sp or "发出去的文字" in sp:
            self.phone_replies += 1
            return "我这边忙完就过去"
        if self.event is not None and "呼吸" in sp:
            return json.dumps(self.event, ensure_ascii=False)
        return "{}"


def new_conv(user, place="公寓客厅", both=True):
    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)
    if both:
        locations.switch_both(app_db, conv, place)
    return conv


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user))
    real_llm = chat_mod.deepseek

    # ── 1) 她声明的东西/人真的存在，手机里第三人发消息带 speaker
    conv = new_conv(user)
    app_db.set_story_minutes(conv, 15 * 60 + 20)
    reply = "\n".join([
        '{"对白": "嗯，箱子在玄关。"}',
        '{"物件": [{"名字": "快递箱", "状态": "玄关，写着青梅的名字，还没拆"}]}',
        '{"人物": [{"名字": "妈妈", "身份": "尤夏的母亲", "电话": true}]}',
        '{"说话人": "妈妈", "消息": "到楼下了，别下来接"}',
    ])
    stub = StubLLM(reply)
    chat_mod.deepseek = stub
    try:
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "门口是什么"})
        evs = sse(r.data.decode("utf-8"))
    finally:
        chat_mod.deepseek = real_llm
    check("回复流正常结束", any(e.get("type") == "done" for e in evs),
          str([e.get("type") for e in evs]))
    props = {p["name"]: p["state"] for p in app_db.get_props(conv, "公寓客厅")}
    check("声明的快递箱真的落库", "快递箱" in props and "还没拆" in props.get("快递箱", ""),
          str(props))
    contacts = app_db.get_phone_contacts(conv)
    check("声明的妈妈成为电话联系人",
          [p["name"] for p in contacts] == ["妈妈"] and contacts[0]["phone"] == 1,
          str(contacts))
    check("联系人被标记为剧情点名的人", contacts and contacts[0]["declared"] == 1,
          str(contacts))
    phone_rows = app_db.get_phone_messages(conv)
    mom_rows = [m for m in phone_rows if (m.get("speaker") or "") == "妈妈"]
    check("妈妈的消息带说话人落库",
          len(mom_rows) == 1 and "楼下了" in mom_rows[0]["content"], str(phone_rows))
    hist = c.get(f"/api/history/{conv}").get_json()
    check("历史接口把说话人带给前端",
          any((m.get("speaker") or "") == "妈妈" for m in hist.get("phone", [])),
          str(hist.get("phone")))

    # ── 2) 手机里可以跟联系人说话：联系人真的回一条
    conv2 = new_conv(user, "尤夏的房间", both=False)
    app_db.set_player_location(conv2, "青梅的房间")
    app_db.set_story_minutes(conv2, 18 * 60 + 5)
    npc.ensure_person(app_db, conv2, "妈妈", role="尤夏的母亲", phone=True)
    stub2 = StubLLM('{"消息": "我在楼下等你"}')
    chat_mod.deepseek = stub2
    try:
        r = c.post("/api/chat", json={"conversation_id": conv2,
                                      "message": "妈妈，你到哪了"})
        evs = sse(r.data.decode("utf-8"))
    finally:
        chat_mod.deepseek = real_llm
    kinds = [e.get("type") for e in evs]
    check("手机里对联系人说话会有人回", "npc_phone" in kinds, str(kinds))
    check("联系人回复确实调用了一次",
          stub2.phone_replies == 1, str(stub2.phone_replies))
    got = [m for m in app_db.get_phone_messages(conv2) if m.get("speaker") == "妈妈"]
    check("联系人回复进了手机通道", got and "忙完" in got[-1]["content"],
          str(app_db.get_phone_messages(conv2)))

    # ── 2b) 她说「我去问问妈妈」：妈妈会真的回一句（不靠模型自己写说话人）
    conv2b = new_conv(user, "尤夏的房间", both=False)
    app_db.set_player_location(conv2b, "青梅的房间")
    npc.ensure_person(app_db, conv2b, "妈妈", role="尤夏的母亲", phone=True)
    stub2b = StubLLM('{"消息": "我去问问妈妈"}')
    chat_mod.deepseek = stub2b
    try:
        r = c.post("/api/chat", json={"conversation_id": conv2b, "message": "周末有空吗"})
        evs = sse(r.data.decode("utf-8"))
    finally:
        chat_mod.deepseek = real_llm
    check("说要问妈妈时，妈妈真的回了",
          any(e.get("type") == "npc_phone" for e in evs),
          str([e.get("type") for e in evs]))
    mom2b = [m for m in app_db.get_phone_messages(conv2b)
             if m.get("speaker") == "妈妈"]
    check("妈妈的回复落进手机历史", bool(mom2b), str(app_db.get_phone_messages(conv2b)))

    # ── 3) 事件门控：亲密时刻 / 冷却 / 睡着
    conv3 = new_conv(user)
    app_db.add_message(conv3, "user", "别走", story_minutes=600)
    app_db.add_message(conv3, "assistant", json.dumps({"对白": "我把你抱紧，亲了一下。"},
                                                      ensure_ascii=False),
                       channel="chat", story_minutes=601)
    check("亲密时刻被认出来", events.scene_is_intimate(app_db, conv3))
    allowed, reason = events.event_gate(app_db, conv3, 610)
    check("亲密之后一段时间不安排偶然事件", not allowed and "亲密" in reason, reason)
    conv4 = new_conv(user)
    app_db.add_message(conv4, "user", "今天天气不错", story_minutes=900)
    app_db.add_message(conv4, "assistant", json.dumps({"对白": "嗯，有点晒。"},
                                                      ensure_ascii=False),
                       channel="chat", story_minutes=901)
    app_db.set_last_random_event_minutes(conv4, 900)
    allowed, reason = events.event_gate(app_db, conv4, 910)
    check("两次插曲之间要隔一会儿", not allowed and "近" in reason, reason)
    allowed, _ = events.event_gate(app_db, conv4, 940)
    check("隔开之后可以再安排", allowed)
    state.set_state(app_db, conv4, "睡眠", 940)
    allowed, reason = events.event_gate(app_db, conv4, 940)
    check("她睡着时不安排事件", not allowed and "睡" in reason, reason)

    # ── 4) 偶然事件里的东西、人都落地；地点不对就丢掉
    conv5 = new_conv(user)
    for i in range(6):
        app_db.add_message(conv5, "user", f"第{i}句话", story_minutes=1200 + i)
        app_db.add_message(conv5, "assistant",
                           json.dumps({"对白": f"第{i}句回答。"}, ensure_ascii=False),
                           channel="chat", story_minutes=1200 + i)
    event = {
        "random_event": {
            "type": "random",
            "title": "快递到了",
            "description": "快递员把箱子放在门口，纸箱上写着青梅的名字。",
            "location": "公寓客厅",
            "intensity": "low",
            "objects": [{"名字": "快递箱", "状态": "门口，写着青梅的名字，还没拆"}],
            "people": [{"名字": "快递员", "身份": "送快递的人"}],
        }
    }
    ev_stub = StubLLM(event=event)
    got_event = events._run_random_event(conv5, app_db, ev_stub, now_minutes=1210)
    check("事件生成成功", bool(got_event), str(got_event))
    props5 = {p["name"]: p["state"] for p in app_db.get_props(conv5, "公寓客厅")}
    check("事件里的快递箱是真的", "快递箱" in props5, str(props5))
    people5 = [p for p in app_db.get_npcs(conv5) if p["name"] == "快递员"]
    check("事件里的快递员站在门口", people5 and people5[0]["location"] == "公寓客厅",
          str(people5))
    check("事件记了发生时间，用来冷却",
          app_db.get_last_random_event_minutes(conv5) == 1210)
    bad_event = json.loads(json.dumps(event))
    bad_event["random_event"]["location"] = "月球背面"
    got_bad = events._run_random_event(conv5, app_db, StubLLM(event=bad_event),
                                       now_minutes=1220)
    check("发生在看不见的地方的事件被丢掉", got_bad is None, str(got_bad))

    # ── 5) 导出会话：对话 / 事件 / 出场的人 / 物件
    exp = c.get(f"/api/export/{conv5}")
    body = exp.data.decode("utf-8-sig")
    check("导出接口返回纯文本", exp.status_code == 200 and "会话导出" in body,
          str(exp.status_code))
    check("导出里有对话", "青梅" in body and "尤夏" in body, body[:120])
    check("导出里有故事进展", "故事进展" in body and "快递到了" in body)
    check("导出里有出场的人", "出场的人" in body and "快递员" in body)
    check("导出里有物件清单", "物件" in body and "快递箱" in body)

    # ── 6) 时钟字段：场景接口带分钟数与同步时刻，闲置接口也带
    scene = c.get(f"/api/scene/{conv5}").get_json()
    check("场景接口带 story_minutes",
          isinstance(scene.get("story_minutes"), int) and scene["story_minutes"] > 0,
          str(scene.get("story_minutes")))
    check("场景接口带 sync_at", scene.get("sync_at", 0) > 0, str(scene.get("sync_at")))
    idle_stub = StubLLM('{"消息": "在干嘛"}')
    chat_mod.deepseek = idle_stub
    try:
        idle_res = c.post(f"/api/idle/{conv5}").get_json()
    finally:
        chat_mod.deepseek = real_llm
    check("闲置接口总是带回当前时间（哪怕她没动作）",
          isinstance(idle_res.get("story_minutes"), int)
          and idle_res.get("story_minutes") > 0 and idle_res.get("sync_at", 0) > 0,
          str(idle_res))

    # ── 7) 口头禅额度：最近说过就这一轮一个都不留
    app_db.add_message(conv5, "assistant", json.dumps({"对白": "唔姆，知道了。"},
                                                      ensure_ascii=False),
                       channel="chat", story_minutes=1230)
    check("最近说过「唔姆」时额度为 0", tics.budget_for(app_db, conv5) == 0,
          str(tics.budget_for(app_db, conv5)))
    check("额度为 0 时整句里的「唔姆」都被洗掉",
          "唔姆" not in tics.Budget(0).clean("唔姆，我知道了。"),
          tics.Budget(0).clean("唔姆，我知道了。"))

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
