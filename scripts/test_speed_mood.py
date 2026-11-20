"""
行动速度 + 心情头像 探针（桩模型，零额度）

覆盖：
1. 迁移新增 conversations.speed / yuxia_mood，默认值正确
2. 等待期仍是动态的（FIRST × GROWTH^n），速度只是乘系数，且任何速度下封顶 20 秒
3. POST /api/speed 改速度、/api/scene 回传 speed 与 mood_face
4. 她输出里的「心情」会被解析、落库、并体现在 /api/scene 上
5. 颜文字只出现在手机消息里（当面不带）——检查提示词规则

用法：python scripts/test_speed_mood.py
"""
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database   # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "speed_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db            # noqa: E402
from core.auth import generate_token         # noqa: E402
import core.locations as locations           # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402
import services.autonomy as autonomy         # noqa: E402
from config import (                         # noqa: E402
    AUTONOMY_WAIT_FIRST_SECONDS, AUTONOMY_WAIT_GROWTH, AUTONOMY_WAIT_MAX_SECONDS,
    AUTONOMY_SPEED_FACTORS, MOOD_FACES, MOOD_DEFAULT,
)

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


class StubLLM:
    def __init__(self, reply):
        self.reply = reply
        self.system_prompt = ""

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        self.system_prompt = system_prompt
        yield self.reply

    def chat_sync(self, *a, **kw):
        return "[生成失败: 测试桩]"


def sse(resp):
    out = []
    for raw in resp.response:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        for part in line.split("\n"):
            if part.strip().startswith("data: "):
                try:
                    out.append(json.loads(part.strip()[6:]))
                except Exception:
                    pass
    return out


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user))

    conv = app_db.create_conversation(user)
    locations.ensure_defaults(app_db, conv)

    # ── 1) 迁移与默认值
    check("speed 有默认值", app_db.get_speed(conv) == 3, str(app_db.get_speed(conv)))
    check("mood 初始为空", app_db.get_yuxia_mood(conv) == "", app_db.get_yuxia_mood(conv))

    # ── 2) 等待期：动态 + 速度系数 + 20 秒封顶
    def wait_at(done_count, speed, channel="chat"):
        app_db.set_player_location(conv, app_db.get_current_location(conv))
        app_db.set_last_user_at(conv, time.time())
        app_db.set_speed(conv, speed)
        app_db.set_last_yuxia_at(conv, time.time(), channel)
        # 伪造 n 次自主输出
        app_db.clear_autonomy(conv) if hasattr(app_db, "clear_autonomy") else None
        for i in range(done_count):
            app_db.push_autonomy(conv, {"action": "speak", "text": f"第{i}句",
                                        "at": time.time() + i, "at_minutes": 0})
        need, _at = autonomy._wait_seconds_needed(app_db, conv)
        return need

    base_need = wait_at(0, 3)
    check("默认速度第一段是 8 秒", base_need == 8, str(base_need))
    slow_need = wait_at(0, 1)
    fast_need = wait_at(0, 5)
    check("调慢会等更久", slow_need > base_need, f"{slow_need} vs {base_need}")
    check("调快会等更短", fast_need < base_need, f"{fast_need} vs {base_need}")
    growth_need = wait_at(1, 3)
    check("慢速下仍然按次数递增（动态）", wait_at(1, 1) > wait_at(0, 1),
          f"{wait_at(1, 1)} vs {wait_at(0, 1)}")
    check("最快速度也保留递增", wait_at(1, 5) > wait_at(0, 5),
          f"{wait_at(1, 5)} vs {wait_at(0, 5)}")
    worst = wait_at(9, 1)
    check(f"任何速度下封顶 {AUTONOMY_WAIT_MAX_SECONDS} 秒",
          worst <= AUTONOMY_WAIT_MAX_SECONDS, str(worst))
    check("封顶值确实是 20", AUTONOMY_WAIT_MAX_SECONDS == 20, str(AUTONOMY_WAIT_MAX_SECONDS))

    # ── 3) 速度接口 + 场景回传
    r = c.post(f"/api/speed/{conv}", json={"speed": 5})
    check("速度接口可用", r.status_code == 200 and (r.get_json() or {}).get("speed") == 5,
          str(r.status_code))
    check("速度落库", app_db.get_speed(conv) == 5, str(app_db.get_speed(conv)))
    r = c.post(f"/api/speed/{conv}", json={"speed": 99})
    check("超范围会被夹到上限", (r.get_json() or {}).get("speed") == 5, str(r.get_json()))
    r = c.post(f"/api/speed/{conv}", json={"speed": "abc"})
    check("非法值被拒 (400)", r.status_code == 400, str(r.status_code))

    scene = c.get(f"/api/scene/{conv}").get_json() or {}
    check("场景接口回传 speed", scene.get("speed") == 5, str(scene.get("speed")))
    check("场景接口回传默认心情表情",
          scene.get("mood") == MOOD_DEFAULT and scene.get("mood_face") == MOOD_FACES[MOOD_DEFAULT],
          f"{scene.get('mood')} / {scene.get('mood_face')}")

    # ── 4) 她输出「心情」→ 落库 + 场景可见
    real = chat_mod.deepseek
    stub = StubLLM('{"对白": "……嗯。", "心情": "害羞"}')
    chat_mod.deepseek = stub
    try:
        r = c.post("/api/chat", json={"conversation_id": conv, "message": "你今天有点可爱"})
        sse(r)
        check("心情落库", app_db.get_yuxia_mood(conv) == "害羞", app_db.get_yuxia_mood(conv))
        scene = c.get(f"/api/scene/{conv}").get_json() or {}
        check("场景接口给出对应颜文字",
              scene.get("mood_face") == MOOD_FACES["害羞"], str(scene.get("mood_face")))
        last = [m for m in app_db.get_messages(conv) if m["role"] == "assistant"][-1]
        check("心情也写进了消息 JSON", "害羞" in last["content"], last["content"])
    finally:
        chat_mod.deepseek = real

    # ── 5) 颜文字规则：只在手机消息里
    prompt = open(os.path.join(ROOT, "yuxia_prompt.txt"), encoding="utf-8").read()
    check("提示词写明当面不带颜文字", "当面说话一律不带颜文字" in prompt)
    check("提示词写明手机照用", "只在手机消息里用" in prompt)
    check("提示词给了心情字段", "**心情**" in prompt and "平静" in prompt)

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
