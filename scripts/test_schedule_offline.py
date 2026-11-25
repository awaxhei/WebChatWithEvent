"""日程 + 离线结算 的探针（桩模型，零额度）

覆盖：
1. 日程表：几点在上课、几点吃饭、几点睡；上课中的教室玩家进不去
2. 离线结算：按日程段生成「这段时间发生了什么」+ 她主动发的消息
3. 回来后先结算再回话；消息落库到手机通道、带故事时间
4. 后台 idle 也会结算（手机不在前台时她照样发消息）
5. 没到阈值的短暂离开不结算
"""
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database     # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "sched_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db      # noqa: E402
from core.auth import generate_token   # noqa: E402
import core.locations as locations     # noqa: E402
import routes.chat_routes as chat_mod  # noqa: E402
import services.offline as offline     # noqa: E402
import services.schedule as schedule   # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class SettleLLM:
    """回答离线结算请求（chat_sync）与对话（chat_stream）"""

    def __init__(self):
        self.settle_calls = 0
        self.stream_calls = 0
        self.last_prompt = ""

    def chat_sync(self, messages, system_prompt="", **kw):
        if "不在的时候" in (system_prompt or ""):
            self.settle_calls += 1
            self.last_prompt = messages[-1]["content"] if messages else ""
            return json.dumps({
                "events": [{"title": "食堂有糖醋排骨", "description": "她一个人吃了午饭，顺手拍了照片。"}],
                "messages": [
                    {"text": "食堂今天有糖醋排骨", "minutes": 11 * 60 + 50},
                    {"text": "中午你吃什么呀", "minutes": 11 * 60 + 55},
                ],
                "mood": "放松",
            }, ensure_ascii=False)
        return "{}"

    def chat_stream(self, messages, system_prompt="", **kw):
        self.stream_calls += 1
        yield '{"对白": "回来啦。"}'


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user["id"]))
    uid = user["id"]

    try:
        # ── 1) 日程表
        # 先让模型排一天，之后查询都走这份动态日程
        class SchedLLM:
            def __init__(self):
                self.calls = 0
                self.last_kw = {}

            def chat_sync(self, messages, system_prompt="", **kw):
                self.calls += 1
                self.last_kw = kw
                if "作息表" in (system_prompt or ""):
                    return json.dumps({
                        "yuxia": [
                            {"start": 0, "end": 450, "activity": "睡着",
                             "location": "尤夏的房间", "reply": "asleep",
                             "enterable": False, "tag": "sleep"},
                            {"start": 450, "end": 540, "activity": "赶早八",
                             "location": "大学教室", "reply": "busy",
                             "enterable": False, "tag": "class"},
                            {"start": 540, "end": 720, "activity": "上课摸鱼",
                             "location": "大学教室", "reply": "busy",
                             "enterable": False, "tag": "class"},
                            {"start": 720, "end": 780, "activity": "食堂吃午饭",
                             "location": "食堂", "reply": "free",
                             "enterable": True, "tag": "meal"},
                            {"start": 780, "end": 1440, "activity": "没课，窝在公寓",
                             "location": "公寓客厅", "reply": "free",
                             "enterable": True, "tag": "free"},
                        ],
                        "qingmei": [
                            {"start": 0, "end": 460, "activity": "睡觉",
                             "location": "青梅的房间", "reply": "asleep",
                             "enterable": False, "tag": "sleep"},
                            {"start": 460, "end": 1440, "activity": "在公寓",
                             "location": "公寓客厅", "reply": "free",
                             "enterable": True, "tag": "free"},
                        ],
                    }, ensure_ascii=False)
                return "{}"

        conv0 = app_db.create_conversation(uid)
        locations.ensure_defaults(app_db, conv0)
        sched_llm = SchedLLM()
        data = schedule._generate(app_db, conv0, 0, sched_llm)
        check("模型能排出一天的日程", bool(data) and len(data["yuxia"]) == 5,
              str(data)[:120])
        check("排日程的 token 预算够用（900 会被截断成半个 JSON）",
              (sched_llm.last_kw or {}).get("max_tokens", 0) >= 2000,
              str(sched_llm.last_kw))
        # 模型偶尔会包一层代码块：解析要能容忍
        check("能解析代码块包着的日程",
              schedule._parse('```json\n{"yuxia": [{"start": 0, "end": 10}]}\n```')
              is not None)
        check("解析不了时返回 None", schedule._parse("随便说说") is None)
        # 短调用默认不开思考：否则思维链会把 token 吃光、内容为空
        import inspect
        from core.deepseek_client import DeepSeekClient
        check("chat_sync 默认关闭思考模式",
              inspect.signature(DeepSeekClient.chat_sync).parameters["thinking"].default is False)
        again = schedule.ensure_day(app_db, conv0, 9 * 60)
        check("当天日程会被缓存复用",
              len(again["yuxia"]) == 5 and again["yuxia"][1]["activity"] == "赶早八",
              str(again["yuxia"][1]))

        b9 = schedule.block_at(app_db, conv0, 9 * 60)
        check("早上九点在上课", b9["tag"] == "class" and "课" in b9["activity"], str(b9))
        check("上课时回消息是忙（可摸鱼）", b9["reply"] == "busy", str(b9["reply"]))
        b12 = schedule.block_at(app_db, conv0, 12 * 60 + 30)
        check("中午十二点半在吃饭", b12["tag"] == "meal", str(b12))
        # 动态日程里这一天的半夜是「窝在公寓」；兜底模板则是睡
        night = [b for b in schedule._fallback("yuxia")
                 if b["start"] <= 23 * 60 + 30 < b["end"]][0]
        check("兜底模板半夜在睡", night["reply"] == "asleep", str(night))
        check("动态日程半夜按模型排的来",
              schedule.block_at(app_db, conv0, 23 * 60 + 30)["activity"] == "没课，窝在公寓",
              str(schedule.block_at(app_db, conv0, 23 * 60 + 30)))
        ok, why = schedule.player_may_enter(app_db, conv0, "大学教室", 9 * 60)
        check("上课中的教室玩家进不去", not ok and "课" in why, why)
        ok2, _ = schedule.player_may_enter(app_db, conv0, "大学教室", 21 * 60)
        check("下课之后教室可以进", ok2)
        blocks = schedule.blocks_between(app_db, conv0, 7 * 60, 13 * 60)
        acts = " ".join(b["activity"] for b in blocks)
        check("跨度里能挑出已结束的日程段", len(blocks) >= 2, str(len(blocks)))
        check("还没结束的段不结算",
              all(b["end"] <= 13 * 60 for b in blocks), str([b["end"] for b in blocks]))

        # ── 2) 离开 2 小时 → 结算出事件与她发的消息
        conv = app_db.create_conversation(uid)
        locations.ensure_defaults(app_db, conv)
        app_db.set_story_minutes(conv, 12 * 60)
        app_db.touch_last_seen(conv, time.time() - 2 * 3600)
        llm = SettleLLM()
        written = offline.settle(app_db, conv, llm, 12 * 60)
        check("结算调了一次模型", llm.settle_calls == 1, str(llm.settle_calls))
        check("结算写进了她的消息", written >= 1, str(written))
        rows = [m for m in app_db.get_messages(conv) if m["channel"] == "phone"]
        check("消息落在手机通道", len(rows) == 2, str([m["content"] for m in rows]))
        check("消息带故事时间", all(int(m.get("story_minutes") or 0) > 0 for m in rows),
              str([m.get("story_minutes") for m in rows]))
        check("结算提示词里有她的日程",
              "在上上午的课" in llm.last_prompt or "在食堂吃午饭" in llm.last_prompt,
              llm.last_prompt[:300])
        check("结算提示词里有离开多久", "你离开了" in llm.last_prompt, llm.last_prompt[:120])
        all_events = app_db.get_recent_events(conv, limit=10)
        events = [e for e in all_events if (e.get("event_type") or e.get("type")) == "misc"
                  and (e.get("tier") or "") == "self"]
        check("结算记了事件", bool(events) and "糖醋排骨" in (events[-1].get("title") or ""),
              str([(e.get("event_type"), e.get("title")) for e in all_events]))
        check("心情被带上", app_db.get_yuxia_mood(conv) == "放松",
              app_db.get_yuxia_mood(conv))
        check("未读数涨了", app_db.get_phone_unread(conv) >= 1,
              str(app_db.get_phone_unread(conv)))

        # ── 3) 短暂离开不结算
        conv2 = app_db.create_conversation(uid)
        locations.ensure_defaults(app_db, conv2)
        app_db.set_story_minutes(conv2, 12 * 60)
        app_db.touch_last_seen(conv2, time.time() - 5 * 60)   # 才 5 分钟
        llm2 = SettleLLM()
        check("离开太短不结算", offline.settle(app_db, conv2, llm2, 12 * 60) == 0
              and llm2.settle_calls == 0, str(llm2.settle_calls))

        # ── 4) 回来第一句话之前先结算
        conv3 = app_db.create_conversation(uid)
        locations.ensure_defaults(app_db, conv3)
        locations.switch_both(app_db, conv3, "公寓客厅")
        app_db.set_story_minutes(conv3, 12 * 60)
        app_db.touch_last_seen(conv3, time.time() - 3 * 3600)
        llm3 = SettleLLM()
        real = chat_mod.deepseek
        chat_mod.deepseek = llm3
        try:
            r = c.post("/api/chat", json={"conversation_id": conv3, "message": "我回来了"})
            r.data.decode("utf-8")
            check("回话前先结算", llm3.settle_calls == 1, str(llm3.settle_calls))
            check("上也照常回话", llm3.stream_calls == 1, str(llm3.stream_calls))
            rows3 = [m for m in app_db.get_messages(conv3) if m["channel"] == "phone"]
            check("结算的消息进了同一个会话", len(rows3) == 2,
                  str([m["content"] for m in rows3]))
            # 结算完把"上次出现"推到现在，不会再重复结算
            check("结算后 presence 被推进",
                  time.time() - app_db.get_last_seen_at(conv3) < 60,
                  str(time.time() - app_db.get_last_seen_at(conv3)))
        finally:
            chat_mod.deepseek = real

        # ── 5) 后台 idle 也结算（手机不在前台时她照样发消息）
        conv4 = app_db.create_conversation(uid)
        locations.ensure_defaults(app_db, conv4)
        app_db.set_story_minutes(conv4, 12 * 60)
        app_db.touch_last_seen(conv4, time.time() - 2 * 3600)
        llm4 = SettleLLM()
        chat_mod.deepseek = llm4
        try:
            r = c.post(f"/api/idle/{conv4}")
            r.data.decode("utf-8") if hasattr(r, "data") else r.get_json()
            rows4 = [m for m in app_db.get_messages(conv4) if m["channel"] == "phone"]
            check("后台 idle 结算出她的消息", len(rows4) == 2,
                  str([m["content"] for m in rows4]))
            check("idle 也顺手推进 presence",
                  time.time() - app_db.get_last_seen_at(conv4) < 60,
                  str(time.time() - app_db.get_last_seen_at(conv4)))
        finally:
            chat_mod.deepseek = real

        # ── 6) 上课时玩家进不去教室
        conv5 = app_db.create_conversation(uid)
        locations.ensure_defaults(app_db, conv5)
        app_db.set_story_minutes(conv5, 9 * 60)
        r = c.post(f"/api/move/{conv5}", json={"who": "player", "location": "大学教室"})
        check("上课时移动被拦", r.status_code == 400, str(r.status_code))
        check("拦的原因说得清", "进不去" in (r.get_json() or {}).get("error", ""),
              str(r.get_json()))
        app_db.set_story_minutes(conv5, 21 * 60)
        r = c.post(f"/api/move/{conv5}", json={"who": "player", "location": "大学教室"})
        check("下课后可以进教室", r.status_code == 200, str(r.status_code))
    finally:
        pass

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
