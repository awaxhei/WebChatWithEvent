"""
自主行动探针（v6：由模型判断，不掷骰子；桩模型零额度）
校验：LLM 决定 speak / phone / move、通道约束、连续输出上限、最小间隔
"""
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "auto_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app                          # noqa: E402
import core.locations as locations            # noqa: E402
import services.autonomy as autonomy          # noqa: E402

db = database.ChatDB()
FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class StubLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_user_msg = ""

    def chat_sync(self, messages, *a, **kw):
        self.calls += 1
        try:
            self.last_user_msg = messages[0]["content"]
        except Exception:
            self.last_user_msg = ""
        return self.payload


def fresh():
    conv = db.create_conversation(1)
    locations.ensure_defaults(db, conv)
    db.add_message(conv, "user", "你刚才想说什么？", channel="chat")
    db.set_last_user_at(conv, time.time())
    return conv


def main():
    app.config["TESTING"] = True

    # 1) 同处一地：模型说 speak → 记到 chat 通道
    conv = fresh()
    llm = StubLLM('{"action": "speak", "text": "……没什么。", "location": ""}')
    acted = autonomy.maybe_act(db, conv, llm, 600, "现在是傍晚", idle=False)
    check("模型决定开口", bool(acted) and acted["action"] == "speak", str(acted))
    check("speak 落到 chat 通道", acted and acted["channel"] == "chat")
    check("确实调用了模型", llm.calls == 1)
    msgs = [m for m in db.get_messages(conv) if m["channel"] == "chat" and m["role"] == "assistant"]
    check("消息已入库", any(m["content"] == "……没什么。" for m in msgs))

    # 2) 异地：模型说 speak → 强制改成 phone
    conv2 = fresh()
    db.set_player_location(conv2, "厨房")
    locations.switch(db, conv2, "公寓客厅")
    acted2 = autonomy.maybe_act(db, conv2, StubLLM('{"action": "speak", "text": "你在哪", "location": ""}'),
                                600, "异地", idle=False)
    check("异地 speak 自动改成 phone", acted2 and acted2["action"] == "phone", str(acted2))
    check("phone 落到手机通道", acted2 and acted2["channel"] == "phone")

    # 3) 模型说 none → 不做任何事
    conv3 = fresh()
    llm3 = StubLLM('{"action": "none", "text": "", "location": ""}')
    check("模型选 none 时不动作", autonomy.maybe_act(db, conv3, llm3, 600, "平静", idle=False) is None)
    check("none 也会真实询问模型", llm3.calls == 1)

    # 4) move：走到新地点并登记
    conv4 = fresh()
    acted4 = autonomy.maybe_act(db, conv4, StubLLM('{"action": "move", "text": "", "location": "学校天台"}'),
                                600, "想上去", idle=False)
    check("模型决定换场景", acted4 and acted4["action"] == "move" and acted4["location"] == "学校天台",
          str(acted4))
    check("新地点已登记", "学校天台" in locations.list_names(db, conv4))

    # 5) 连续自主输出上限：超过上限不再询问模型
    conv5 = fresh()
    for i in range(1 + autonomy.AUTONOMY_MAX_CONSECUTIVE):
        db.add_message(conv5, "assistant", f"第{i}句", channel="chat")
    llm5 = StubLLM('{"action": "speak", "text": "继续说", "location": ""}')
    check("超上限后不再动作", autonomy.maybe_act(db, conv5, llm5, 600, "x", idle=False) is None)
    check("超上限时不再调用模型（省额度）", llm5.calls == 0, f"calls={llm5.calls}")

    # 6) 闲置最小间隔：刚说过话 → 不问
    conv6 = fresh()
    llm6 = StubLLM('{"action": "speak", "text": "嗯", "location": ""}')
    check("刚发言时闲置不触发", autonomy.maybe_act(db, conv6, llm6, 600, "x", idle=True) is None)
    check("时间不足时不调用模型", llm6.calls == 0)

    db.set_last_user_at(conv6, time.time() - autonomy.AUTONOMY_MIN_INTERVAL_SECONDS - 5)
    acted6 = autonomy.maybe_act(db, conv6, llm6, 600, "x", idle=True)
    check("超过最小间隔后由模型判断", bool(acted6) and llm6.calls == 1, str(acted6))

    # 7) 她能读到随机事件（提示词里带最近事件）
    conv7 = fresh()
    db.add_event(conv7, "random", "水槽水管爆开", "旧水管突然爆开，水漫了一地", "active",
                 story_time="第一天 07:12", tier="high")
    llm7 = StubLLM('{"action": "act", "text": "去关水阀，顺手把地上的水擦掉", "location": ""}')
    acted7 = autonomy.maybe_act(db, conv7, llm7, 600, "情境", idle=False)
    check("随机事件进入了自主行动提示词", "水槽水管爆开" in llm7.last_user_msg, llm7.last_user_msg[:200])
    check("提示词里带时间与场景", "第一天 07:12" in llm7.last_user_msg or "此刻" in llm7.last_user_msg)

    # 8) act：她真的做一件事，写进活动与事件
    check("act 被识别为行动", bool(acted7) and acted7["action"] == "act", str(acted7))
    activity, _at = db.get_yuxia_activity(conv7)
    check("活动已记录", activity == "去关水阀，顺手把地上的水擦掉", activity)
    kinds = [e["event_type"] for e in db.get_recent_events(conv7, limit=10)]
    check("act 记进事件表", "misc" in kinds, str(kinds))
    check("同处一地时 act 对玩家可见", acted7.get("visible") is True)

    # 9) 防重复：和刚才做过的高度相似 → 丢弃
    again = StubLLM('{"action": "act", "text": "去关水阀，把地上的水擦掉", "location": ""}')
    check("重复的 act 被拦截", autonomy.maybe_act(db, conv7, again, 610, "情境", idle=False) is None)

    # 10) 换了内容就可以继续
    newone = StubLLM('{"action": "act", "text": "把湿掉的抹布拿去阳台晾着", "location": ""}')
    acted9 = autonomy.maybe_act(db, conv7, newone, 620, "情境", idle=False)
    check("不同内容可以继续行动", bool(acted9) and acted9["action"] == "act", str(acted9))
    activity2, _at2 = db.get_yuxia_activity(conv7)
    check("活动被更新", activity2 == "把湿掉的抹布拿去阳台晾着", activity2)

    # 11) 重复的台词同样拦截
    conv11 = fresh()
    db.add_message(conv11, "assistant", "你饿不饿", channel="chat")
    dup = StubLLM('{"action": "speak", "text": "你饿不饿", "location": ""}')
    check("重复的台词被拦截", autonomy.maybe_act(db, conv11, dup, 600, "x", idle=False) is None)

    # 12) act 也计入连续自主次数
    conv12 = fresh()
    for i in range(autonomy.AUTONOMY_MAX_CONSECUTIVE + 1):
        db.push_autonomy(conv12, {"action": "act", "text": f"第{i}件事", "at": time.time()})
    llm12 = StubLLM('{"action": "act", "text": "再擦一次", "location": ""}')
    check("act 计入连续上限", autonomy.maybe_act(db, conv12, llm12, 600, "x", idle=False) is None)
    check("超限后不调用模型", llm12.calls == 0)

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
