"""
冒烟测试：接口层回归（不消耗 API 额度）
- 使用隔离的临时库 .testtmp/smoke，绝不碰真实 data/chat.db
- 覆盖：鉴权、会话越权、静态资源、SSE 结构、表情包接口

用法：python scripts/smoke_test.py
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 必须在导入 app 之前把数据库指向临时目录
import core.database as database    # noqa: E402

SMOKE_DIR = os.path.join(ROOT, ".testtmp", "smoke")
shutil.rmtree(SMOKE_DIR, ignore_errors=True)
os.makedirs(SMOKE_DIR, exist_ok=True)
database.DB_PATH = os.path.join(SMOKE_DIR, "chat.db")

from app import app, db as app_db     # noqa: E402
from core.auth import generate_token  # noqa: E402
import routes.chat_routes as chat_mod  # noqa: E402

FAILED = []


class StubDeepSeek:
    """测试桩：验证路由与 SSE 结构，不消耗额度"""
    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        yield '{"对白": "「……嗯。」"}'

    def chat_sync(self, *args, **kwargs):
        return "[生成失败: 测试桩]"


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def client_for(user_id):
    c = app.test_client()
    token = generate_token(user_id)
    try:
        c.set_cookie("auth_token", token)
    except TypeError:
        c.set_cookie("localhost", "auth_token", token)
    return c


def main():
    app.config["TESTING"] = True

    # 预置数据：两个用户各一个会话（用于越权测试）
    user1 = app_db.get_user("awaxhei")["id"]
    user2 = app_db.get_user("user2")["id"]
    conv1 = app_db.create_conversation(user1)
    app_db.add_message(conv1, "user", "归属人1的消息")
    conv2 = app_db.create_conversation(user2)
    app_db.add_message(conv2, "user", "归属人2的消息")

    anon = app.test_client()
    c1 = client_for(user1)
    c2 = client_for(user2)

    r = anon.get("/api/history")
    check("未登录访问 /api/history 返回 401", r.status_code == 401, f"got {r.status_code}")

    r = anon.get("/api/config")
    cfg = r.get_json() or {}
    check("/api/config 可用且含 has_server_key", r.status_code == 200 and "has_server_key" in cfg)
    check("/api/config 不泄露明文 Key", "sk-" not in str(cfg))

    r = anon.get("/static/image/icon-192.png")
    check("PWA 图标 icon-192.png 可访问", r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n")
    r = anon.get("/static/js/api_key.js")
    check("api_key.js 可访问", r.status_code == 200)

    # Service Worker 必须在根路径并带 Service-Worker-Allowed，才能控制整站
    r = anon.get("/sw.js")
    check("根路径 sw.js 可访问", r.status_code == 200, f"got {r.status_code}")
    check("sw.js 允许根作用域", r.headers.get("Service-Worker-Allowed") == "/")

    r = c1.get("/api/history")
    convs = r.get_json()
    check("用户1 历史列表返回 200 且为数组", r.status_code == 200 and isinstance(convs, list))
    check("用户1 只看到自己的会话", all(c["id"] == conv1 for c in (convs or [])))

    r = c1.get(f"/api/history/{conv1}")
    detail = r.get_json() or {}
    check("会话归属人可读取会话", r.status_code == 200)
    check("会话详情返回故事时间", str(detail.get("story_time", "")).startswith("第"),
          str(detail.get("story_time")))
    check("会话详情返回当前地点", bool(detail.get("location")), str(detail.get("location")))

    r = c2.get(f"/api/history/{conv1}")
    check("越权读取会话被拒绝 (404)", r.status_code == 404, f"got {r.status_code}")
    r = c2.delete(f"/api/history/{conv1}")
    check("越权删除会话被拒绝 (404)", r.status_code == 404, f"got {r.status_code}")
    r = c2.get(f"/api/events/{conv1}")
    check("越权读事件被拒绝 (404)", r.status_code == 404, f"got {r.status_code}")
    r = c2.post(f"/api/events/init/{conv1}")
    check("越权初始化事件被拒绝 (404)", r.status_code == 404, f"got {r.status_code}")
    r = c2.post(f"/api/atmosphere/{conv1}", json={})
    check("越权请求氛围接口被拒绝 (404)", r.status_code == 404, f"got {r.status_code}")

    r = anon.get("/api/stickers")
    check("/api/stickers 可用", r.status_code == 200 and "emotions" in (r.get_json() or {}))
    r = anon.get("/api/stickers/stats")
    check("表情包统计接口可用", r.status_code == 200 and "total" in (r.get_json() or {}))
    r = anon.get("/api/stickers/match?q=害羞")
    check("表情包匹配接口可用", r.status_code == 200 and "matches" in (r.get_json() or {}))

    # 用测试桩跑完整聊天/氛围链路
    real_deepseek = chat_mod.deepseek
    chat_mod.deepseek = StubDeepSeek()
    try:
        r = c1.post("/api/chat", json={"conversation_id": "", "message": "测试一下链路"})
        events = [json.loads(line[6:]) for line in r.data.decode("utf-8").split("\n")
                  if line.startswith("data: ")]
        types = [e.get("type") for e in events]
        check("聊天接口返回 SSE 流", r.status_code == 200 and "chunk" in types and "done" in types,
              f"{r.status_code} {types}")
        check("聊天接口下发确定性故事时间",
              any(e.get("type") == "story_time" and str(e.get("content", "")).startswith("第")
                  for e in events))

        conv_new = next((e["id"] for e in events if e.get("type") == "conv_id"), "")
        r = c1.post(f"/api/atmosphere/{conv_new}", json={"msg_count": 1, "is_push_cmd": False})
        check("氛围接口返回 SSE 流（不是 500/None）",
              r.status_code == 200 and b'"type": "done"' in r.data,
              f"{r.status_code} {r.data[:80]}")

        # v4：场景 / 移动 / 手机 / 闲置
        r = c1.get(f"/api/scene/{conv_new}")
        scene = r.get_json() or {}
        check("场景接口可用", r.status_code == 200 and "description" in scene, str(r.status_code))
        check("场景接口返回双方位置",
              "player_location" in scene and "co_located" in scene)

        r = c1.post(f"/api/move/{conv_new}", json={"who": "player", "location": "厨房"})
        moved = r.get_json() or {}
        check("玩家可以主动移动", r.status_code == 200 and moved.get("player_location") == "厨房",
              str(r.status_code))
        evs_move = app_db.get_recent_events(conv_new, limit=8)
        check("换场景留痕（尤夏下一轮才知道人走了）",
              any(e["event_type"] == "scene_change" and "青梅去了厨房" in (e["title"] or "")
                  for e in evs_move),
              str([e["title"] for e in evs_move]))

        r = c1.post(f"/api/move/{conv_new}", json={"who": "yuxia", "location": "公园"})
        check("尤夏可以被移动", (r.get_json() or {}).get("location") == "公园")

        # 一起走：异地时不行，会合之后可以
        r = c1.post(f"/api/move/{conv_new}", json={"who": "both", "location": "厨房"})
        check("异地时不能一起移动 (400)", r.status_code == 400, f"got {r.status_code}")
        c1.post(f"/api/move/{conv_new}", json={"who": "player", "location": "公园"})
        r = c1.post(f"/api/move/{conv_new}", json={"who": "both", "location": "厨房"})
        both = r.get_json() or {}
        check("两人可以一起移动",
              both.get("location") == "厨房" and both.get("player_location") == "厨房",
              str(both.get("location")) + "/" + str(both.get("player_location")))

        # 聊天指令 /带 地点：把两个人一起带走
        r = c1.post("/api/chat", json={"conversation_id": conv_new, "message": "/带 卧室"})
        evs = [json.loads(line[6:]) for line in r.data.decode("utf-8").split("\n")
               if line.startswith("data: ")]
        check("/带 指令返回 SSE", any(e.get("type") == "done" for e in evs), str(r.status_code))
        check("/带 把两人一起带走",
              app_db.get_current_location(conv_new) == "卧室"
              and app_db.get_player_location(conv_new) == "卧室",
              f"{app_db.get_current_location(conv_new)} / {app_db.get_player_location(conv_new)}")

        r = c1.get(f"/api/phone/{conv_new}")
        phone = r.get_json() or {}
        check("手机接口可用", r.status_code == 200 and "messages" in phone and "unread" in phone)

        r = c1.post(f"/api/phone/read/{conv_new}")
        check("手机已读接口可用", r.status_code == 200 and (r.get_json() or {}).get("unread") == 0)

        r = c1.post(f"/api/idle/{conv_new}")
        idle = r.get_json() or {}
        check("闲置自主行动接口可用", r.status_code == 200 and "acted" in idle)

        r = c2.get(f"/api/scene/{conv_new}")
        check("越权读场景被拒绝 (404)", r.status_code == 404, f"got {r.status_code}")

        # 时间调节（前端拨表盘）
        before = app_db.get_story_minutes(conv_new)
        r = c1.post(f"/api/time/{conv_new}", json={"hour": 21, "minute": 30})
        moved = r.get_json() or {}
        check("时间可以拨到晚上",
              r.status_code == 200 and moved.get("story_minutes", 0) > before,
              f"{r.status_code} {moved.get('story_minutes')}")
        check("返回前后时间点", bool(moved.get("from")) and bool(moved.get("to")),
              f"{moved.get('from')} -> {moved.get('to')}")
        check("拨完落库",
              app_db.get_story_minutes(conv_new) == moved.get("story_minutes"),
              str(app_db.get_story_minutes(conv_new)))
        events_now = app_db.get_recent_events(conv_new, limit=5)
        check("写入 time_advance 事件",
              any(e["event_type"] == "time_advance" for e in events_now),
              str([e["event_type"] for e in events_now]))
        r = c1.post(f"/api/time/{conv_new}", json={"minutes": 10})
        check("不能往回拨 (400)", r.status_code == 400, str(r.status_code))

        # 模型偶尔用中文引号 + 中文冒号当 JSON 语法，必须能掰回来
        raw = '{“消息”： “唔——诶？你在说什么啊(ᗜ o ᗜ)”}'
        objs = chat_mod._extract_json_objects(chat_mod._normalize_quotes(raw))
        check("中文引号与中文冒号也能解析",
              bool(objs) and "唔——诶" in (objs[0].get("消息") or ""), str(objs))
    finally:
        chat_mod.deepseek = real_deepseek

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
