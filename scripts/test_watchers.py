"""
v5 探针：场景观察者 + 神态描写（不耗 API，用桩模型）
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "watch_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app                          # noqa: E402
import core.locations as locations            # noqa: E402
import services.character_writer as cw        # noqa: E402
import services.scene_watcher as sw           # noqa: E402

db = database.ChatDB()
FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class StubLLM:
    """按 system_prompt 内容区分调用方"""
    def __init__(self, scene_json='{"new_location": "学校天台", "entered_by": "both", "reason": "对话里提到两人一起上了天台"}',
                 desc="她抬眼看了你一下，耳朵动了动，尾巴悄悄卷住自己的手腕。"):
        self.scene_json = scene_json
        self.desc = desc
        self.calls = []

    def chat_sync(self, messages, system_prompt="", **kw):
        self.calls.append(system_prompt[:20])
        if "地点表" in (system_prompt or ""):
            return self.scene_json
        return self.desc


def main():
    app.config["TESTING"] = True
    conv = db.create_conversation(1)
    locations.ensure_defaults(db, conv)
    db.add_message(conv, "user", "我们去学校天台吹吹风吧", channel="chat")
    db.add_message(conv, "assistant", '{"对白": "……好。"}', channel="chat")

    known_before = set(locations.list_names(db, conv))
    check("场景不在已有列表里", "学校天台" not in known_before)

    found = sw.observe(db, conv, StubLLM(), 600, "现在是傍晚", api_key=None)
    check("观察者报告新场景", bool(found) and found["location"] == "学校天台", str(found))
    check("地点已创建", "学校天台" in locations.list_names(db, conv))
    check("标记为新建", found and found["created"], str(found))
    check("双方位置已更新",
          db.get_current_location(conv) == "学校天台" and db.get_player_location(conv) == "学校天台",
          f"{db.get_current_location(conv)} / {db.get_player_location(conv)}")
    events = db.get_events(conv, limit=10)
    check("写入 scene_change 事件",
          any(e["event_type"] == "scene_change" and "学校天台" in e["title"] for e in events))

    # 重复提及同一地点不应重复创建
    again = sw.observe(db, conv, StubLLM(), 610, "现在是傍晚", api_key=None)
    check("重复提及不报新建", bool(again) and again["created"] is False, str(again))

    # 没有新场景时保持沉默
    none_llm = StubLLM(scene_json='{"new_location": "", "entered_by": "none", "reason": "无"}')
    check("没有新场景时返回 None", sw.observe(db, conv, none_llm, 620, "", None) is None)

    # 动作描写：只写尤夏，且默认不写玩家
    class CaptureLLM(StubLLM):
        def __init__(self):
            super().__init__(desc="尤夏把杯子往自己那边挪了挪，指尖在杯沿上停了一下。")
            self.last_user = ""
            self.last_system = ""

        def chat_sync(self, messages, system_prompt="", **kw):
            self.last_user = messages[0]["content"] if messages else ""
            self.last_system = system_prompt or ""
            return super().chat_sync(messages, system_prompt, **kw)

    cap = CaptureLLM()
    text = cw.describe(db, conv, cap, 630, "现在是傍晚", api_key=None)
    check("生成动作描写", bool(text) and len(text) >= 6, text)
    check("提示词只让写尤夏", "只写尤夏" in cap.last_system, cap.last_system[:80])
    check("提示词默认禁止写玩家", "不要写青梅" in cap.last_system, cap.last_system[:80])
    check("玩家没做动作时，明确说一个字都不要写青梅",
          "一个字都不要写青梅" in cap.last_system, cap.last_system[-260:])
    check("提示词带上双方位置", "青梅在" in cap.last_user, cap.last_user[:160])
    check("提示词带上此刻时间", "此刻" in cap.last_user, cap.last_user[:160])
    states = [m for m in db.get_messages(conv) if m["channel"] == "state"]
    check("动作描写落库到 state 通道", len(states) == 1 and states[0]["content"] == text)
    check("动作描写不混入聊天通道",
          all(m["channel"] == "chat" for m in db.get_messages(conv, channel="chat")))

    # 玩家这一句本身是动作时，才允许顺着写这个动作带出的结果
    db.add_message(conv, "user", "（钻进被窝）", channel="chat")
    cap2 = CaptureLLM()
    cw.describe(db, conv, cap2, 635, "现在是傍晚", api_key=None)
    check("玩家做动作时把动作原文给了旁白",
          "钻进被窝" in cap2.last_system, cap2.last_system[-260:])
    check("有动作也只允许写这一下的结果",
          "顺着写这个动作带出的结果" in cap2.last_system
          and "不要替她加别的动作" in cap2.last_system, cap2.last_system[-260:])

    # 模型不按 JSON 输出时，整段当旁白（不能因为格式丢掉描写）
    plain = CaptureLLM()
    plain.desc = "尤夏把脸埋进抱枕里，只露出耳朵。"
    out = cw.describe(db, conv, plain, 636, "现在是傍晚", api_key=None)
    check("纯文本输出也能用", "抱枕" in out, out)

    # 旁白可以改动道具状态：JSON 输出里的「道具」会写回库里
    here = db.get_current_location(conv)
    db.set_prop(conv, here, "抱枕", "在床头")
    prop_llm = CaptureLLM()
    prop_llm.desc = json.dumps(
        {"旁白": "尤夏把抱枕抓过来按在怀里。",
         "道具": [{"名字": "抱枕", "状态": "被抱在怀里"}]}, ensure_ascii=False)
    cw.describe(db, conv, prop_llm, 637, "现在是傍晚", api_key=None)
    saved = [p for p in db.get_props(conv, here) if p["name"] == "抱枕"]
    check("旁白动过的东西状态被写回", bool(saved) and "怀里" in saved[0]["state"],
          str(saved))

    # 青梅是女生：模型写成「他」也要被纠正
    male_llm = CaptureLLM()
    male_llm.desc = "尤夏抬眼看他，把杯子往他那边推了推，其他人都没注意。"
    fixed = cw.describe(db, conv, male_llm, 640, "现在是傍晚", api_key=None)
    check("指代青梅的「他」被改成「她」", "他" not in fixed.replace("其他", ""), fixed)
    check("正常词（其他）不被误伤", "其他" in fixed, fixed)

    # 描写现在要求 60~130 字，模型偶尔写超：要在句读处截断，不能留半句话
    long_text = "尤夏抬眼。" * 60
    clipped = cw._clean(long_text)
    check("超长描写被截到上限内", len(clipped) <= cw.MAX_DESC_CHARS, str(len(clipped)))
    check("截断停在句号后面", clipped.endswith("。"), clipped[-12:])
    check("短描写原样保留", cw._clip("尤夏抬眼看他。") == "尤夏抬眼看他。",
          cw._clip("尤夏抬眼看他。"))
    check("没有句读时也能截断", len(cw._clip("啊" * 500)) == cw.MAX_DESC_CHARS,
          str(len(cw._clip("啊" * 500))))

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
