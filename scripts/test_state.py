"""状态 / 动作 / 短句拆条 的探针（桩模型，零额度）

覆盖：
1. 状态规则：夜里到点睡、天亮迷糊、饭点不动人
2. 睡眠时她不会有反应（不调对话模型，只留一句旁白）
3. 被叫醒时进「迷糊」并正常回复
4. 她自己报的状态（回复里的「状态」字段）会落库
5. 一句里有多短句时，拆成几条独立消息（分开的气泡）
6. 「动作」字段走旁白通道，不进对话框
"""
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "state_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db            # noqa: E402
from core.auth import generate_token         # noqa: E402
import core.locations as locations           # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402
import services.state_engine as st           # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class StubLLM:
    """对话用 chat_stream；小判断用 chat_sync"""

    def __init__(self, reply='{"对白": "嗯。"}', wake=False):
        self.reply = reply
        self.wake = wake
        self.streamed = 0
        self.sync_calls = 0

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        self.streamed += 1
        yield self.reply

    def chat_sync(self, messages, system_prompt="", **kw):
        self.sync_calls += 1
        if "叫醒" in (system_prompt or ""):
            return '{"wake": %s}' % ("true" if self.wake else "false")
        return "{}"


def sse(body):
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except Exception:
                pass
    return out


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user))

    # ── 1) 状态的时间规则
    check("23:30 该睡了", st.rules_state(23 * 60 + 30) == "睡眠", st.rules_state(1410))
    check("凌晨 2 点还是睡", st.rules_state(2 * 60) == "睡眠")
    # 早上七点她当然还在睡（用户定的口径）；醒来是她被叫醒之后的事
    check("7:10 还在睡", st.rules_state(7 * 60 + 10) == "睡眠", st.rules_state(430))
    check("8 点之后不强制状态", st.rules_state(8 * 60 + 1) == "", st.rules_state(481))
    check("中午不强制状态", st.rules_state(12 * 60) == "", st.rules_state(720))

    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)
    locations.switch_both(app_db, conv, "公寓客厅")

    app_db.set_yuxia_state(conv, "清醒")
    check("夜里规则把清醒改成睡眠",
          st.apply_rules(app_db, conv, 23 * 60 + 30) == "睡眠", app_db.get_yuxia_state(conv))
    check("睡眠状态能读出来", st.is_asleep(app_db, conv))
    check("早上七点规则仍然让她睡",
          st.apply_rules(app_db, conv, 7 * 60) == "睡眠", app_db.get_yuxia_state(conv))
    app_db.set_yuxia_state(conv, "清醒")
    check("白天不会把人弄成睡眠",
          st.apply_rules(app_db, conv, 12 * 60) == "清醒", app_db.get_yuxia_state(conv))

    check("她自己报的状态会落库",
          st.apply_reply(app_db, conv, {"状态": "兴奋"}) == "兴奋"
          and app_db.get_yuxia_state(conv) == "兴奋")
    st.apply_reply(app_db, conv, {"状态": "量子态"})
    check("非法状态不认", app_db.get_yuxia_state(conv) == "兴奋")
    app_db.set_yuxia_state(conv, "清醒")

    # 睡没睡以当日日程为准：日程说她醒着，就不能再判她睡着
    # （手机上她正发消息叫你起床、系统却在旁边写「她迷迷糊糊地睁开眼睛」，就是这么来的）
    conv_sched = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_sched)
    app_db.set_day_schedule(conv_sched, 0, json.dumps({"yuxia": [
        {"start": 0, "end": 420, "activity": "睡着", "location": "尤夏的房间",
         "reply": "asleep", "enterable": False, "tag": "sleep"},
        {"start": 420, "end": 1440, "activity": "在图书馆赶作业", "location": "图书馆",
         "reply": "busy", "enterable": True, "tag": "study"},
    ], "qingmei": []}, ensure_ascii=False))
    app_db.set_yuxia_state(conv_sched, "清醒")
    check("日程写着在赶作业：10 点不是睡着",
          st.schedule_asleep(app_db, conv_sched, 10 * 60) is False)
    check("日程说她醒着，就不改睡眠",
          st.apply_rules(app_db, conv_sched, 10 * 60) == "清醒",
          app_db.get_yuxia_state(conv_sched))
    check("日程写着睡：凌晨就是睡",
          st.apply_rules(app_db, conv_sched, 3 * 60) == "睡眠",
          app_db.get_yuxia_state(conv_sched))
    check("日程写着 7 点起床：七点半之后不再判睡",
          st.schedule_asleep(app_db, conv_sched, 8 * 60) is False)

    # ── 2) 到点睡着：只由世界说一句「尤夏睡着了」，不再拦她的回复
    # 把故事时钟拨到深夜：否则 07:00 的"天亮迷糊"规则会立刻把她叫醒
    app_db.set_story_minutes(conv, 23 * 60 + 30)
    app_db.set_yuxia_state(conv, "清醒")
    real = chat_mod.deepseek
    try:
        llm = StubLLM(reply='{"对白": "……嗯。"}')
        chat_mod.deepseek = llm
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "还没睡？"})
        evs = sse(r.data.decode("utf-8"))
        done = next((e for e in evs if e.get("type") == "done"), {})
        check("睡着不再标记 sleeping、也不早退", done.get("sleeping") is not True, str(done)[:120])
        check("睡着时仍然回话", llm.streamed == 1, str(llm.streamed))
        states = [m for m in app_db.get_messages(conv) if m["channel"] == "state"]
        check("睡着这件事由世界说一句",
              any("睡着了" in (m["content"] or "") for m in states),
              str([m["content"] for m in states]))
        check("世界那句在流里也发了",
              any(e.get("type") == "yuxia_state" and "睡着了" in (e.get("content") or "")
                  for e in evs),
              str([e for e in evs if e.get("type") == "yuxia_state"])[:160])
        check("答完话状态是迷糊（被叫醒）", app_db.get_yuxia_state(conv) == "迷糊",
              app_db.get_yuxia_state(conv))
        check("醒来也有一句提示",
              any("迷糊" in (e.get("content") or "") for e in evs
                  if e.get("type") == "yuxia_state"), str(evs)[:160])

        # ── 2b) 夜里连着聊：「睡着了」只说一次，不会被规则反复按回床上
        app_db.set_yuxia_state(conv, "睡眠")
        for q in ("还是冷", "把你的被子给我一点", "再聊两句"):
            chat_mod.deepseek = llm
            r2 = c.post("/api/chat", json={"conversation_id": conv, "message": q})
            r2.data.decode("utf-8")
        sleep_notes = [m for m in app_db.get_messages(conv)
                       if m["channel"] == "state" and "睡着了" in (m["content"] or "")]
        check("夜里连着聊，「睡着了」只出现一次", len(sleep_notes) == 1,
              str([m["content"] for m in sleep_notes]))

        # ── 3) 再问一句：不受睡眠状态影响，继续正常回
        app_db.set_yuxia_state(conv, "睡眠")
        llm2 = StubLLM(reply='{"对白": "唔姆。……怕打雷。"}')
        chat_mod.deepseek = llm2
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "你最怕什么"})
        r.data.decode("utf-8")
        check("睡着时被问到也会回答", llm2.streamed == 1, str(llm2.streamed))
        check("回完状态是迷糊", app_db.get_yuxia_state(conv) == "迷糊",
              app_db.get_yuxia_state(conv))

        # ── 2c) 手机通道里不该出现「她迷迷糊糊睁开眼」这类只看得到人的旁白
        conv_far = app_db.create_conversation(user)
        locations.ensure_defaults(app_db, conv_far)
        locations.switch_both(app_db, conv_far, "公寓客厅")
        app_db.set_player_location(conv_far, "厨房")     # 她在客厅、你在厨房：异地
        app_db.set_story_minutes(conv_far, 23 * 60 + 30)
        app_db.set_yuxia_state(conv_far, "睡眠")
        llm3 = StubLLM(reply='{"对白": "唔姆。……还没睡吗。"}')
        chat_mod.deepseek = llm3
        r = c.post("/api/chat", json={"conversation_id": conv_far, "message": "还在睡吗"})
        evs_far = sse(r.data.decode("utf-8"))
        check("异地时不播报睡着/醒来",
              not any(e.get("type") == "yuxia_state" for e in evs_far),
              str([e for e in evs_far if e.get("type") == "yuxia_state"])[:160])
        states_far = [m for m in app_db.get_messages(conv_far) if m["channel"] == "state"]
        check("异地时也不往库里写这段旁白", not states_far,
              str([m["content"] for m in states_far]))
        check("异地照样回消息", llm3.streamed == 1, str(llm3.streamed))
    finally:
        chat_mod.deepseek = real

    # ── 3b) 模型一个字都不给时会再要一次（试玩里出现过空回复）
    conv_empty = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_empty)
    locations.switch_both(app_db, conv_empty, "公寓客厅")

    class SilentLLM(StubLLM):
        def __init__(self):
            super().__init__()
            self.rounds = 0

        def chat_stream(self, messages, system_prompt="", **kw):
            self.rounds += 1
            if self.rounds == 1:
                yield ""
            else:
                yield '{"对白": "……嗯。"}'

    silent = SilentLLM()
    chat_mod.deepseek = silent
    try:
        r = c.post("/api/chat", json={"conversation_id": conv_empty, "message": "问你个事"})
        r.data.decode("utf-8")
        check("空回复会再问一次", silent.rounds == 2, str(silent.rounds))
        rows = [m for m in app_db.get_messages(conv_empty)
                if m["channel"] == "chat" and m["role"] == "assistant"]
        check("补回来的那句话真的落库", bool(rows) and "嗯" in rows[0]["content"],
              str([m["content"] for m in rows]))
    finally:
        chat_mod.deepseek = real

    # ── 4) 多短句拆成几条消息 / 动作走旁白
    conv2 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv2)
    locations.switch_both(app_db, conv2, "公寓客厅")
    parts = chat_mod._bubble_parts("唔姆。还行。就是忘了几点睡的。")
    check("一句里多句会拆开", parts == ["唔姆。", "还行。", "就是忘了几点睡的。"], str(parts))
    check("省略号不算断句", chat_mod._bubble_parts("……没想过不行吗") == ["……没想过不行吗"],
          str(chat_mod._bubble_parts("……没想过不行吗")))
    check("拆完不丢字", "".join(parts) == "唔姆。还行。就是忘了几点睡的。")

    chat_mod._persist_reply(
        conv2, '{"对白": "唔姆。还行。", "动作": "把外套往他那边挪了挪", "心情": "平静"}',
        True, "公寓客厅", 600, "chat")
    chat_rows = [m for m in app_db.get_messages(conv2) if m["channel"] == "chat"]
    action_rows = [m for m in app_db.get_messages(conv2) if m["channel"] == "action"]
    check("两句对白落成两条消息", len(chat_rows) == 2, str([m["content"] for m in chat_rows]))
    check("每条都是独立 JSON", all(m["content"].startswith("{") for m in chat_rows),
          str([m["content"] for m in chat_rows]))
    check("动作进独立的动作通道、不进对话框",
          len(action_rows) == 1 and "外套" in action_rows[0]["content"],
          str([m["content"] for m in action_rows]))
    check("动作不再混进神态通道",
          not [m for m in app_db.get_messages(conv2) if m["channel"] == "state"],
          str([m["content"] for m in app_db.get_messages(conv2) if m["channel"] == "state"]))
    check("心情只挂在第一条上",
          "心情" in chat_rows[0]["content"] and "心情" not in chat_rows[1]["content"],
          str([m["content"] for m in chat_rows]))

    # 只说动作、不说话也是合法的一轮
    conv3 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv3)
    locations.switch_both(app_db, conv3, "公寓客厅")
    chat_mod._persist_reply(conv3, '{"动作": "把手伸进袖子里", "状态": "饥饿"}',
                            True, "公寓客厅", 600, "chat")
    check("可以只做动作不回答",
          not [m for m in app_db.get_messages(conv3) if m["channel"] == "chat"]
          and [m for m in app_db.get_messages(conv3) if m["channel"] == "action"],
          str([(m["channel"], m["content"]) for m in app_db.get_messages(conv3)]))
    check("动作那一轮也能改状态", app_db.get_yuxia_state(conv3) == "饥饿",
          app_db.get_yuxia_state(conv3))

    # ── 4b) 动作要当场推给前端（手机 App 就靠这个事件显示旁白）
    conv_act = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_act)
    locations.switch_both(app_db, conv_act, "公寓客厅")
    try:
        act_llm = StubLLM(reply='{"对白": "嗯。", "动作": "把毯子往她那边拉了拉"}')
        chat_mod.deepseek = act_llm
        r = c.post("/api/chat", json={"conversation_id": conv_act, "message": "冷吗"})
        evs = sse(r.data.decode("utf-8"))
        acts = [e for e in evs if e.get("type") == "yuxia_action"]
        check("动作在流里当场发出来", bool(acts) and "毯子" in acts[0].get("content", ""),
              str([e for e in evs if e.get("type") == "yuxia_action"])[:160])
        check("动作事件在 done 之前",
              [e.get("type") for e in evs].index("yuxia_action")
              < [e.get("type") for e in evs].index("done"),
              str([e.get("type") for e in evs]))
        hist = c.get(f"/api/history/{conv_act}").get_json() or {}
        chips = [m for m in (hist.get("messages") or []) if m.get("channel") == "action"]
        check("历史里动作单独一条（不跟神态混）",
              len(chips) == 1 and "毯子" in chips[0]["content"], str(chips)[:160])
    finally:
        chat_mod.deepseek = real

    # ── 4c) 异地（手机通道）：她不该写动作，更不该有「被抱住」这种画面
    conv_far = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_far)
    app_db.set_current_location(conv_far, "公寓玄关")
    app_db.set_player_location(conv_far, "公寓客厅")

    class PhoneStub(StubLLM):
        def __init__(self):
            super().__init__(reply='{"消息": "隔着手机抱不到啦", "动作": "被抱住，脸往她肩上贴"}')
            self.prompt = ""

        def chat_stream(self, messages, system_prompt="", **kw):
            self.prompt = system_prompt
            yield self.reply

    phone_llm = PhoneStub()
    chat_mod.deepseek = phone_llm
    try:
        # 玩家在手机通道里「做出动作」：系统当成一行文字送给她，她要用文字回
        r = c.post("/api/chat", json={"conversation_id": conv_far, "message": "（抱住她）"})
        r.data.decode("utf-8")
        rows = app_db.get_messages(conv_far)
        act_rows = [m for m in rows if m["channel"] == "action"]
        phone_rows = [m for m in rows if m["channel"] == "phone"]
        check("异地时身体接触的动作被丢掉", not act_rows, str([m["content"] for m in act_rows]))
        check("异地时她用消息回", any("抱不到" in m["content"] for m in phone_rows),
              str([m["content"] for m in phone_rows]))
        check("提示词写明不在一起、看不见对方",
              "不在一起，看不见对方" in phone_llm.prompt, phone_llm.prompt[-260:])
        check("提示词把括号动作当成一行字",
              "只是她打的一行字" in phone_llm.prompt, phone_llm.prompt[-260:])
        user_rows = [m for m in rows if m["role"] == "user"]
        check("玩家的动作原文进了时间线",
              any("抱住她" in m["content"] for m in user_rows),
              str([m["content"] for m in user_rows]))
    finally:
        chat_mod.deepseek = real

    # ── 4d) 异地时允许她一个人做事（推进剧情），动作带时间
    conv_far2 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_far2)
    app_db.set_current_location(conv_far2, "便利店")
    app_db.set_player_location(conv_far2, "公寓客厅")
    app_db.set_story_minutes(conv_far2, 15 * 60 + 20)

    class SoloLLM(StubLLM):
        def __init__(self):
            super().__init__(reply='{"消息": "买到了", "动作": "把饭团放进袋子里"}')

    chat_mod.deepseek = SoloLLM()
    try:
        r = c.post("/api/chat", json={"conversation_id": conv_far2, "message": "买个饭团"})
        r.data.decode("utf-8")
        acts = [m for m in app_db.get_messages(conv_far2) if m["channel"] == "action"]
        check("异地时一个人做的事照收", len(acts) == 1 and "饭团" in acts[0]["content"],
              str([m["content"] for m in acts]))
        minutes = int(acts[0].get("story_minutes") or 0) if acts else 0
        check("动作带上故事时间", 15 * 60 + 20 <= minutes <= 15 * 60 + 40, str(minutes))
        ctx = chat_mod._context_window(conv_far2)
        joined = json.dumps(ctx, ensure_ascii=False)
        check("回灌给她的动作里带「时间」",
              "时间" in joined and "15:2" in joined, joined[-200:])
    finally:
        chat_mod.deepseek = real

    # ── 4e) 时间锚定真实世界：不打字也在走；手动拨时被拒
    import time as _time
    import services.time_keeper as tk
    conv_clock = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv_clock)
    app_db.set_story_minutes(conv_clock, 9 * 60)
    base = tk.init_clock(app_db, conv_clock)
    check("刚写过锚点，时间不走样", abs(base - 9 * 60) <= 1, str(base))
    # 把锚点往前挪一小时，模拟「一小时没人说话」
    with app_db._get_conn() as conn:
        conn.execute("UPDATE conversations SET story_at=? WHERE id=?",
                     (_time.time() - 3600, conv_clock))
    drifted = tk.init_clock(app_db, conv_clock)
    check("不打字时间也在流（1 小时 ≈ 60 分钟）",
          60 <= drifted - 9 * 60 <= 62, str(drifted - 9 * 60))

    r = c.post(f"/api/time/{conv_clock}", json={"hour": 18, "minute": 0})
    check("手动拨时钟被拒", r.status_code == 400, str(r.status_code))
    check("拒绝原因写清楚", "真实世界" in (r.get_json() or {}).get("error", ""),
          str(r.get_json()))

    # 关键词跳时也关掉了：说「晚安」不再跳到第二天
    now2, jumped = tk.apply_message_tick(app_db, conv_clock, "我去睡了，晚安")
    check("关键词不再拨时钟", jumped is None, str(jumped))
    check("晚安之后仍在同一天", now2 // 1440 == 9 * 60 // 1440 or now2 < 24 * 60,
          str(now2))

    # ── 5) 随机事件可以改变她的状态（忙碌 / 感冒 / 发烧），并且会自然过期
    from services import event_engine
    conv4 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv4)
    app_db.set_yuxia_state(conv4, "清醒")

    event_engine._apply_event_state(app_db, conv4, "感冒", 600)
    check("事件能把状态改成感冒", app_db.get_yuxia_state(conv4) == "感冒",
          app_db.get_yuxia_state(conv4))
    check("状态记录了进入时间", app_db.get_yuxia_state_at(conv4) == 600,
          str(app_db.get_yuxia_state_at(conv4)))
    event_engine._apply_event_state(app_db, conv4, "量子态", 600)
    check("事件给非法状态时不改", app_db.get_yuxia_state(conv4) == "感冒")
    event_engine._apply_event_state(app_db, conv4, "", 600)
    check("事件没写状态时不改", app_db.get_yuxia_state(conv4) == "感冒")

    # 夜里规则不该把生病的人强行改成睡着
    check("生病时不被夜里规则改成睡眠",
          st.apply_rules(app_db, conv4, 23 * 60 + 30) == "感冒",
          app_db.get_yuxia_state(conv4))

    # 感冒 6 小时、忙碌 90 分钟后自然恢复
    check("感冒没好之前不会过期",
          st.expire(app_db, conv4, 600 + 300) == "感冒", st.expire(app_db, conv4, 600 + 300))
    check("感冒过了 6 小时自动好",
          st.expire(app_db, conv4, 600 + 361) == "清醒", st.expire(app_db, conv4, 600 + 361))
    event_engine._apply_event_state(app_db, conv4, "忙碌", 2000)
    check("忙碌 90 分钟后自动闲下来",
          st.expire(app_db, conv4, 2000 + 91) == "清醒", st.expire(app_db, conv4, 2000 + 91))

    event_engine._apply_event_state(app_db, conv4, "发烧", 3000)
    check("事件能把状态改成发烧", app_db.get_yuxia_state(conv4) == "发烧",
          app_db.get_yuxia_state(conv4))
    check("发烧有自己的说话风格", st.STYLE.get("发烧") not in (None, ""), st.STYLE.get("发烧"))

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
