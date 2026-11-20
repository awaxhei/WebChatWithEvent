"""
100+ 轮端到端试玩（扮演青梅，走真模型）

用法：python scripts/e2e_play_100.py [轮数]

- 隔离临时库（不碰 data/chat.db）
- 按青梅的口吻连续说话，中途插入各项功能：手机通道 / 表情包 / 场景切换 /
  叫 NPC / 拨时间 / 故事进展 / 暂停 / 速度 / 推进模式 / 自主行动 / 通知 / 删会话
- 每轮做结构断言（SSE 完整、落库合法、通道正确、没有「尤夏：」前缀…），
  最后汇总成报告（docs/playtest_report.md）
"""
import io
import json
import os
import random
import re
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import requests  # noqa: E402

import core.database as database  # noqa: E402

TMP = os.path.join(ROOT, ".testtmp", "play_" + uuid.uuid4().hex[:6])
os.makedirs(TMP, exist_ok=True)
database.DB_PATH = os.path.join(TMP, "chat.db")

from app import app, db as app_db            # noqa: E402
from core.auth import generate_token         # noqa: E402
import core.locations as locations           # noqa: E402

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 105
REPORT = os.path.join(ROOT, "docs", "playtest_report.md")

stats = {
    "rounds": 0, "ok": 0, "http_fail": 0, "json_fail": 0, "prefix": 0,
    "phone": 0, "chat": 0, "npc": 0, "autonomy": 0, "sticker_sent": 0,
    "fallback": 0, "empty": 0, "moods": {}, "failures": [], "notes": [],
    "her_lines": [],
}
FAILED = []


def check(name, cond, extra=""):
    if cond:
        stats["ok"] += 1
        return True
    FAILED.append(name)
    stats["failures"].append(f"{name}  [{extra}]")
    print(f"  FAIL {name}  [{extra}]")
    return False


# ── 青梅要说的话（按场景/话题铺开，尽量自然） ──────────────
LINES = [
    "早啊，你还没醒？", "我给你买了豆浆，放桌上了", "你头发翘起来了，左边那撮",
    "今天要不要一起去超市", "你昨晚又熬夜看动画了吧", "冰箱里那盒布丁是谁吃掉的",
    "外面下雨了，你带伞了吗", "我伞放你包里了", "你尾巴刚才扫到我手了",
    "别装没听到", "你耳朵在动诶", "刚才是不是憋笑", "你是不是又在删聊天记录",
    "手机借我一下", "你屏幕亮了，谁给你发消息", "这表情包哪来的，笑死",
    "你今天穿这件好看", "别扯衣角，我说真的", "你脸红什么",
    "我先去洗个澡", "你等我一下，五分钟", "你吃饭了吗", "别拿泡面凑合",
    "我今天有点累", "陪我坐会儿", "你别老盯着手机", "话说回来，你昨天那个梦还记得吗",
    "我梦见你变成猫了", "然后你就一直跟着我", "醒来的时候有点失落",
    "你会不会有一天突然消失", "别笑，我是认真的", "算了，当我没说",
    "你今天好像特别安静", "是不是哪里不舒服", "手给我看看，怎么这么凉",
    "房间是不是有点冷", "你冷不冷", "把外套穿上", "我不是关心你，我冷",
    "刚刚楼下有人叫你名字", "我探头看了一眼，不认识", "你不认识就对了",
    "你今天怎么一直看我", "我脸上有东西？", "别看了，走开",
    "你昨天的表情包还在吗", "发我一张", "我要那种很欠揍的",
    "刚才那个问题你还没回答", "别转话题", "你又开始了",
    "我们认识多久了", "你算得出来吗", "我算了，十一年", "有点久了",
    "你还记得小时候你把我锁在教室吗", "我当时哭得可惨", "你后来还跟我道歉了，笨死了",
    "今天想吃你做的蛋包饭", "什么？不会？", "那我们一起做", "你别切到手就行",
    "你切菜的样子好认真", "眼睛都不眨", "怪可爱的", "我说了什么吗",
    "别用手指我", "手机还我", "你刚才是不是偷偷拍照了", "删掉",
    "你手上那个贴纸哪来的", "给我也贴一个", "不贴胸口，贴手背",
    "外面好像有人在放烟花", "去看看？", "你慢点，我鞋还没穿好",
    "你走这么快干嘛", "等等我", "手，给我", "不是，我怕摔",
    "今晚早点睡吧", "你别又通宵", "我监督你", "看什么看",
    "你今晚要不要一起看个电影", "别挑恐怖片", "上次那个我三天没睡好",
    "你打呼噜了", "我没睡着的时候听到的", "别否认",
    "你刚才在发呆", "想什么", "说出来听听", "好吧，不说了",
    "你手机壳怎么换了", "丑", "我说丑就是丑", "换回去",
    "今天风好大", "我头发被吹乱了", "你别看", "还看",
    "你帮我拿一下那个袋子", "重的那个", "谢了", "就一句谢",
    "你最近是不是瘦了", "下巴都尖了", "不准减肥", "我认真的",
    "你周末有空吗", "陪我去个地方", "不用准备，跟我走就行",
    "我数到三，你把手机放下", "一", "二", "……算了",
    "你是不是有话想说", "那你先组织一下语言", "我等着",
    "刚才那段路好黑", "你为什么一直走外侧", "没什么，就是注意到了",
    "你在看什么书", "我也想看", "一起看？", "那页我看过了，你翻",
    "今天有人跟我搭话", "问路的", "你紧张什么",
    "你手怎么出汗了", "是不是发烧了", "额头给我摸一下",
    "我又不咬人", "你退那么远干嘛", "过来",
    "明天要早起", "你叫我", "别装听不见", "叫不醒我就掀被子",
    "对了，你的充电线借我", "找不到我的了", "别问我为什么",
    "你昨天说的那个店，今天关门了", "白跑一趟", "你倒是说句话",
    "我想吃冰的", "你管我", "就一口", "好吧，两口",
    "你刚才在笑什么", "手机给我看", "不给拉倒",
    "我困了", "你肩膀借我靠一下", "就一下", "别动",
    "今天过得还挺快", "你觉得呢", "你回答得好敷衍", "重说",
]


def sse_round(session, base, conv, message=None, channel=None, sticker=None):
    payload = {"conversation_id": conv}
    if message is not None:
        payload["message"] = message
    if sticker:
        payload["sticker"] = sticker
    if channel:
        payload["channel"] = channel
    r = session.post(f"{base}/api/chat", json=payload, stream=True, timeout=300)
    events = []
    chunks = ""
    if r.status_code != 200:
        return r.status_code, events, chunks
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except json.JSONDecodeError:
            continue
        events.append(ev)
        if ev.get("type") == "chunk":
            chunks += ev.get("content") or ""
    return r.status_code, events, chunks


def parse_sse_body(body):
    events = []
    for line in body.split("\n"):
        if not line.startswith("data: "):
            continue
        try:
            events.append(json.loads(line[6:]))
        except json.JSONDecodeError:
            continue
    return events


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    token = generate_token(user)

    # 直接用真模型（app 已按 config 注入 deepseek），全程走 flask test_client
    c = app.test_client()
    c.set_cookie("auth_token", token)

    def stream_sse(path, payload):
        resp = c.post(path, json=payload)
        body = resp.data.decode("utf-8")
        events = parse_sse_body(body)
        chunks = "".join(e.get("content") or "" for e in events if e.get("type") == "chunk")
        return resp.status_code, events, chunks

    def atmosphere(conv_id, msg_count):
        try:
            resp = c.post(f"/api/atmosphere/{conv_id}",
                          json={"msg_count": msg_count, "is_push_cmd": False})
            return parse_sse_body(resp.data.decode("utf-8"))
        except Exception as e:
            stats["notes"].append(f"atmosphere 失败: {e}")
            return []

    conv = ""
    # ── 建会话 ──
    conv = (c.post("/api/history/new", json={}).get_json() or {}).get("id", "")
    locations.ensure_defaults(app_db, conv)
    check("新会话可以创建", bool(conv), conv)

    phones = 0
    chats = 0
    sticker_paths = []
    npc_seen = 0
    autonomy_acts = 0

    rng = random.Random(20261120)

    for i in range(1, ROUNDS + 1):
        stats["rounds"] = i
        line = LINES[(i - 1) % len(LINES)]
        # 每 7 轮换一点花样
        want_phone = (i % 7 == 3) and not app_db.get_npcs(conv)
        channel = "phone" if want_phone else None
        co = app_db.get_current_location(conv) == app_db.get_player_location(conv)

        # ── 中途插入的功能操作 ──
        if i == 8:
            # 搬到街上，叫 NPC
            locations.switch_both(app_db, conv, "街上")
            app_db.set_player_location(conv, "街上")
            r = c.post(f"/api/npcs/{conv}", json={"name": "水果摊老板", "role": "老板",
                                                  "location": "街上"})
            check("手动加 NPC", r.status_code == 200, str(r.status_code))
        if i == 9:
            line = "老板，这个苹果怎么卖"
        if i == 12:
            r = c.get(f"/api/npcs/{conv}")
            npcs = (r.get_json() or {}).get("npcs") or []
            check("NPC 列表可读", r.status_code == 200 and isinstance(npcs, list))
            npc_seen = len(npcs)
        if i == 15:
            # 玩家一个人走开 → 手机通道
            r = c.post(f"/api/move/{conv}", json={"who": "player", "location": "厨房"})
            scene = r.get_json() or {}
            check("玩家可以单独走开", scene.get("co_located") is False, str(scene.get("co_located")))
        if i == 16:
            line = "我在厨房，你过来吗"
        if i == 19:
            # 一起走：先把玩家挪到她身边（她可能自己动过），再试 both
            yloc = app_db.get_current_location(conv)
            c.post(f"/api/move/{conv}", json={"who": "player", "location": yloc})
            r = c.post(f"/api/move/{conv}", json={"who": "both", "location": yloc})
            got = r.get_json() or {}
            check("两人可以一起移动", got.get("co_located") is True,
                  f"{got.get('location')}/{got.get('player_location')}")
        if i == 22:
            # 拨时间
            r = c.post(f"/api/time/{conv}", json={"hour": 20, "minute": 30})
            check("拨时间可用", r.status_code == 200, str(r.status_code))
        if i == 25:
            r = c.get(f"/api/events/{conv}")
            ev = (r.get_json() or {}).get("events") or []
            check("故事进展有事件", len(ev) > 0, str(len(ev)))
            check("故事进展有摘要", bool((r.get_json() or {}).get("story_summary")))
        if i == 28:
            r = c.post(f"/api/pause/{conv}", json={"paused": True})
            check("可以暂停", (r.get_json() or {}).get("paused") is True)
            idle = c.post(f"/api/idle/{conv}").get_json() or {}
            check("暂停后不自主行动", idle.get("acted") is False and idle.get("paused") is True, str(idle))
            c.post(f"/api/pause/{conv}", json={"paused": False})
        if i == 31:
            r = c.post(f"/api/speed/{conv}", json={"speed": 5})
            check("速度可以调到 5", (r.get_json() or {}).get("speed") == 5, str(r.get_json()))
        if i == 34:
            r = c.post(f"/api/chat", json={"conversation_id": conv, "message": ""})
            check("空消息被拒", r.status_code == 400, str(r.status_code))
            r = c.post("/api/chat", json={"conversation_id": conv, "sticker": "upload/nope.png"})
            check("不存在的表情包被拒", r.status_code == 400, str(r.status_code))
        if i == 37:
            line = "/带 卧室"
        if i == 40:
            line = "/去 便利店"
        if i == 43:
            line = "/推进 门铃突然响了"
        if i == 46:
            # 通知接口
            r = c.get(f"/api/notify/pending?conversation_id={conv}&since_id=0")
            body = r.get_json() or {}
            check("通知接口可用", r.status_code == 200 and "messages" in body, str(r.status_code))
        if i == 50:
            # 表情包：库里有就直接用（不往用户的表情包目录里塞测试图），空库才上传一张自测
            lst = (c.get("/api/stickers/list?limit=5").get_json() or {})
            items = lst.get("items") or []
            check("表情包库可读", lst.get("total") is not None, str(lst)[:100])
            if not items:
                png = make_png(i)
                r = c.post("/api/stickers/upload", content_type="multipart/form-data",
                           data={"files": (io.BytesIO(png), f"selfcheck_{i}.png")})
                up = r.get_json() or {}
                check("空库时能上传一张自测表情包", up.get("uploaded_count") == 1, str(up)[:120])
                items = ((c.get("/api/stickers/list?limit=5").get_json() or {}).get("items")) or []
            sticker_paths = [it["path"] for it in items[:2]]
            check("能拿到表情包用于发送", bool(sticker_paths), str(sticker_paths))
        if i == 51 and sticker_paths:
            st, evs, chunks = stream_sse("/api/chat", {
                "conversation_id": conv, "sticker": sticker_paths[0]})
            stats["sticker_sent"] += 1
            check("能发一张表情包", st == 200 and any(e.get("type") == "done" for e in evs))
            time.sleep(0.2)
            continue
        if i == 55:
            # 自主行动接口
            r = c.post(f"/api/idle/{conv}")
            idle = r.get_json() or {}
            check("闲置接口可用", r.status_code == 200 and "acted" in idle, str(r.status_code))
            if idle.get("acted"):
                autonomy_acts += 1
        if i == 60:
            r = c.get(f"/api/phone/{conv}")
            check("手机接口可用", r.status_code == 200 and "messages" in (r.get_json() or {}))
        if i == 65:
            r = c.get(f"/api/history/{conv}")
            body = r.get_json() or {}
            check("历史接口可用", r.status_code == 200 and isinstance(body.get("messages"), list))
        if i == 70:
            r = c.get("/api/config")
            cfg = r.get_json() or {}
            check("客户端配置接口可用",
                  "mood_faces" in cfg and "speed_default" in cfg, str(list(cfg))[:120])

        # ── 发一条消息 ──
        t0 = time.time()
        status, events, chunks = stream_sse("/api/chat", {
            "conversation_id": conv, "message": line, "channel": channel} if channel
            else {"conversation_id": conv, "message": line})
        elapsed = time.time() - t0

        kinds = [e.get("type") for e in events]
        if status != 200 or "done" not in kinds:
            stats["http_fail"] += 1
            stats["failures"].append(f"第 {i} 轮 SSE 不完整: {status} {kinds[:5]}")
            print(f"  FAIL 第 {i} 轮 SSE: {status} {kinds[:5]}")
            continue
        if "conv_id" in kinds:
            conv = next((e["id"] for e in events if e.get("type") == "conv_id"), conv)

        done = next((e for e in events if e.get("type") == "done"), {})
        ch = done.get("channel") or "chat"
        if ch == "phone":
            stats["phone"] += 1
        else:
            stats["chat"] += 1
        if "npc" in kinds:
            stats["npc"] += 1
        if any(e.get("type") == "autonomy" for e in events):
            stats["autonomy"] += 1

        # 落库检查
        msgs = [m for m in app_db.get_messages(conv) if m["role"] == "assistant"]
        last = msgs[-1] if msgs else None
        if not last:
            stats["empty"] += 1
            stats["failures"].append(f"第 {i} 轮没有落库")
            continue
        content = last["content"] or ""
        last_channel = last.get("channel") or "chat"
        if re.match(r"^\s*(?:尤夏|青梅|她|我)\s*[:：]", content):
            stats["prefix"] += 1
            stats["failures"].append(f"第 {i} 轮出现说话人前缀: {content[:30]}")
        if last_channel != "chat":
            # 手机消息本来就是纯文本，不算兜底
            pass
        elif content.startswith("{"):
            try:
                obj = json.loads(content)
                mood = obj.get("心情")
                if mood:
                    stats["moods"][mood] = stats["moods"].get(mood, 0) + 1
                speech = obj.get("对白") or ""
                if speech and len(stats["her_lines"]) < 40:
                    stats["her_lines"].append((line, speech))
            except json.JSONDecodeError:
                stats["json_fail"] += 1
        elif content and not content.startswith(("API 错误", "错误:", "[生成失败", "无法连接")):
            stats["fallback"] += 1

        # 通道一致性
        co_now = app_db.get_current_location(conv) == app_db.get_player_location(conv)
        if co_now and ch == "phone" and not channel:
            stats["failures"].append(f"第 {i} 轮：同处一地却走了手机")

        # 世界引擎
        msg_count = done.get("msg_count") or i
        atmosphere(conv, msg_count)
        if i % 10 == 0:
            print(f"  第 {i} 轮 ok（{elapsed:.1f}s，{ch}）")

    # ── 收尾：删会话（另起一个） ──
    throwaway = (c.post("/api/history/new", json={}).get_json() or {}).get("id")
    r = c.delete(f"/api/history/{throwaway}")
    check("会话可以删除", r.status_code == 200, str(r.status_code))

    write_report()
    print()
    print(f"完成 {stats['rounds']} 轮：聊天 {stats['chat']} / 手机 {stats['phone']} / "
          f"NPC {stats['npc']} / 自主 {stats['autonomy'] + autonomy_acts}")
    print(f"失败项：{len(FAILED)}")
    return 0 if not FAILED else 1


def make_png(seed):
    import struct
    import zlib
    w = h = 24

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b""
    for y in range(h):
        raw += b"\x00" + bytes([(seed * 17 + x * 5 + y * 3) % 256 for x in range(w * 3)])
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def write_report():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    lines = ["# 100+ 轮试玩报告（扮演青梅）", ""]
    lines.append(f"- 轮数：**{stats['rounds']}**")
    lines.append(f"- 通道分布：当面 {stats['chat']} 轮 / 手机 {stats['phone']} 轮")
    lines.append(f"- 触发 NPC 回话：{stats['npc']} 次；自主行动：{stats['autonomy']} 次")
    lines.append(f"- 她没说 JSON（走兜底）：{stats['fallback']} 次；JSON 解析失败：{stats['json_fail']} 次")
    lines.append(f"- 说话人前缀：{stats['prefix']} 次")
    lines.append(f"- 心情分布：{stats['moods']}")
    lines.append("")
    lines.append(f"## 失败项（{len(FAILED)}）")
    for f in stats["failures"][:40]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## 她说过的话（抽样）")
    for mine, hers in stats["her_lines"][:24]:
        lines.append(f"- 青梅：{mine}")
        lines.append(f"  尤夏：{hers}")
    with io.open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
