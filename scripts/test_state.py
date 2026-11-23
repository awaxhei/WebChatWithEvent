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
    check("7:10 刚醒，迷糊", st.rules_state(7 * 60 + 10) == "迷糊")
    check("中午不强制状态", st.rules_state(12 * 60) == "", st.rules_state(720))

    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)
    locations.switch_both(app_db, conv, "公寓客厅")

    app_db.set_yuxia_state(conv, "清醒")
    check("夜里规则把清醒改成睡眠",
          st.apply_rules(app_db, conv, 23 * 60 + 30) == "睡眠", app_db.get_yuxia_state(conv))
    check("睡眠状态能读出来", st.is_asleep(app_db, conv))
    check("天亮后从睡眠醒来变迷糊",
          st.apply_rules(app_db, conv, 7 * 60) == "迷糊", app_db.get_yuxia_state(conv))
    app_db.set_yuxia_state(conv, "清醒")
    check("白天不会把人弄成睡眠",
          st.apply_rules(app_db, conv, 12 * 60) == "清醒", app_db.get_yuxia_state(conv))

    check("她自己报的状态会落库",
          st.apply_reply(app_db, conv, {"状态": "兴奋"}) == "兴奋"
          and app_db.get_yuxia_state(conv) == "兴奋")
    st.apply_reply(app_db, conv, {"状态": "量子态"})
    check("非法状态不认", app_db.get_yuxia_state(conv) == "兴奋")
    app_db.set_yuxia_state(conv, "清醒")

    # ── 2) 睡眠时没有反应（不调对话模型）
    # 把故事时钟拨到深夜：否则 07:00 的"天亮迷糊"规则会立刻把她叫醒
    app_db.set_story_minutes(conv, 23 * 60 + 30)
    app_db.set_yuxia_state(conv, "睡眠")
    real = chat_mod.deepseek
    try:
        llm = StubLLM(wake=False)
        chat_mod.deepseek = llm
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "我回来了"})
        evs = sse(r.data.decode("utf-8"))
        done = next((e for e in evs if e.get("type") == "done"), {})
        check("睡着时标记 sleeping", done.get("sleeping") is True, str(done)[:120])
        check("睡着时不调用对话模型（省额度）", llm.streamed == 0, str(llm.streamed))
        states = [m for m in app_db.get_messages(conv) if m["channel"] == "state"]
        # 睡着也要有极小反应：一句旁白，但绝不产生对话气泡
        check("睡着时留一句极小反应的旁白",
              len(states) == 1 and states[0]["content"] in chat_mod._SLEEP_NOTES,
              str([m["content"] for m in states]))
        chat_rows = [m for m in app_db.get_messages(conv)
                     if m["channel"] in ("chat", "phone") and m["role"] == "assistant"]
        check("睡着时不产生对话气泡", not chat_rows,
              str([(m["channel"], m["content"]) for m in chat_rows]))
        check("睡着时状态没被改掉", app_db.get_yuxia_state(conv) == "睡眠")

        # ── 3) 被叫醒 → 迷糊 + 正常回复
        llm2 = StubLLM(reply='{"对白": "唔姆。……早。"}', wake=True)
        chat_mod.deepseek = llm2
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "尤夏？"})
        evs = sse(r.data.decode("utf-8"))
        check("叫醒时走了正常回复", llm2.streamed == 1, str(llm2.streamed))
        check("叫醒后状态变迷糊", app_db.get_yuxia_state(conv) == "迷糊",
              app_db.get_yuxia_state(conv))
        check("打了一声招呼说还没醒",
              any(e.get("type") == "state" and "迷糊" in (e.get("content") or "") for e in evs),
              str([e for e in evs if e.get("type") == "state"])[:120])
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
    state_rows = [m for m in app_db.get_messages(conv2) if m["channel"] == "state"]
    check("两句对白落成两条消息", len(chat_rows) == 2, str([m["content"] for m in chat_rows]))
    check("每条都是独立 JSON", all(m["content"].startswith("{") for m in chat_rows),
          str([m["content"] for m in chat_rows]))
    check("动作进旁白通道、不进对话框",
          len(state_rows) == 1 and "外套" in state_rows[0]["content"],
          str([m["content"] for m in state_rows]))
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
          and [m for m in app_db.get_messages(conv3) if m["channel"] == "state"],
          str([(m["channel"], m["content"]) for m in app_db.get_messages(conv3)]))
    check("动作那一轮也能改状态", app_db.get_yuxia_state(conv3) == "饥饿",
          app_db.get_yuxia_state(conv3))

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
