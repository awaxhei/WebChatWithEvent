"""异地动作：不再用词表拦，而由她声明的「对谁」判断

覆盖：
1. `_CONTACT_WORDS` / `_action_ok` 这类黑名单已经删掉（防回归）
2. 异地时，她标明 `对谁: 青梅` 的动作不成立，被丢掉
3. 异地时，「靠着窗台」「摸口袋」这类她一个人做得出来的动作照收
4. 同处一地时，碰到青梅的动作当然保留
5. 提示词改成正面约束（动作的宾语只能是她自己/身边的东西）
6. NPC 名字不再用英文口头禅黑名单，改成「至少一个汉字」的形状校验
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "apart_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db            # noqa: E402
from core.auth import generate_token         # noqa: E402
import core.locations as locations           # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402
import services.npc as npc                   # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class StubLLM:
    def __init__(self, lines):
        self.lines = lines
        self.stream_prompt = ""

    def chat_stream(self, messages, system_prompt="", api_key_override=None,
                    temperature=0.7):
        self.stream_prompt = system_prompt
        for line in self.lines:
            yield line

    def chat_sync(self, messages, system_prompt="", **kw):
        return "{}"


def new_conv(user, yuxia, player):
    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)
    app_db.set_current_location(conv, yuxia)
    app_db.set_player_location(conv, player)
    return conv


def actions_of(conv):
    return [m["content"] for m in app_db.get_messages(conv)
            if m.get("channel") == "action"]


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user))
    real_llm = chat_mod.deepseek

    check("动作黑名单已经删掉", not hasattr(chat_mod, "_action_ok"))
    check("接触词表已经删掉", not hasattr(chat_mod, "_CONTACT_WORDS"))

    # ── 1) 异地：标了对象的接触动作不成立，自己做的事照收
    conv = new_conv(user, "公寓玄关", "公寓客厅")
    stub = StubLLM([
        json.dumps({"消息": "嗯，我在玄关。",
                    "动作": "把杯子递给她", "对谁": "青梅"}, ensure_ascii=False),
        json.dumps({"动作": "靠着窗台，把袖口卷起来"}, ensure_ascii=False),
        json.dumps({"动作": "把杯子挪到自己手边", "对谁": "自己"}, ensure_ascii=False),
    ])
    chat_mod.deepseek = stub
    try:
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "在吗"})
        r.data.decode("utf-8")
    finally:
        chat_mod.deepseek = real_llm
    acts = actions_of(conv)
    check("标了青梅的动作不成立", not any("递给她" in a for a in acts), str(acts))
    check("「靠着窗台」不会被词表误杀",
          any("靠着窗台" in a for a in acts), str(acts))
    check("标明对自己的动作保留",
          any("挪到自己手边" in a for a in acts), str(acts))
    check("一轮只收下这两条", len(acts) == 2, str(acts))
    check("提示词是正面约束",
          "动作的宾语只能是她自己" in stub.stream_prompt,
          stub.stream_prompt[-400:])
    check("提示词说明用「对谁」标对象",
          "对谁" in stub.stream_prompt and "青梅" in stub.stream_prompt,
          stub.stream_prompt[-400:])

    # ── 2) 同处一地：碰到对方的动作保留
    conv2 = new_conv(user, "公寓客厅", "公寓客厅")
    stub2 = StubLLM([
        json.dumps({"动作": "把杯子递给她", "对谁": "青梅"}, ensure_ascii=False),
        json.dumps({"动作": "把马克杯挪到窗台",
                    "道具": [{"名字": "马克杯", "状态": "窗台上，杯口朝外"}]},
                   ensure_ascii=False),
    ])
    chat_mod.deepseek = stub2
    try:
        r = c.post("/api/chat", json={"conversation_id": conv2, "message": "给我"})
        r.data.decode("utf-8")
    finally:
        chat_mod.deepseek = real_llm
    check("当面时接触动作保留", any("递给她" in a for a in actions_of(conv2)),
          str(actions_of(conv2)))
    props = {p["name"]: p["state"] for p in app_db.get_props(conv2, "公寓客厅")}
    check("动作能改变实体的状态",
          props.get("马克杯") == "窗台上，杯口朝外", str(props))

    # ── 3) NPC 名字：形状校验代替英文口头禅黑名单
    check("英文口头禅不是名字", npc._is_bogus_name("hey") is True)
    check("英文口头禅不是名字（ok）", npc._is_bogus_name("ok") is True)
    check("中文身份可以是名字", npc._is_bogus_name("学姐") is False)
    check("代词不是名字", npc._is_bogus_name("你") is True)
    check("路人不是名字", npc._is_bogus_name("路人") is True)

    # ── 4) 地点隐私是数据字段，不再按名字里的词猜
    check("私人地点词表已经删掉", not hasattr(npc, "_PRIVATE_KEYWORDS"))
    locations.ensure_defaults(app_db, conv2)
    check("默认的家里是私人空间",
          npc.is_private(app_db, conv2, "公寓客厅") is True)
    check("默认的便利店是公共空间",
          npc.is_private(app_db, conv2, "便利店") is False)
    custom, _ = locations.resolve(app_db, conv2, "旧仓库")
    check("新地点默认私人（不会凭空站人）",
          npc.is_private(app_db, conv2, custom) is True, str(custom))
    locations.set_private(app_db, conv2, custom, False)
    check("地点隐私可以显式声明成公共",
          npc.is_private(app_db, conv2, custom) is False)
    import services.scene_watcher as scene_watcher
    parsed = scene_watcher._parse(
        '{"new_location": "旅店房间", "entered_by": "both", "private": true, '
        '"reason": "两个人进去了"}')
    check("场景观察者能声明新地点是私人空间",
          parsed and parsed.get("private") is True, str(parsed))

    # 私人空间只认剧情点名的人：旧事件人物/路人不会站在屋里
    app_db.add_npc(conv2, "旧事件里的人", "邻居", "公寓客厅", "", declared=1)
    check("旧事件人物在私人空间里不显示",
          "旧事件里的人" not in npc.present_block(app_db, conv2, "公寓客厅"),
          npc.present_block(app_db, conv2, "公寓客厅"))
    app_db.add_npc(conv2, "妈妈", "尤夏的母亲", "公寓客厅", "", declared=1,
                   source="declared")
    check("剧情点名的家人仍然在",
          "妈妈" in npc.present_block(app_db, conv2, "公寓客厅"),
          npc.present_block(app_db, conv2, "公寓客厅"))

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
