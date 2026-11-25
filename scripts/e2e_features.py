"""
功能面覆盖检查（不走长对话，只打接口）

覆盖：速度 / 暂停 / 换地方 / 上课禁入 / 表情包导入与发送 / 故事进展 /
      后台通知 / NPC 列表 / 网页与手机端权限一致

用法：python scripts/e2e_features.py
"""
import hashlib
import io
import json
import os
import secrets
import struct
import sys
import uuid
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

TMP = os.path.join(ROOT, ".testtmp", "features_" + uuid.uuid4().hex[:6])
os.makedirs(TMP, exist_ok=True)
database.DB_PATH = os.path.join(TMP, "chat.db")

from app import app, db as app_db            # noqa: E402
import core.locations as locations           # noqa: E402
import core.stickers as stickers             # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402

STICKER_TMP = os.path.join(TMP, "stickers")
os.makedirs(STICKER_TMP, exist_ok=True)
stickers.STICKER_DIR = STICKER_TMP
stickers.VISION_ENABLED = False

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(f"{name}  {extra}".strip())


class StubLLM:
    def __init__(self, reply='{"对白": "……嗯。"}'):
        self.reply = reply
        self.system_prompt = ""
        self.messages = []

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        self.system_prompt = system_prompt
        self.messages = list(messages)
        yield self.reply

    def chat_sync(self, *a, **kw):
        return "[生成失败: 测试桩]"


def make_png(seed=0):
    width = height = 8

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b""
    for y in range(height):
        raw += b"\x00" + bytes([(seed * 7 + x * 3 + y) % 256 for x in range(width * 3)])
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def sse_events(body):
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return out


def main():
    app.config["TESTING"] = True
    name = "feat_" + secrets.token_hex(3)
    password = secrets.token_urlsafe(12)
    app_db.sync_accounts({name: hashlib.sha256(password.encode()).hexdigest()})
    uid = app_db.get_user(name)["id"]

    web = app.test_client()
    login = (web.post("/api/auth/login",
                      json={"username": name, "password": password}).get_json() or {})
    token = login.get("token") or ""
    check("网页端登录", bool(token), str(login))

    mobile = app.test_client()
    mobile.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    mobile.environ_base["HTTP_X_DEEPSEEK_API_KEY"] = \
        __import__("config").DEEPSEEK_API_KEY

    conv = (web.post("/api/history/new", json={}).get_json() or {}).get("id", "")
    locations.ensure_defaults(app_db, conv)
    locations.switch_both(app_db, conv, "公寓客厅")
    check("建会话", bool(conv))

    # ── 权限：手机端 Bearer 与网页端 Cookie 都能用，匿名被拒
    check("手机端 Bearer 可用", mobile.get("/api/history").status_code == 200)
    check("匿名被拒 (401)", app.test_client().get("/api/history").status_code == 401)

    # ── 速度
    r = mobile.post(f"/api/speed/{conv}", json={"speed": 5})
    check("速度可以调到 5", (r.get_json() or {}).get("speed") == 5, str(r.get_json()))
    r = mobile.post(f"/api/speed/{conv}", json={"speed": 99})
    check("速度超范围被夹紧", (r.get_json() or {}).get("speed") == 5, str(r.get_json()))
    check("速度落库", app_db.get_speed(conv) == 5, str(app_db.get_speed(conv)))

    # ── 暂停：世界停转，但对话照常
    r = mobile.post(f"/api/pause/{conv}", json={"paused": True})
    check("可以暂停", (r.get_json() or {}).get("paused") is True, str(r.get_json()))
    atm = sse_events(web.post(f"/api/atmosphere/{conv}",
                              json={"msg_count": 1}).data.decode("utf-8"))
    check("暂停后世界流直接结束", any(e.get("type") == "done" and e.get("paused") for e in atm),
          str(atm)[:120])
    idle = mobile.post(f"/api/idle/{conv}", json={}).get_json() or {}
    check("暂停后她不会自行行动", idle.get("paused") is True, str(idle))
    mobile.post(f"/api/pause/{conv}", json={"paused": False})

    # ── 换地方（接口版，App 的地点选择器走它）
    r = mobile.post(f"/api/move/{conv}", json={"who": "both", "location": "厨房"})
    check("拉着她一起换地方", r.status_code == 200 and (r.get_json() or {}).get("co_located") is True,
          str(r.get_json()))
    r = mobile.post(f"/api/move/{conv}", json={"who": "player", "location": "公寓客厅"})
    check("玩家一个人离开（异地）",
          r.status_code == 200 and (r.get_json() or {}).get("co_located") is False,
          str(r.get_json()))
    r = mobile.post(f"/api/move/{conv}", json={"who": "both", "location": "公园"})
    check("异地时没法拉着她走", r.status_code == 400, str(r.get_json()))
    r = mobile.post(f"/api/move/{conv}", json={"who": "player", "location": "厨房"})
    check("走回她身边", (r.get_json() or {}).get("co_located") is True, str(r.get_json()))
    r = mobile.post(f"/api/move/{conv}", json={"who": "both", "location": "公寓客厅"})
    check("回到一起之后可以一起走",
          (r.get_json() or {}).get("co_located") is True, str(r.get_json()))

    # ── 上课中的教室进不去（兜底日程 8:30–11:40 在上课）
    app_db.set_story_minutes(conv, 9 * 60)
    locations.switch_both(app_db, conv, "公寓客厅")
    r = mobile.post(f"/api/move/{conv}", json={"who": "player", "location": "大学教室"})
    check("上课时段教室进不去", r.status_code == 400 and "课" in (r.get_json() or {}).get("error", ""),
          str(r.get_json()))

    # ── 表情包：导入 → 列表 → 当成图发给她
    png = make_png(3)
    up = (mobile.post("/api/stickers/upload", content_type="multipart/form-data",
                      data={"files": [(io.BytesIO(png), "cat.png")]}).get_json() or {})
    check("表情包导入", up.get("uploaded_count") == 1, str(up)[:120])
    listing = mobile.get("/api/stickers/list").get_json() or {}
    items = listing.get("items") or listing.get("stickers") or []
    check("表情包列表里有它", bool(items), str(listing)[:120])
    stats = mobile.get("/api/stickers/stats").get_json() or {}
    check("表情包统计可用", isinstance(stats, dict), str(stats)[:80])

    stub = StubLLM('{"对白": "这什么鬼表情。"}')
    chat_mod.deepseek = stub
    rel = items[0].get("path") if isinstance(items[0], dict) else items[0]
    r = web.post("/api/chat", json={"conversation_id": conv, "sticker": rel})
    body = r.data.decode("utf-8")          # 必须消费流，生成器才会真的跑
    check("表情包能当作消息发出去",
          r.status_code == 200 and '"type": "done"' in body, str(r.status_code))
    seen = json.dumps(stub.messages, ensure_ascii=False) + stub.system_prompt
    check("她真的“看见”了那张图", "表情包" in seen, seen[-200:])
    sticker_rows = [m for m in app_db.get_messages(conv)
                    if m["role"] == "user" and "表情包" in (m["content"] or "")]
    check("库里存成表情包消息", bool(sticker_rows),
          str([m["content"][:40] for m in sticker_rows][:2]))
    reply_rows = [m for m in app_db.get_messages(conv) if m["role"] == "assistant"]
    check("她对着表情包回了话", bool(reply_rows), str(len(reply_rows)))

    # ── 故事进展 / NPC 列表
    ev = web.get(f"/api/events/{conv}").get_json() or {}
    check("故事进展接口可用", isinstance(ev, dict) and "events" in ev, str(ev)[:120])
    npcs = web.get(f"/api/npcs/{conv}").get_json() or {}
    check("NPC 列表接口可用", isinstance(npcs, dict), str(npcs)[:80])

    # ── 后台通知：只给她发的消息、按 since_id 增量
    app_db.add_message(conv, "assistant", "在吗", channel="phone")
    app_db.add_message(conv, "assistant", "我先去洗澡了", channel="phone")
    pending = mobile.get(f"/api/notify/pending?conversation_id={conv}&since_id=0").get_json() or {}
    check("通知接口返回她的消息", (pending.get("messages") or []) != [], str(pending)[:120])
    last_id = pending.get("last_id", 0)
    again = mobile.get(f"/api/notify/pending?conversation_id={conv}&since_id={last_id}").get_json() or {}
    check("按 since_id 增量", (again.get("messages") or []) == [], str(again)[:120])

    # ── 网页端与手机端看到同一份历史
    hw = web.get(f"/api/history/{conv}").get_json() or {}
    hm = mobile.get(f"/api/history/{conv}").get_json() or {}
    check("两端历史条数一致",
          len(hw.get("messages") or []) == len(hm.get("messages") or []),
          f"{len(hw.get('messages') or [])} vs {len(hm.get('messages') or [])}")

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项")
        for f in FAILED:
            print("  -", f)
        return 1
    print("功能面全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
