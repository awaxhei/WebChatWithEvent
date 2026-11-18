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

    # 9) 动作不能连着来：中间必须先有话（交互感）
    again = StubLLM('{"action": "act", "text": "把湿掉的抹布拿去阳台晾着", "location": ""}')
    check("act 之后紧接 act 被拦下",
          autonomy.maybe_act(db, conv7, again, 610, "情境", idle=False) is None)
    after = StubLLM('{"action": "speak", "text": "……要帮忙吗。", "location": ""}')
    db.set_last_user_at(conv7, time.time() - autonomy.AUTONOMY_MIN_INTERVAL_SECONDS - 5)
    db.set_last_yuxia_at(conv7, time.time() - autonomy.AUTONOMY_MIN_INTERVAL_SECONDS - 2, "act")
    acted8 = autonomy.maybe_act(db, conv7, after, 612, "情境", idle=True)
    check("动作之后可以补一句话", bool(acted8) and acted8["action"] == "speak", str(acted8))

    # 10) 做了一件事、又说了一句话之后，就等玩家开口（不再自顾自）
    more = StubLLM('{"action": "act", "text": "把阳台的窗帘拉上", "location": ""}')
    check("act+说话之后不再自己动",
          autonomy.maybe_act(db, conv7, more, 614, "情境", idle=True) is None)
    check("等玩家时不再调用模型", more.calls == 0, f"calls={more.calls}")

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

    # 13) act 会占用故事时间（否则多次行动全挤在同一分钟）
    conv13 = fresh()
    db.set_story_minutes(conv13, 600)
    acted13 = autonomy.maybe_act(
        db, conv13, StubLLM('{"action": "act", "text": "把窗帘拉开一条缝", "location": ""}'),
        600, "x", idle=False)
    check("act 已执行", bool(acted13) and acted13["action"] == "act", str(acted13))
    check("act 推进了故事时间",
          db.get_story_minutes(conv13) == 600 + autonomy.AUTONOMY_ACT_MINUTES,
          str(db.get_story_minutes(conv13)))
    check("act 事件标记为 self（不能当世界事件喂回去）",
          any(e.get("tier") == "self" for e in db.get_recent_events(conv13, limit=5)))

    # 14) 同一时间窗内动手次数有预算（默认 12 分钟内只允许一次）
    conv14 = fresh()
    db.set_story_minutes(conv14, 600)
    for i in range(autonomy.AUTONOMY_ACT_PER_WINDOW):
        db.push_autonomy(conv14, {"action": "act", "text": f"第{i}件事",
                                  "at": time.time(), "at_minutes": 600 + i})
    llm14 = StubLLM('{"action": "act", "text": "又把灶台擦了一遍", "location": ""}')
    check("同窗动手次数用完后不再 act",
          autonomy.maybe_act(db, conv14, llm14, 605, "x", idle=False) is None)
    check("预算用完后仍然问了模型（判断在服务端）", llm14.calls == 1, f"calls={llm14.calls}")
    check("预算用完时提示词里会说明",
          "已经动过手了" in llm14.last_user_msg, llm14.last_user_msg[-120:])

    # 15) 时间窗过了、中间也说过话，就又能动手
    conv15 = fresh()
    db.set_story_minutes(conv15, 620)
    long_ago = time.time() - autonomy.AUTONOMY_AFTER_ACT_GRACE_SECONDS - 10
    db.set_last_user_at(conv15, long_ago - 10)
    db.push_autonomy(conv15, {"action": "act", "text": "叠好沙发上的毯子",
                              "at": long_ago, "at_minutes": 600})
    db.push_autonomy(conv15, {"action": "speak", "text": "……毯子叠好了。",
                              "at": long_ago, "at_minutes": 604})
    stale = StubLLM('{"action": "act", "text": "把买回来的菜放进冰箱", "location": ""}')
    acted15 = autonomy.maybe_act(db, conv15, stale, 620, "x", idle=True)
    check("过了时间窗可以重新动手", bool(acted15) and acted15["action"] == "act", str(acted15))

    # 16) 刚做完就说话（玩家还在），不再接着动手
    conv16 = fresh()
    db.set_story_minutes(conv16, 600)
    db.set_last_user_at(conv16, time.time())
    now_ts = time.time()
    db.push_autonomy(conv16, {"action": "act", "text": "把窗帘拉开",
                              "at": now_ts - 6, "at_minutes": 600})
    db.push_autonomy(conv16, {"action": "speak", "text": "……外面在下雨。",
                              "at": now_ts - 2, "at_minutes": 601})
    greedy = StubLLM('{"action": "act", "text": "去拿伞", "location": ""}')
    check("动作+说话之后把话筒交回玩家",
          autonomy.maybe_act(db, conv16, greedy, 604, "x", idle=True) is None)
    check("这时也不调用模型", greedy.calls == 0, f"calls={greedy.calls}")

    # 17) 她刚说完话，玩家还没回：先等，不行动、也不调模型
    conv17 = fresh()
    db.set_story_minutes(conv17, 600)
    db.set_last_user_at(conv17, time.time() - 60)
    db.set_last_yuxia_at(conv17, time.time() - 5, "chat")
    eager = StubLLM('{"action": "act", "text": "把杯子收了", "location": ""}')
    check("刚说完话不会立刻行动",
          autonomy.maybe_act(db, conv17, eager, 601, "x", idle=True) is None)
    check("等待期间不调用模型", eager.calls == 0, f"calls={eager.calls}")

    # 18) 等够了（超过 AUTONOMY_SPEAK_WAIT_SECONDS）才开始动
    db.set_last_yuxia_at(conv17, time.time() - autonomy.AUTONOMY_SPEAK_WAIT_SECONDS - 2, "chat")
    check("等不及了才行动", bool(autonomy.maybe_act(db, conv17, eager, 603, "x", idle=True)))

    # 19) 手机消息要等更久（玩家得先看到红点、打开手机、读完再回）
    conv19 = fresh()
    db.set_story_minutes(conv19, 600)
    db.set_last_user_at(conv19, time.time() - 60)
    db.set_last_yuxia_at(conv19, time.time() - autonomy.AUTONOMY_SPEAK_WAIT_SECONDS - 5, "phone")
    phone_wait = StubLLM('{"action": "act", "text": "把灯关了", "location": ""}')
    check("手机消息的等待更长",
          autonomy.maybe_act(db, conv19, phone_wait, 601, "x", idle=True) is None)
    db.set_last_yuxia_at(conv19, time.time() - autonomy.AUTONOMY_PHONE_WAIT_SECONDS - 2, "phone")
    check("手机消息等够了才行动",
          bool(autonomy.maybe_act(db, conv19, phone_wait, 603, "x", idle=True)))

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
