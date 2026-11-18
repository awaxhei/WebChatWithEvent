"""
真实端到端验证 v4（消耗少量 API 额度，deepseek-flash + 思考模式）
覆盖：短对白 / 无动作字段 / 场景描写 / 玩家移动 / 手机通道 / 未读 / 自主行动

用法：python scripts/e2e_chat.py
"""
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "v4_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app                 # noqa: E402
from core.auth import generate_token  # noqa: E402

CURRENT = {"id": ""}


def read_sse(resp):
    events = []
    for raw in resp.response:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        for part in line.split("\n"):
            part = part.strip()
            if part.startswith("data: "):
                try:
                    events.append(json.loads(part[6:]))
                except Exception:
                    pass
    return events


def turn(c, msg, label=""):
    print("-" * 62)
    print(f"[{label or '对话'}] 用户：{msg}")
    r = c.post("/api/chat", json={"conversation_id": CURRENT["id"], "message": msg})
    events = read_sse(r)
    text = "".join(e.get("content", "") for e in events if e.get("type") == "chunk")
    msg_count, channel = 1, ""
    for ev in events:
        if ev.get("type") == "conv_id":
            CURRENT["id"] = ev["id"]
        elif ev.get("type") == "story_time":
            print(f"  [时钟] {ev['content']} jump={ev.get('jump')}")
        elif ev.get("type") == "scene_changed":
            print(f"  [场景切换] {(ev.get('scene') or {}).get('location')}")
        elif ev.get("type") == "done":
            msg_count = ev.get("msg_count", 1)
            channel = ev.get("channel", "")
    print(f"  [通道] {channel}")
    print(f"  [AI A] {text.strip()}")
    print(f"  [检查] 含动作字段: {'\"动作\"' in text}（应为 False）")

    r2 = c.post(f"/api/atmosphere/{CURRENT['id']}",
                json={"msg_count": msg_count, "is_push_cmd": False})
    for ev in read_sse(r2):
        t = ev.get("type")
        if t in ("world_narration", "story_summary", "autonomy"):
            print(f"  [{t}] {ev.get('content')} {ev.get('action') or ''}")
        elif t == "reflections":
            print(f"  [反思] {ev.get('items')}")
    return text


def main():
    app.config["TESTING"] = True
    c = app.test_client()
    try:
        c.set_cookie("auth_token", generate_token(1))
    except TypeError:
        c.set_cookie("localhost", "auth_token", generate_token(1))

    turn(c, "我回来了，今天好累。", "同处一地")

    scene = c.get(f"/api/scene/{CURRENT['id']}").get_json()
    print("-" * 62)
    print(f"[场景] {scene['location']} / {scene['bucket']}")
    print(f"       {scene['description']}")
    print(f"       同处一地: {scene['co_located']}  已知地点: {len(scene['locations'])} 个")

    moved = c.post(f"/api/move/{CURRENT['id']}",
                   json={"who": "player", "location": "厨房"}).get_json()
    print("-" * 62)
    print(f"[移动] 玩家 -> {moved['player_location']}  同处一地: {moved['co_located']}")
    print(f"[场景] {moved['location']} / {moved['bucket']}: {moved['description']}")
    print(f"[玩家视角场景] {moved.get('player_description', '')[:80]}")

    turn(c, "我到厨房了，你想吃什么？", "不同场景")

    phone = c.get(f"/api/phone/{CURRENT['id']}").get_json()
    print("-" * 62)
    print(f"[手机] 未读 {phone['unread']} 条")
    for m in phone["messages"][-6:]:
        print(f"  {m['role']}: {m['content'][:60]}")

    c.post(f"/api/phone/read/{CURRENT['id']}")
    print(f"[手机] 标记已读后未读: {c.get('/api/phone/' + CURRENT['id']).get_json()['unread']}")

    idle = c.post(f"/api/idle/{CURRENT['id']}").get_json()
    print("-" * 62)
    print(f"[闲置] acted={idle.get('acted')} action={idle.get('action')} text={idle.get('text')}")

    db = database.ChatDB()
    print("=" * 62)
    print("记忆条数:", db.count_memories(CURRENT["id"]))
    print("事件:")
    for e in db.get_events(CURRENT["id"], limit=10):
        print(f"  [{e['event_type']}] tier={e.get('tier') or '-'} {e['title']} @{e['story_time']}")
    print("临时库:", database.DB_PATH)


if __name__ == "__main__":
    main()
