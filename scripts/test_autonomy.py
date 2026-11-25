"""
自主行动探针（v6：由模型判断，不掷骰子；桩模型零额度）
校验：LLM 决定 speak / phone / move、通道约束、连续输出上限、最小间隔
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


def fresh(co_located=True):
    """建一个会话；默认让两人同处一室（这些用例测的是"在一起时"的自主行动）

    开局改成各在各的房间之后，这里显式同处一地，别让用例的前提依赖配置。
    """
    conv = db.create_conversation(1)
    locations.ensure_defaults(db, conv)
    if co_located:
        locations.switch_both(db, conv, "公寓客厅")
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

    # 5b) 计数不能算重：她自主说一句，既落一条消息又记一条流水，只能算一次
    conv5b = fresh()
    llm5b = StubLLM('{"action": "speak", "text": "嗯。", "location": ""}')
    autonomy.maybe_act(db, conv5b, llm5b, 600, "x", idle=False)
    check("自主说一句只算一次输出", autonomy._assistant_streak(db, conv5b) == 1,
          f"streak={autonomy._assistant_streak(db, conv5b)}（消息 + 流水被算了两遍）")

    # 5c) 上限按时间窗算：歇够了能重新开口，不会被永久冻结
    conv5c = fresh()
    for i in range(1 + autonomy.AUTONOMY_MAX_CONSECUTIVE):
        db.add_message(conv5c, "assistant", f"第{i}句", channel="chat")
    check("刚超上限时被挡住",
          autonomy._assistant_streak(db, conv5c) >= 1 + autonomy.AUTONOMY_MAX_CONSECUTIVE,
          f"streak={autonomy._assistant_streak(db, conv5c)}")
    real_time = autonomy.time.time
    try:
        # 把时钟往后拨 11 分钟：假装这些输出都已经超出时间窗
        autonomy.time.time = lambda: real_time() + autonomy.AUTONOMY_STREAK_WINDOW_SECONDS + 60
        check("过了时间窗之后计数归零（可以再开口）",
              autonomy._assistant_streak(db, conv5c) == 0,
              f"streak={autonomy._assistant_streak(db, conv5c)}")
    finally:
        autonomy.time.time = real_time

    # 6) 闲置最小间隔：刚说过话 → 不问
    conv6 = fresh()
    llm6 = StubLLM('{"action": "speak", "text": "嗯", "location": ""}')
    check("刚发言时闲置不触发", autonomy.maybe_act(db, conv6, llm6, 600, "x", idle=True) is None)
    check("时间不足时不调用模型", llm6.calls == 0)

    db.set_last_user_at(conv6, time.time() - autonomy.AUTONOMY_MIN_INTERVAL_SECONDS - 5)
    acted6 = autonomy.maybe_act(db, conv6, llm6, 600, "x", idle=True)
    check("超过最小间隔后由模型判断", bool(acted6) and llm6.calls == 1, str(acted6))

    # 6b) 竞态：进判断时够久，模型返回时玩家刚说过话 → 不要再插一句
    conv6b = fresh()
    db.set_last_user_at(conv6b, time.time() - autonomy.AUTONOMY_MIN_INTERVAL_SECONDS - 5)

    class ChattyLLM(StubLLM):
        """模型调用期间玩家说了话（模拟 /api/idle 与 /api/chat 撞车）"""

        def chat_sync(self, messages, *a, **kw):
            self.calls += 1
            db.set_last_user_at(conv6b, time.time())
            return self.payload

    llm6b = ChattyLLM('{"action": "speak", "text": "你刚才叫尤夏了", "location": ""}')
    check("模型返回时玩家刚说话就不插话",
          autonomy.maybe_act(db, conv6b, llm6b, 600, "x", idle=True) is None,
          "她不该重复回答同一句")

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

    # 13) 跟随真实时间时，动作不再额外占用故事时间（否则会跑到真实时间前面）
    conv13 = fresh()
    db.set_story_minutes(conv13, 600)
    acted13 = autonomy.maybe_act(
        db, conv13, StubLLM('{"action": "act", "text": "把窗帘拉开一条缝", "location": ""}'),
        600, "x", idle=False)
    check("act 已执行", bool(acted13) and acted13["action"] == "act", str(acted13))
    check("动作不再额外加故事时间",
          db.get_story_minutes(conv13) == 600,
          str(db.get_story_minutes(conv13)))
    check("act 事件标记为 self（不能当世界事件喂回去）",
          any(e.get("tier") == "self" for e in db.get_recent_events(conv13, limit=5)))

    # 13b) 日程里她这个点在睡：她不会主动发消息
    # （否则手机上会同时出现「起床了没？」和系统那句「尤夏迷迷糊糊地睁开眼睛」）
    conv_sleep = fresh()
    db.set_day_schedule(conv_sleep, 0, json.dumps({"yuxia": [
        {"start": 0, "end": 1440, "activity": "睡着", "location": "尤夏的房间",
         "reply": "asleep", "enterable": False, "tag": "sleep"},
    ], "qingmei": []}, ensure_ascii=False))
    llm_sleep = StubLLM('{"action": "phone", "text": "起床了没？"}')
    acted_sleep = autonomy.maybe_act(db, conv_sleep, llm_sleep, 600, "早上")
    check("睡着时不主动发消息", acted_sleep is None, str(acted_sleep))
    check("睡着时不调用模型", llm_sleep.calls == 0, str(llm_sleep.calls))

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

    # 18) 等够了（超过第一段等待）才开始动
    db.set_last_yuxia_at(conv17, time.time() - autonomy.AUTONOMY_WAIT_FIRST_SECONDS - 2, "chat")
    check("等不及了才行动", bool(autonomy.maybe_act(db, conv17, eager, 603, "x", idle=True)))

    # 19) 递增式等待：每自主做一件事，下一段就翻一档
    conv19 = fresh()
    db.set_last_user_at(conv19, time.time() - 60)
    waits19 = []
    for i in range(5):
        db.set_last_yuxia_at(conv19, time.time(), "chat")
        waits19.append(autonomy._wait_seconds_needed(db, conv19)[0])
        db.push_autonomy(conv19, {"action": "speak", "text": f"第{i}句",
                                  "at": time.time(), "at_minutes": 600})
    check("第一次等待最短", waits19[0] == autonomy.AUTONOMY_WAIT_FIRST_SECONDS, str(waits19))
    check("等待逐次变长", waits19 == sorted(waits19) and waits19[-1] > waits19[0], str(waits19))
    check("等待有封顶",
          waits19[-1] <= autonomy.AUTONOMY_WAIT_MAX_SECONDS, str(waits19))

    # 19b) 发手机消息的第一段更久（要先看到红点、打开手机）
    conv19b = fresh()
    db.set_last_user_at(conv19b, time.time() - 60)
    db.set_last_yuxia_at(conv19b, time.time(), "chat")
    wait_chat, _ = autonomy._wait_seconds_needed(db, conv19b)
    db.set_last_yuxia_at(conv19b, time.time(), "phone")
    wait_phone, _ = autonomy._wait_seconds_needed(db, conv19b)
    check("手机消息的第一段更长", wait_phone > wait_chat, f"{wait_phone} vs {wait_chat}")

    # 19c) 她刚问了一句 → 多等一会
    conv19c = fresh()
    db.set_last_user_at(conv19c, time.time() - 60)
    db.add_message(conv19c, "assistant", '{"对白": "……你要不要先吃？"}', channel="chat")
    db.set_last_yuxia_at(conv19c, time.time(), "chat")
    wait_q, _ = autonomy._wait_seconds_needed(db, conv19c)
    conv19q = fresh()
    db.set_last_user_at(conv19q, time.time() - 60)
    db.add_message(conv19q, "assistant", '{"对白": "……嗯。"}', channel="chat")
    db.set_last_yuxia_at(conv19q, time.time(), "chat")
    wait_stmt, _ = autonomy._wait_seconds_needed(db, conv19q)
    check("问了问题会等更久", wait_q > wait_stmt, f"{wait_q} vs {wait_stmt}")

    # 19d) 刚动完手还没说话 → 允许马上补一句
    conv19d = fresh()
    db.set_last_user_at(conv19d, time.time() - 60)
    db.set_last_yuxia_at(conv19d, time.time(), "act")
    wait_act, _ = autonomy._wait_seconds_needed(db, conv19d)
    check("动作之后允许马上补一句",
          wait_act == autonomy.AUTONOMY_MIN_INTERVAL_SECONDS, str(wait_act))

    # 20) 她答应了"来了"，就允许在等待期里跟过去（不然只会嘴上答应）
    conv20 = fresh()
    db.set_story_minutes(conv20, 600)
    db.set_player_location(conv20, "厨房")
    locations.switch(db, conv20, "公寓客厅")
    db.set_last_user_at(conv20, time.time())
    db.set_last_yuxia_at(conv20, time.time(), "phone")
    follow = StubLLM('{"action": "move", "text": "", "location": "厨房"}')
    acted20 = autonomy.maybe_act(db, conv20, follow, 600, "x", idle=False)
    check("等待期里允许跟过去（move）",
          bool(acted20) and acted20["action"] == "move" and acted20["location"] == "厨房",
          str(acted20))
    check("跟过去时确实会问模型", follow.calls == 1, f"calls={follow.calls}")
    check("提示词里写明只能 move 或 none",
          "只允许两个选择" in follow.last_user_msg, follow.last_user_msg[-160:])
    check("她真的到了厨房", db.get_current_location(conv20) == "厨房")

    # 21) 等待期里她不能趁机说话
    conv21 = fresh()
    db.set_player_location(conv21, "厨房")
    locations.switch(db, conv21, "公寓客厅")
    db.set_last_user_at(conv21, time.time())
    db.set_last_yuxia_at(conv21, time.time(), "chat")
    chatty = StubLLM('{"action": "speak", "text": "……那你等着。", "location": ""}')
    check("等待期里不能接着说话",
          autonomy.maybe_act(db, conv21, chatty, 600, "x", idle=False) is None)

    # 22) 同处一地时不需要"跟过去"，等待期直接不问模型
    conv22 = fresh()
    db.set_last_user_at(conv22, time.time())
    db.set_last_yuxia_at(conv22, time.time(), "chat")
    same = StubLLM('{"action": "move", "text": "", "location": "厨房"}')
    check("同处一地时等待期不调用模型",
          autonomy.maybe_act(db, conv22, same, 600, "x", idle=False) is None)
    check("确实没调用", same.calls == 0, f"calls={same.calls}")

    # 23) lead：她拉着玩家一起换地方（只有在一起时才行）
    conv23 = fresh()
    locations.switch(db, conv23, "厨房")
    db.set_player_location(conv23, "厨房")
    lead = StubLLM('{"action": "lead", "text": "", "location": "公园"}')
    acted23 = autonomy.maybe_act(db, conv23, lead, 600, "x", idle=False)
    check("lead 带走了两个人",
          bool(acted23) and acted23["action"] == "lead", str(acted23))
    check("尤夏到了公园", db.get_current_location(conv23) == "公园", db.get_current_location(conv23))
    check("玩家也一起到了公园", db.get_player_location(conv23) == "公园",
          db.get_player_location(conv23))
    kinds23 = [e["event_type"] for e in db.get_recent_events(conv23, limit=5)]
    check("记了一条 scene_change 事件", "scene_change" in kinds23, str(kinds23))

    # 24) 异地时不能把人带走
    conv24 = fresh()
    locations.switch(db, conv24, "公寓客厅")
    db.set_player_location(conv24, "厨房")
    acted24 = autonomy.maybe_act(db, conv24,
                                 StubLLM('{"action": "lead", "text": "", "location": "公园"}'),
                                 600, "x", idle=False)
    check("异地时 lead 无效", acted24 is None, str(acted24))
    check("两人地点都没变",
          db.get_current_location(conv24) == "公寓客厅"
          and db.get_player_location(conv24) == "厨房",
          f"{db.get_current_location(conv24)} / {db.get_player_location(conv24)}")

    # 25) /api/idle 的返回里必须写清"她自己换到哪儿了"
    #     手机端就是靠 location 显示「尤夏去了X」的；以前这条字段没人校验，
    #     Android 那边也没写分支，结果她挪了地方屏幕上什么都不显示
    from core.auth import generate_token
    conv25 = fresh()
    client = app.test_client()
    client.set_cookie("auth_token", generate_token(db.get_user("awaxhei")["id"]))
    real_maybe_act = autonomy.maybe_act
    try:
        for action, label in (("move", "尤夏去了厨房"), ("lead", "尤夏拉着你去了厨房")):
            autonomy.maybe_act = lambda *a, _act=action, **kw: {
                "action": _act, "text": "", "location": "厨房",
                "channel": "", "visible": True}
            payload = client.post(f"/api/idle/{conv25}").get_json() or {}
            check(f"idle 的 {action} 带上目标地点",
                  payload.get("action") == action and payload.get("location") == "厨房",
                  str(payload))
            check(f"idle 的 {action} 带上场景（前端好直接换）",
                  isinstance(payload.get("scene"), dict), str(payload.get("scene")))
            # 前端拼出来的提示语（与 App/网页一致）
            text = (f"尤夏去了{payload.get('location')}" if action == "move"
                    else f"尤夏拉着你去了{payload.get('location')}")
            check(f"{action} 能拼出「{label}」", text == label, text)
    finally:
        autonomy.maybe_act = real_maybe_act

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
