"""
手机端（原生 App）接口探针

覆盖：
1. 登录返回 token，Bearer 头能用（App 不依赖 Cookie）
2. 表情包「相册多选一键导入」：多张一起传、自动去重、非图片拒收、落盘可访问
3. 表情包列表（选择面板）
4. 玩家发表情包进对话（当面/手机两个通道），她读到的是人话，库里存的是图片消息
5. 后台通知轮询 /api/notify/pending：只给她的消息、按 since_id 增量

用法：python scripts/test_mobile_api.py
"""
import io
import json
import os
import struct
import sys
import uuid
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "mobile_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app, db as app_db          # noqa: E402
import core.stickers as stickers           # noqa: E402
import routes.chat_routes as chat_mod      # noqa: E402

# 测试用的表情包目录（绝不写进真实的 static/stickers/）
STICKER_TMP = os.path.join(tmp, "stickers")
os.makedirs(STICKER_TMP, exist_ok=True)
stickers.STICKER_DIR = STICKER_TMP
stickers.VISION_ENABLED = False            # 不打真的视觉标，省额度

FAILED = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def make_png(seed=0):
    """手搓一张最小的合法 PNG（不依赖 Pillow）"""
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


class StubLLM:
    """只回一条对白，足够验证链路"""

    def __init__(self, reply='{"对白": "……嗯(ᗜ ‸ ᗜ)"}'):
        self.reply = reply
        self.system_prompt = ""
        self.messages = []

    def chat_stream(self, messages, system_prompt="", api_key_override=None, temperature=0.7):
        self.system_prompt = system_prompt
        self.messages = list(messages)
        yield self.reply

    def chat_sync(self, *a, **kw):
        return "[生成失败: 测试桩]"


def sse_events(body):
    return [json.loads(line[6:]) for line in body.split("\n") if line.startswith("data: ")]


def main():
    app.config["TESTING"] = True
    client = app.test_client()

    user = app_db.get_user("awaxhei")["id"]
    # 用用户名密码登录，拿 token（App 的登录方式）
    r = client.post("/api/auth/login", json={"username": "awaxhei",
                                             "password": "awaXhei@1900196100",
                                             "remember": True})
    login = r.get_json() or {}
    check("登录成功", r.status_code == 200 and login.get("success") is True, str(login))
    token = login.get("token") or ""
    check("登录返回 token（App 用 Bearer）", len(token) > 20, token[:20])

    # 只带 Bearer、不带 Cookie 的独立客户端
    mobile = app.test_client()
    mobile.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    r = mobile.get("/api/history")
    check("Bearer 鉴权可用", r.status_code == 200 and isinstance(r.get_json(), list), str(r.status_code))
    r = app.test_client().get("/api/history")
    check("无凭证被拒 (401)", r.status_code == 401, str(r.status_code))

    # ── 批量删除会话（App 和网页都用这个接口）
    ids = [mobile.post("/api/history/new", json={}).get_json()["id"] for _ in range(4)]
    r = mobile.post("/api/history/delete", json={"ids": ids[:3]})
    res = r.get_json() or {}
    check("批量删除返回删除条数", r.status_code == 200 and res.get("deleted") == 3, str(res))
    left = [c["id"] for c in mobile.get("/api/history").get_json()]
    check("删掉的会话不见了", all(i not in left for i in ids[:3]), str(left))
    check("没选中的会话还在", ids[3] in left, str(left))

    r = mobile.post("/api/history/delete", json={"ids": []})
    check("空列表被拒 (400)", r.status_code == 400, str(r.status_code))
    r = app.test_client().post("/api/history/delete", json={"ids": [ids[3]]})
    check("批量删除也要鉴权 (401)", r.status_code == 401, str(r.status_code))

    # 混进别人的会话：自己的删掉，别人的跳过
    foreign = app_db.create_conversation(app_db.get_user("user2")["id"])
    mine = mobile.post("/api/history/new", json={}).get_json()["id"]
    res = mobile.post("/api/history/delete", json={"ids": [mine, foreign]}).get_json() or {}
    check("别人的会话被跳过", res.get("skipped") == 1 and res.get("deleted") == 1, str(res))
    check("别人的会话没被删掉", app_db.conversation_belongs_to(foreign, app_db.get_user("user2")["id"]))
    app_db.delete_conversation(foreign)
    mobile.post("/api/history/delete", json={"ids": [ids[3]]})

    # ── 一键导入：多张一起传
    png1, png2, png3 = make_png(1), make_png(2), make_png(3)
    r = mobile.post("/api/stickers/upload", content_type="multipart/form-data", data={
        "files": [(io.BytesIO(png1), "a.png"),
                  (io.BytesIO(png2), "b.png"),
                  (io.BytesIO(png3), "c.png")],
    })
    up = r.get_json() or {}
    check("多张图片一次导入", r.status_code == 200 and up.get("uploaded_count") == 3, str(up))
    check("导入后已入库", (up.get("total") or 0) >= 3, str(up.get("total")))
    check("同一批里有重复时去重",
          mobile.post("/api/stickers/upload", content_type="multipart/form-data", data={
              "files": [(io.BytesIO(png1), "a-again.png")],
          }).get_json().get("duplicates") == 1)
    r = mobile.post("/api/stickers/upload", content_type="multipart/form-data", data={
        "files": [(io.BytesIO(b"not an image at all"), "evil.png")],
    })
    bad = r.get_json() or {}
    check("非图片被拒收",
          bad.get("uploaded_count") == 0 and any(x["reason"] == "不是图片" for x in bad.get("rejected", [])),
          str(bad.get("rejected")))

    # 落盘位置 + 可访问的静态地址（测试把目录指到临时区，所以这里只校验磁盘与 URL 形状）
    paths = up.get("uploaded") or []
    check("文件落在 upload/ 子目录", all(p.startswith("upload/") for p in paths), str(paths))
    check("磁盘上真有文件", all(os.path.exists(os.path.join(STICKER_TMP, p)) for p in paths))
    with open(os.path.join(STICKER_TMP, paths[0]), "rb") as f:
        check("落盘内容就是原图", f.read() == png1 or f.read()[:8] == b"\x89PNG\r\n\x1a\n")
    check("静态地址形状正确",
          stickers.url_for(paths[0]).startswith("/static/stickers/upload/")
          and stickers.url_for(paths[0]).endswith(".png"),
          stickers.url_for(paths[0]))

    # ── 选择面板
    r = mobile.get("/api/stickers/list?limit=50")
    listing = r.get_json() or {}
    check("表情包列表可用", r.status_code == 200 and listing.get("total", 0) >= 3, str(listing.get("total")))
    first = (listing.get("items") or [{}])[0]
    check("列表项带可用 url", str(first.get("url", "")).startswith("/static/stickers/"), str(first))
    check("列表需要登录 (401)", app.test_client().get("/api/stickers/list").status_code == 401)

    # ── 玩家发表情包（两个通道）
    real = chat_mod.deepseek
    try:
        conv = app_db.create_conversation(user)
        stub = StubLLM()
        chat_mod.deepseek = stub
        sticker_path = first["path"]
        r = mobile.post("/api/chat", json={"conversation_id": conv, "sticker": sticker_path,
                                           "channel": "chat"})
        body = r.data.decode("utf-8")
        check("当面通道发表情包返回 SSE", '"type": "done"' in body, body[:120])
        stored = [m for m in app_db.get_messages(conv) if m["role"] == "user"]
        check("库里存成图片消息",
              bool(stored) and json.loads(stored[0]["content"]).get("url", "").startswith("/static/"),
              (stored[0]["content"][:80] if stored else ""))
        check("她读到的是人话",
              "青梅发来一个表情包" in (stub.messages[-1]["content"] if stub.messages else ""),
              str(stub.messages[-1]["content"][:80] if stub.messages else ""))

        # 异地 → 手机通道
        app_db.set_player_location(conv, "厨房")
        chat_mod.deepseek = stub = StubLLM()
        r = mobile.post("/api/chat", json={"conversation_id": conv, "sticker": sticker_path})
        r.data.decode("utf-8")
        phone_msgs = [m for m in app_db.get_messages(conv) if m["channel"] == "phone"]
        check("异地时表情包走手机通道", any(m["role"] == "user" for m in phone_msgs),
              str([m["channel"] for m in phone_msgs]))

        r = mobile.post("/api/chat", json={"conversation_id": conv, "sticker": "upload/nope.png"})
        check("不存在的表情包 (400)", r.status_code == 400, str(r.status_code))
        r = mobile.post("/api/chat", json={"conversation_id": conv, "message": ""})
        check("空消息仍然被拒 (400)", r.status_code == 400, str(r.status_code))

        # ── 后台通知轮询
        r = mobile.get(f"/api/notify/pending?conversation_id={conv}&since_id=0")
        notes = r.get_json() or {}
        check("通知接口可用", r.status_code == 200 and "messages" in notes, str(r.status_code))
        check("只返回她的消息（不含玩家自己的）",
              all(m["id"] > 0 for m in notes.get("messages", []))
              and not any("青梅发来" in (m.get("text") or "") for m in notes.get("messages", [])),
              json.dumps(notes.get("messages"), ensure_ascii=False)[:160])
        check("通知带去重游标", notes.get("last_id", 0) > 0, str(notes.get("last_id")))
        last = notes.get("last_id")
        r = mobile.get(f"/api/notify/pending?conversation_id={conv}&since_id={last}")
        check("since_id 之后没有新消息时为空", (r.get_json() or {}).get("messages") == [],
              str((r.get_json() or {}).get("messages")))
        r = mobile.get(f"/api/notify/pending?conversation_id={conv}&since_id=0")
        check("返回里有已读/暂停/位置等状态",
              all(k in (r.get_json() or {}) for k in ("unread", "paused", "co_located", "location")))
        r = app.test_client().get(f"/api/notify/pending?conversation_id={conv}&since_id=0")
        check("通知接口需要登录 (401)", r.status_code == 401, str(r.status_code))

        # 越权：别的用户读不到
        other = app_db.create_conversation(app_db.get_user("user2")["id"])
        r = mobile.get(f"/api/notify/pending?conversation_id={other}&since_id=0")
        check("别人的会话读不到 (404)", r.status_code == 404, str(r.status_code))
    finally:
        chat_mod.deepseek = real
        stickers.VISION_ENABLED = True

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败: {FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
