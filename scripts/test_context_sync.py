"""
上下文同步探针（v7）

覆盖三个玩家实测问题：
1. 她读不到随机事件 —— 事件只写进 events 表，聊天提示词里没有
2. 手机和当面各说各的 —— 上下文只取当前通道的历史
3. 事件表取的是"最早的 N 条" —— 事件一多，新事件永远进不了上下文

用法：python scripts/test_context_sync.py
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "ctx_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db               # noqa: E402
from core.auth import generate_token            # noqa: E402
import routes.chat_routes as chat_mod           # noqa: E402
import services.world_context as world_context  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class CaptureLLM:
    """记录送进模型的内容，用于断言提示词里到底有什么"""

    def __init__(self, reply='{"对白": "「……嗯。」"}'):
        self.reply = reply
        self.system_prompt = ""
        self.messages = []

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        self.system_prompt = system_prompt
        self.messages = list(messages)
        yield self.reply

    def chat_sync(self, *args, **kwargs):
        return "[生成失败: 测试桩]"


class SyncStub:
    """只回答 chat_sync（自主行动走这条路）"""

    def __init__(self, payload):
        self.payload = payload

    def chat_sync(self, *args, **kwargs):
        return self.payload


def client_for(user_id):
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user_id))
    return c


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = client_for(user)

    # ── 1) 事件表：必须能取到"最近"的，而不是最早的
    conv = app_db.create_conversation(user)
    for i in range(30):
        app_db.add_event(conv, "misc", f"旧事件{i}", "", story_time="第一天 07:00")
    app_db.add_event(conv, "random", "水槽水管爆开", "旧水管爆开，水漫了一地", "active",
                     story_time="第一天 07:12", tier="high")
    recent = app_db.get_recent_events(conv, limit=5)
    check("get_recent_events 取最新 5 条", [e["title"] for e in recent][-1] == "水槽水管爆开",
          str([e["title"] for e in recent]))
    check("get_recent_events 按时间正序", [e["id"] for e in recent] == sorted(e["id"] for e in recent))
    check("旧接口 get_events 仍返回最早 N 条",
          app_db.get_events(conv, limit=5)[0]["title"] == "旧事件0")

    # ── 2) 两条通道合并进上下文
    conv2 = app_db.create_conversation(user)
    app_db.add_message(conv2, "user", "当面说的话", channel="chat")
    app_db.add_message(conv2, "assistant", json.dumps({"对白": "当面回的话"}, ensure_ascii=False),
                       channel="chat")
    app_db.add_message(conv2, "user", "手机里说的话", channel="phone")
    app_db.add_message(conv2, "assistant", "手机里回的话", channel="phone")

    window = chat_mod._context_window(conv2, limit=10)
    joined = json.dumps(window, ensure_ascii=False)
    check("上下文包含当面消息", "当面说的话" in joined and "当面回的话" in joined)
    check("上下文包含手机消息", "手机里说的话" in joined and "手机里回的话" in joined)
    check("顺序按时间（当面在前）", window[0]["content"] == "当面说的话", str(window[0]))
    phone_reply = next((m for m in window if "手机里回的话" in m["content"]), None)
    check("手机通道的她的话包成 {\"消息\"}",
          phone_reply is not None and "消息" in phone_reply["content"], str(phone_reply))
    chat_reply = next((m for m in window if "当面回的话" in m["content"]), None)
    check("当面通道保留原始 JSON（强化格式）",
          chat_reply is not None and "对白" in chat_reply["content"], str(chat_reply))

    # ── 3) 聊天提示词里带上随机事件（她读得到）
    conv3 = app_db.create_conversation(user)
    app_db.add_message(conv3, "user", "刚进门", channel="chat")
    app_db.add_event(conv3, "random", "水槽旧水管爆开", "水柱喷得到处都是", "active",
                     story_time="第一天 07:12", tier="high")

    llm = CaptureLLM()
    real = chat_mod.deepseek
    chat_mod.deepseek = llm
    try:
        r = c.post("/api/chat", json={"conversation_id": conv3, "message": "怎么回事"})
        body = r.data.decode("utf-8")
        check("聊天接口正常返回", r.status_code == 200 and '"type": "done"' in body, str(r.status_code))
        check("聊天提示词包含最近的随机事件", "水槽旧水管爆开" in llm.system_prompt,
              llm.system_prompt[:300])
        check("聊天提示词标记了突发类型", "（突发）" in llm.system_prompt, llm.system_prompt[:300])
        sent = json.dumps(llm.messages, ensure_ascii=False)
        check("发给模型的历史包含上一条消息", "刚进门" in sent, sent[:200])
        check("发给模型的历史包含本次输入", "怎么回事" in sent)
    finally:
        chat_mod.deepseek = real

    # ── 4) 世界块：活动 + 最近事件都进得去
    app_db.set_yuxia_activity(conv3, "把地上的水擦掉", 432)
    block = world_context.world_block(app_db, conv3, 440)
    check("世界块含最近事件", "水槽旧水管爆开" in block, block[:200])
    check("世界块含她此刻在做的事", "把地上的水擦掉" in block, block[:200])
    expired = world_context.world_block(app_db, conv3, 432 + 400)
    check("跨时段后活动自然过期", "把地上的水擦掉" not in expired, expired[:200])

    # ── 5) 异地时 act 不可见（只在故事进展留痕）
    import core.locations as locations
    import services.autonomy as autonomy

    conv4 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv4)
    app_db.set_player_location(conv4, "厨房")
    locations.switch(app_db, conv4, "公寓客厅")
    app_db.add_message(conv4, "user", "我在这边", channel="chat")
    acted = autonomy.maybe_act(app_db, conv4,
                               SyncStub('{"action": "act", "text": "把书翻到夹书签那页", "location": ""}'),
                               600, "情境", idle=False)
    check("异地 act 生成成功", bool(acted) and acted["action"] == "act", str(acted))
    check("异地 act 对玩家不可见", acted is not None and acted.get("visible") is False)

    # ── 6) 暂停自动推进：她不自己动、世界引擎停转，但对话照常
    conv5 = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv5)
    app_db.add_message(conv5, "user", "先说句话", channel="chat")
    app_db.set_last_user_at(conv5, 0)

    r = c.post(f"/api/pause/{conv5}", json={"paused": True})
    check("暂停接口可用", r.status_code == 200 and (r.get_json() or {}).get("paused") is True)
    check("暂停状态落库", app_db.is_paused(conv5) is True)

    scene = c.get(f"/api/scene/{conv5}").get_json() or {}
    check("场景接口回传暂停状态（刷新后能恢复按钮）", scene.get("paused") is True, str(scene.get("paused")))

    class CountingStub:
        def __init__(self):
            self.calls = 0

        def chat_sync(self, *a, **kw):
            self.calls += 1
            return '{"action": "act", "text": "偷偷擦桌子", "location": ""}'

    stub = CountingStub()
    check("暂停后不产生自主行动",
          autonomy.maybe_act(app_db, conv5, stub, 600, "情境", idle=False) is None)
    check("暂停后连模型都不调用（省额度）", stub.calls == 0, f"calls={stub.calls}")

    idle = c.post(f"/api/idle/{conv5}").get_json() or {}
    check("暂停时闲置接口直接返回", idle.get("acted") is False and idle.get("paused") is True, str(idle))

    atm = c.post(f"/api/atmosphere/{conv5}", json={"msg_count": 5, "is_push_cmd": True})
    body = atm.data.decode("utf-8")
    check("暂停时世界引擎不运行（直接 done）",
          '"type": "start"' in body and '"paused": true' in body, body[:120])

    r = c.post(f"/api/pause/{conv5}", json={"paused": False})
    check("可以恢复", (r.get_json() or {}).get("paused") is False and app_db.is_paused(conv5) is False)

    # ── 7) 她自己的动作不能混进「最近发生的事」，且必须是第二人称
    conv6 = app_db.create_conversation(user)
    app_db.add_event(conv6, "random", "楼上传来一阵拖椅子的声音", "刺啦一声", "active",
                     story_time="第一天 07:05", tier="low")
    app_db.add_event(conv6, "misc", "尤夏从沙发上起身", "尤夏从沙发上起身，赤脚走进厨房。",
                     "completed", story_time="第一天 07:03", tier="self")
    block = world_context.recent_events_block(app_db, conv6, limit=6)
    check("世界事件仍然在", "楼上传来一阵拖椅子的声音" in block, block[:160])
    check("她自己的动作不算世界事件", "从沙发上起身" not in block, block[:160])

    # 事件要标出"距现在多久"，否则刚响的敲门声会被她说成"早上那事"
    aged = world_context.recent_events_block(app_db, conv6, limit=6, now_minutes=307)
    check("刚发生的事件标「刚刚」", "刚刚" in aged, aged[:200])
    aged2 = world_context.recent_events_block(app_db, conv6, limit=6, now_minutes=460)
    check("久一点的事件标分钟数", "分钟前" in aged2, aged2[:200])
    wb = world_context.world_block(app_db, conv6, now_minutes=307)
    check("世界块带上「此刻」", "此刻" in wb, wb[:200])

    app_db.set_yuxia_activity(conv6, "尤夏把碗摞起来", 429)
    line = world_context.activity_line(app_db, conv6, 430)
    check("活动行改成第二人称（去掉「尤夏」主语）",
          "尤夏" not in line and "把碗摞起来" in line, line)

    app_db.push_autonomy(conv6, {"action": "act", "text": "尤夏把灶台擦了一遍",
                                 "at": 1.0, "at_minutes": 426})
    acts_block = world_context.self_actions_block(app_db, conv6, 430)
    check("自己的动作用「你刚才做了」单独给", "你刚才做了" in acts_block and "尤夏" not in acts_block,
          acts_block)

    # ── 8) 对话提示词里带上「你刚才做了」
    llm2 = CaptureLLM()
    chat_mod.deepseek = llm2
    try:
        r = c.post("/api/chat", json={"conversation_id": conv6, "message": "你刚才在忙什么"})
        body = r.data.decode("utf-8")   # 必须消费流，生成器才会真正跑起来
        check("聊天接口正常返回（第二人称场景）", r.status_code == 200 and '"type": "done"' in body,
              str(r.status_code))
        check("提示词含「你刚才做了」", "你刚才做了" in llm2.system_prompt,
              llm2.system_prompt[:400])
        check("提示词里她自己的动作不再是第三人称",
              "尤夏把灶台擦了一遍" not in llm2.system_prompt)
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
