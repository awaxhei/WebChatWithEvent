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
import time
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

    # ── 1b) 情境摘要的"轮次"要按全部消息算，否则会一直停在开头
    #      （实测：玩家只发了 3 句，她已经自言自语十几条，摘要却还是第 1 轮的
    #        "她刚叫了你的名字"，于是她真去回那句根本没发生的话）
    conv_b = app_db.create_conversation(user)
    app_db.add_message(conv_b, "user", "早上好尤夏", channel="chat")
    for i in range(6):
        app_db.add_message(conv_b, "assistant", f"她的第{i}句", channel="chat")
        app_db.add_message(conv_b, "assistant", f"旁白{i}", channel="state")
    check("全部消息数按所有角色统计", app_db.get_total_message_count(conv_b) == 13,
          str(app_db.get_total_message_count(conv_b)))
    check("玩家消息数只数 user", app_db.get_message_count(conv_b) == 1,
          str(app_db.get_message_count(conv_b)))
    check("两者不同（摘要必须用前者）",
          app_db.get_total_message_count(conv_b) > app_db.get_message_count(conv_b) + 3,
          f"{app_db.get_total_message_count(conv_b)} vs {app_db.get_message_count(conv_b)}")

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

    # ── 7b) 她自己动手做下的事要进对话时间线（两个模型读同一份历史）
    app_db.push_autonomy(conv6, {"action": "act", "text": "把灶台擦了一遍",
                                 "at": 1.0, "at_minutes": 426})
    autonomy._do_act(app_db, conv6, "把灶台擦了一遍", "公寓客厅", 426, True)
    acts = [m for m in app_db.get_messages(conv6) if m["channel"] == "action"]
    check("在场的自主动作写进时间线", len(acts) == 1 and "灶台" in acts[0]["content"],
          str([(m["channel"], m["content"]) for m in acts]))
    autonomy._do_act(app_db, conv6, "在远处翻书", "图书馆", 428, False)
    acts2 = [m for m in app_db.get_messages(conv6) if m["channel"] == "action"]
    check("看不见的动作不进时间线", len(acts2) == 1, str([m["content"] for m in acts2]))

    # 自主行动那一侧也读同一份时间线，不然她又会挑一个别的道具
    auto_prompt = autonomy._build_prompt(app_db, conv6, 430, "", "公寓客厅", True, False, 0)
    check("自主行动能看到自己刚做的动作",
          "（动作）" in auto_prompt and "把灶台擦了一遍" in auto_prompt, auto_prompt[-300:])

    # ── 8) 动作以她的输出格式回灌进对话历史（不是靠提示词再念一遍）
    llm2 = CaptureLLM()
    chat_mod.deepseek = llm2
    try:
        r = c.post("/api/chat", json={"conversation_id": conv6, "message": "你刚才在忙什么"})
        body = r.data.decode("utf-8")   # 必须消费流，生成器才会真正跑起来
        check("聊天接口正常返回（第二人称场景）", r.status_code == 200 and '"type": "done"' in body,
              str(r.status_code))
        sent = json.dumps(llm2.messages, ensure_ascii=False)
        check("历史里带着她自己刚才的动作", "把灶台擦了一遍" in sent, sent[-400:])
        acts_sent = [m for m in llm2.messages
                     if isinstance(m.get("content"), str) and '"动作"' in m["content"]]
        check("动作按她的输出格式回灌", bool(acts_sent), sent[-400:])
        check("提示词里不再单独念一遍动作",
              "你刚才做的动作" not in llm2.system_prompt, llm2.system_prompt[-200:])
    finally:
        chat_mod.deepseek = real

    # ── 9) 玩家回复速度会被记录下来（等待时长靠它自适应）
    convL = app_db.create_conversation(user)
    app_db.add_message(convL, "user", "先说一句", channel="chat")
    app_db.set_last_user_at(convL, time.time())
    app_db.set_last_yuxia_at(convL, time.time() - 30, "chat")
    llm3 = CaptureLLM()
    chat_mod.deepseek = llm3
    try:
        r = c.post("/api/chat", json={"conversation_id": convL, "message": "回一句"})
        r.data.decode("utf-8")
        lat = app_db.get_reply_latency(convL)
        check("记录玩家回复间隔", 25 <= lat <= 40, str(lat))
    finally:
        chat_mod.deepseek = real

    # ── 10) 拨时间之后，尤夏能从世界块里看到天色变了
    convT = app_db.create_conversation(user)
    app_db.add_message(convT, "user", "先坐会儿", channel="chat")
    r = c.post(f"/api/time/{convT}", json={"hour": 22, "minute": 15})
    t = r.get_json() or {}
    check("拨时间接口可用", r.status_code == 200 and t.get("story_minutes"), str(r.status_code))
    block_t = world_context.world_block(app_db, convT, t.get("story_minutes"))
    check("世界块里能看到时间被拨动", "时间被拨到了" in block_t, block_t[:200])
    llmT = CaptureLLM()
    chat_mod.deepseek = llmT
    try:
        r = c.post("/api/chat", json={"conversation_id": convT, "message": "天怎么黑这么快"})
        r.data.decode("utf-8")
        check("尤夏的提示词里有这次时间变化", "时间被拨到了" in llmT.system_prompt,
              llmT.system_prompt[:300])
        check("提示词里的此刻时间已更新", "22:15" in llmT.system_prompt, llmT.system_prompt[:300])
    finally:
        chat_mod.deepseek = real

    # ── 11) 玩家在界面上换场景：一个字都没说，她也得知道人走了
    convS = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, convS)
    # 开局是各在各的房间（异地开场），这里先摆到同一个房间再测"换场景"
    locations.switch_both(app_db, convS, "公寓客厅")
    app_db.add_message(convS, "user", "先在这儿坐会儿", channel="chat")
    check("先摆到同一个房间（后面测换场景用）",
          app_db.get_current_location(convS) == app_db.get_player_location(convS) == "公寓客厅",
          f"{app_db.get_current_location(convS)}/{app_db.get_player_location(convS)}")

    # 换成桩：换场景会顺手预热环境描写/情境叙述，别真去打 API
    llmS = CaptureLLM()
    chat_mod.deepseek = llmS
    try:
        r = c.post(f"/api/move/{convS}", json={"who": "player", "location": "厨房"})
        moved_resp = r.get_json() or {}
        check("移动接口可用", r.status_code == 200 and moved_resp.get("player_location") == "厨房")
        check("回传原来的地点（前端好写转场旁白）",
              moved_resp.get("from_player_location") == "公寓客厅",
              str(moved_resp.get("from_player_location")))

        evs = app_db.get_recent_events(convS, limit=10)
        check("换场景会记一条事件",
              any(e["event_type"] == "scene_change" and "青梅去了厨房" in e["title"] for e in evs),
              str([e["title"] for e in evs]))
        check("事件里写出她是从哪儿走开的",
              any("公寓客厅" in (e.get("description") or "")
                  and "厨房" in (e.get("description") or "") for e in evs),
              str([e.get("description") for e in evs]))

        block = world_context.place_block(app_db, convS, app_db.get_current_location(convS),
                                          app_db.get_player_location(convS))
        check("位置块写清了她自己在哪", "【你此刻在哪】公寓客厅" in block, block)
        check("位置块写清了青梅在哪", "青梅此刻在哪" in block and "厨房" in block, block)
        check("位置块提示只能手机联系", "手机" in block, block)

        check("换场景后缓存的情境叙述作废", app_db.get_context_brief(convS)[0] == "",
              str(app_db.get_context_brief(convS)))

        r = c.post("/api/chat", json={"conversation_id": convS, "message": "你去哪了"})
        r.data.decode("utf-8")      # 必须消费流，生成器才会真正跑起来
        check("聊天提示词里有她自己的位置", "【你此刻在哪】公寓客厅" in llmS.system_prompt,
              llmS.system_prompt[:400])
        check("聊天提示词里有青梅的新位置",
              "【青梅此刻在哪】厨房" in llmS.system_prompt, llmS.system_prompt[:400])
        check("聊天提示词里带着这次转场事件",
              "青梅去了厨房" in llmS.system_prompt, llmS.system_prompt[:400])

        # 一起走：先回到一起，再两个人一起换地方，留一条"两人一起"的事件
        r = c.post(f"/api/move/{convS}", json={"who": "player", "location": "公寓客厅"})
        check("走回去之后又在一起了", (r.get_json() or {}).get("co_located") is True,
              str((r.get_json() or {}).get("co_located")))
        r = c.post(f"/api/move/{convS}", json={"who": "both", "location": "卧室"})
        both_resp = r.get_json() or {}
        check("两人可以一起换地方",
              r.status_code == 200 and both_resp.get("location") == "卧室"
              and both_resp.get("player_location") == "卧室",
              str(both_resp.get("location")))
        evs = app_db.get_recent_events(convS, limit=10)
        check("一起走只记一条两人事件",
              any("两人一起去了卧室" in (e.get("title") or "") for e in evs),
              str([e.get("title") for e in evs]))

        # 原地"移动"不该刷出一堆重复事件
        before_events = len(app_db.get_recent_events(convS, limit=50))
        c.post(f"/api/move/{convS}", json={"who": "player", "location": "卧室"})
        after_events = len(app_db.get_recent_events(convS, limit=50))
        check("没换地方就不记事件", before_events == after_events,
              f"{before_events} -> {after_events}")

        # 她自己的移动不能当成"世界发生的事"回灌给她
        c.post(f"/api/move/{convS}", json={"who": "yuxia", "location": "阳台"})
        evs = app_db.get_recent_events(convS, limit=10)
        own = [e for e in evs if "尤夏去了阳台" in (e.get("title") or "")]
        check("她的移动事件标了 self",
              bool(own) and own[-1].get("tier") == world_context.SELF_TIER,
              str([(e.get("title"), e.get("tier")) for e in evs]))
        visible = world_context.recent_events_block(app_db, convS, limit=8)
        check("她的移动不出现在「最近发生的事」里", "尤夏去了阳台" not in visible, visible[:200])
    finally:
        chat_mod.deepseek = real

    # ── 12) 分处两地时，两边的场景描写必须各自独立（不能拿另一边的顶替）
    convX = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, convX)
    # 开局是各在各的房间，这里把尤夏摆到客厅、玩家挪到厨房来对比
    locations.switch(app_db, convX, "公寓客厅")
    llmX = CaptureLLM()          # chat_sync 返回失败 → 环境描写走 fallback，按地点生成
    chat_mod.deepseek = llmX
    try:
        c.post(f"/api/move/{convX}", json={"who": "player", "location": "厨房"})
        scene = {}
        for _ in range(6):
            scene = c.get(f"/api/scene/{convX}").get_json() or {}
            if not scene.get("pending"):
                break
            time.sleep(1.2)
        check("分处两地时场景接口标明不同地点",
              scene.get("co_located") is False
              and scene.get("location") != scene.get("player_location"),
              f"{scene.get('location')} / {scene.get('player_location')}")
        check("她那边和玩家那边的描写各自独立",
              bool(scene.get("yuxia_description")) and bool(scene.get("player_description"))
              and scene.get("yuxia_description") != scene.get("player_description"),
              f"{str(scene.get('yuxia_description'))[:30]} | {str(scene.get('player_description'))[:30]}")
        check("玩家那边的描写是他所在地点的",
              "厨房" in (scene.get("player_description") or ""), str(scene.get("player_description")))
        check("她那边的描写是她所在地点的",
              "公寓客厅" in (scene.get("yuxia_description") or ""), str(scene.get("yuxia_description")))
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
