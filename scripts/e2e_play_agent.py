"""
按「独立子 agent 写的剧本」做一轮完整试玩（真模型）

剧本由另一个上下文独立生成（它只知道人物与场景，不知道实现）：
  .testtmp/playtest/script.json
本脚本负责把它演出来，并在每一轮做结构与一致性断言。

覆盖：
1. 网页端（Cookie 登录、/api/chat + /api/atmosphere 两条流）
2. 手机端（Bearer token、X-DeepSeek-API-Key、history/phone/scene/ping）
3. 说话 / 玩家动作（/动作）/ 换地方（/去）/ 带人走（/带）
4. 真实时间时钟、日程与睡眠状态、异地不播报旁白、口头禅限流、JSON 结构、空回复

用法：python scripts/e2e_play_agent.py [剧本路径]
报告：.testtmp/playtest/report_agent.md
"""
import datetime as _dt
import hashlib
import json
import os
import secrets
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

TMP = os.path.join(ROOT, ".testtmp", "playtest")
os.makedirs(TMP, exist_ok=True)
DB_DIR = os.path.join(TMP, "db_" + uuid.uuid4().hex[:6])
os.makedirs(DB_DIR, exist_ok=True)
database.DB_PATH = os.path.join(DB_DIR, "chat.db")

from app import app, db as app_db            # noqa: E402
import core.locations as locations           # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402

SCRIPT = os.path.join(TMP, "script.json")
if len(sys.argv) > 1:
    SCRIPT = sys.argv[1]
REPORT = os.path.join(TMP, "report_agent.md")

# 中途模拟的时间点（故事分钟）：早上 7:25 她还在睡、之后按时推进到深夜
TIME_HOPS = [
    (1, 7 * 60 + 25),      # 开场：清晨，她还在睡（脚本第一句就是叫她起床）
    (8, 7 * 60 + 40),      # 她起了
    (16, 12 * 60 + 20),    # 午饭
    (23, 13 * 60 + 20),    # 午休
    (33, 18 * 60 + 10),    # 晚饭
    (43, 22 * 60 + 10),    # 晚上
    (47, 23 * 60 + 40),    # 深夜：她睡了，测叫醒
]

FAILED = []
ROWS = []
TICS_PREV = False
SIM = {"minutes": None, "at": 0.0}


def check(name, cond, extra=""):
    if not cond:
        FAILED.append(f"{name}  {extra}".strip())
        print(f"  FAIL {name}  {extra}")
    return bool(cond)


def sse_events(body):
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return out


def chat_payload(turn):
    kind = turn.get("kind")
    if kind == "say":
        return turn.get("text", ""), False
    if kind == "act":
        return "/动作 " + turn.get("text", ""), False
    if kind == "move":
        return "/去 " + turn.get("place", ""), True
    if kind == "bring":
        return "/带 " + turn.get("place", ""), True
    return turn.get("text", ""), False


def parse_jsonish(text):
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def make_clients():
    """一个网页端（Cookie）一个手机端（Bearer）"""
    name = "play_" + secrets.token_hex(3)
    password = secrets.token_urlsafe(12)
    other = "play_other_" + secrets.token_hex(3)
    app_db.sync_accounts({
        name: hashlib.sha256(password.encode()).hexdigest(),
        other: hashlib.sha256(b"x").hexdigest(),
    })
    uid = app_db.get_user(name)["id"]

    web = app.test_client()
    r = web.post("/api/auth/login", json={"username": name, "password": password,
                                          "remember": True})
    login = r.get_json() or {}
    token = login.get("token") or ""
    check("网页端登录", r.status_code == 200 and login.get("success") is True, str(login))

    mobile = app.test_client()
    mobile.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    mobile.environ_base["HTTP_X_DEEPSEEK_API_KEY"] = __import__("config").DEEPSEEK_API_KEY
    return web, mobile, uid, token


def mobile_contract(mobile, conv):
    """手机端各接口的基本契约（App 真正会调的）"""
    r = mobile.get("/api/ping")
    ping = r.get_json() or {}
    check("手机端 /api/ping 可用", r.status_code == 200 and ping.get("ok") is True, str(r.status_code))
    check("ping 带版本字段", "app_version" in ping and "build_sha" in ping, str(ping))

    r = mobile.get("/api/history")
    check("手机端会话列表", r.status_code == 200 and isinstance(r.get_json(), list), str(r.status_code))

    r = mobile.get(f"/api/history/{conv}")
    hist = r.get_json() or {}
    check("手机端读历史", r.status_code == 200 and "messages" in hist, str(r.status_code))

    r = mobile.get(f"/api/phone/{conv}")
    phone = r.get_json() or {}
    check("手机端手机面板", r.status_code == 200 and "messages" in phone, str(r.status_code))
    db_phone = [m["content"] for m in app_db.get_messages(conv) if m["channel"] == "phone"]
    listed = [m.get("content") for m in phone.get("messages", [])]
    check("手机面板与库里手机消息一致",
          listed == db_phone[-len(listed):] if listed else not db_phone,
          f"{len(listed)} vs {len(db_phone)}")

    r = mobile.post(f"/api/phone/read/{conv}")
    check("手机端标已读", r.status_code == 200, str(r.status_code))

    r = mobile.get(f"/api/scene/{conv}")
    scene = r.get_json() or {}
    check("手机端场景", r.status_code == 200 and "co_located" in scene, str(r.status_code))

    anon = app.test_client().get("/api/scene/" + conv)
    check("无凭证访问被拒 (401)", anon.status_code == 401, str(anon.status_code))


def set_time(minutes, note=""):
    app_db.set_story_minutes(conv_id, minutes)
    SIM["minutes"] = int(minutes)
    SIM["at"] = time.time()
    print(f"  · 模拟时间 -> {minutes // 60:02d}:{minutes % 60:02d} {note}")


def read_state():
    return {
        "co_located": chat_mod._co_located(conv_id),
        "yuxia_loc": app_db.get_current_location(conv_id),
        "player_loc": app_db.get_player_location(conv_id),
        "state": app_db.get_yuxia_state(conv_id),
        "story_minutes": int(app_db.get_story_minutes(conv_id) or 0),
    }


def turn_checks(idx, turn, text, client_label, stream_body, atm_body, msgs_before):
    global TICS_PREV
    events = sse_events(stream_body)
    types = [e.get("type") for e in events]
    done = next((e for e in events if e.get("type") == "done"), {})
    check(f"#{idx} 流以 done 结束", bool(done), str(types[-3:]))

    msgs_after = app_db.get_messages(conv_id)
    new_rows = msgs_after[msgs_before:]
    assistant_rows = [m for m in new_rows if m["role"] == "assistant"]
    check(f"#{idx} 她这一轮有输出", bool(assistant_rows), str(new_rows))

    # 结构：当面通道必须是 JSON 对象
    bad_json = []
    for row in assistant_rows:
        if row["channel"] != "chat":
            continue
        obj = parse_jsonish(row["content"])
        if obj is None:
            bad_json.append(row["content"][:40])
    check(f"#{idx} 对白是合法 JSON", not bad_json, str(bad_json[:2]))

    spoken = []
    actions = []
    for row in assistant_rows:
        obj = parse_jsonish(row["content"])
        if obj and obj.get("对白"):
            spoken.append(str(obj["对白"]))
        if row["channel"] == "action":
            actions.append(row["content"])
        if row["channel"] == "phone":
            spoken.append(row["content"])

    joined = " ".join(spoken)
    check(f"#{idx} 没有说话人前缀", "尤夏：" not in joined and "尤夏:" not in joined, joined[:40])
    check(f"#{idx} 没有「喵」", "喵" not in joined, joined[:40])
    check(f"#{idx} 没有叠字口癖", "唔姆唔姆" not in joined and "嗯嗯" not in joined, joined[:40])

    # 口头禅限流：本轮最多一个，且不连续两轮
    tic_hits = joined.count("唔姆")
    check(f"#{idx} 一轮最多一个「唔姆」", tic_hits <= 1, str(tic_hits))
    if TICS_PREV:
        check(f"#{idx} 上一轮用了这轮就不再用", tic_hits == 0, str(tic_hits))
    TICS_PREV = tic_hits > 0

    # 异地的旁白不该出现
    state_events = [e for e in events if e.get("type") == "yuxia_state"]
    apart = not done.get("co_located", True)
    if apart:
        check(f"#{idx} 异地不播报她的神态/睡醒", not state_events, str(state_events[:1]))
        check(f"#{idx} 异地回复走手机通道",
              done.get("channel") == "phone" or not spoken, str(done.get("channel")))

    # 世界引擎（网页/手机都会单独拉这条流）
    atm = sse_events(atm_body)
    check(f"#{idx} 世界流正常结束", any(e.get("type") == "done" for e in atm),
          str([e.get("type") for e in atm][-4:]))

    # 故事时间：模拟过的轮次贴着模拟值，没模拟的轮次贴着真实钟点
    st = done.get("story_time") or ""
    shown = None
    if st and ":" in st:
        hh, mm = st.rsplit(" ", 1)[-1].split(":")[:2]
        shown = int(hh) * 60 + int(mm)
    if shown is not None and SIM["minutes"] is not None:
        elapsed = int((time.time() - SIM["at"]) // 60)
        expected = (SIM["minutes"] + elapsed) % 1440
        check(f"#{idx} 故事时间跟着模拟时间走", abs(shown - expected) <= 3,
              f"{st} vs {expected // 60:02d}:{expected % 60:02d}")
    elif shown is not None:
        expected = _dt.datetime.now().hour * 60 + _dt.datetime.now().minute
        check(f"#{idx} 故事时间对齐真实钟点", abs(shown - expected) <= 2,
              f"{st} vs {expected // 60:02d}:{expected % 60:02d}")

    return {
        "idx": idx, "kind": turn.get("kind"), "client": client_label,
        "text": text, "events": types, "channel": done.get("channel"),
        "co_located": done.get("co_located"), "story_time": st,
        "spoken": spoken, "actions": actions, "state": read_state()["state"],
        "locations": read_state()["yuxia_loc"] + " / " + read_state()["player_loc"],
    }


def main():
    global conv_id
    app.config["TESTING"] = True
    if not os.path.exists(SCRIPT):
        print(f"剧本还没生成：{SCRIPT}")
        return 2
    with open(SCRIPT, encoding="utf-8") as f:
        script = json.load(f)
    turns = script.get("turns") or []
    limit = int(os.environ.get("PLAY_LIMIT", "0") or 0)
    if limit:
        turns = turns[:limit]
        print(f"（只跑前 {limit} 轮，用于冒烟）")
    print(f"剧本共 {len(turns)} 轮：{SCRIPT}")

    web, mobile, uid, token = make_clients()
    r = web.post("/api/history/new", json={})
    conv_id = (r.get_json() or {}).get("id", "")
    check("新建会话", bool(conv_id), str(r.get_json()))
    locations.ensure_defaults(app_db, conv_id)
    locations.switch_both(app_db, conv_id, "公寓客厅")
    mobile_contract(mobile, conv_id)

    hops = dict(TIME_HOPS)
    extra_checks = {}
    for idx, turn in enumerate(turns, start=1):
        if idx in hops:
            set_time(hops[idx])
        text, _is_move = chat_payload(turn)
        use_mobile = idx % 2 == 0            # 一半走手机端、一半走网页端
        client = mobile if use_mobile else web
        label = "手机" if use_mobile else "网页"

        # 手机端每轮都会带 X-DeepSeek-API-Key；网页端用服务端 config 里的 key
        msgs_before = len(app_db.get_messages(conv_id))
        was_asleep = app_db.get_yuxia_state(conv_id) == "睡眠"
        t0 = time.time()
        r = client.post("/api/chat", json={"conversation_id": conv_id, "message": text})
        body = r.data.decode("utf-8")
        check(f"#{idx} /api/chat 200", r.status_code == 200, str(r.status_code))
        events = sse_events(body)
        done = next((e for e in events if e.get("type") == "done"), {})
        if was_asleep and done.get("co_located"):
            check(f"#{idx} 睡着被叫起会播报睁眼",
                  any(e.get("type") == "yuxia_state" for e in events),
                  str([e.get("content", "")[:12] for e in events
                       if e.get("type") == "yuxia_state"]))
        atm = client.post(f"/api/atmosphere/{conv_id}",
                          json={"msg_count": msgs_before,
                                "is_push_cmd": bool(done.get("is_push_cmd"))})
        atm_body = atm.data.decode("utf-8")
        check(f"#{idx} /api/atmosphere 200", atm.status_code == 200, str(atm.status_code))
        row = turn_checks(idx, turn, text, label, body, atm_body, msgs_before)
        row["seconds"] = round(time.time() - t0, 1)
        ROWS.append(row)
        print(f"#{idx:02d} [{label}/{turn.get('kind')}] {text[:24]!r} "
              f"channel={row['channel']} loc={row['locations']} "
              f"{row['seconds']}s 说{len(row['spoken'])} 动{len(row['actions'])}")

        # 每几轮做一次跨端一致性检查
        if idx % 7 == 0:
            hw = web.get(f"/api/history/{conv_id}").get_json() or {}
            hm = mobile.get(f"/api/history/{conv_id}").get_json() or {}
            check(f"#{idx} 网页与手机看到同一份历史",
                  len(hw.get("messages") or []) == len(hm.get("messages") or []),
                  f"{len(hw.get('messages') or [])} vs {len(hm.get('messages') or [])}")
            check(f"#{idx} 两端地点一致",
                  hw.get("location") == hm.get("location"), str(hw.get("location")))

    # ── 深夜：同处一地点，叫醒她 -> 应该出现「睡着了 / 睁眼」这类旁白
    print("\n== 深夜叫醒 ==")
    locations.switch_both(app_db, conv_id, "公寓客厅")     # 先回到同一个地方
    set_time(23 * 60 + 40, "（睡眠中）")
    app_db.set_yuxia_state(conv_id, "睡眠")
    before = len(app_db.get_messages(conv_id))
    r = web.post("/api/chat", json={"conversation_id": conv_id, "message": "尤夏，睡着了？"})
    evs = sse_events(r.data.decode("utf-8"))
    check("同处一地叫醒她会播报睡醒",
          any(e.get("type") == "yuxia_state" for e in evs),
          str([e.get("content", "")[:16] for e in evs if e.get("type") == "yuxia_state"]))
    check("叫醒后她照常回话", len(app_db.get_messages(conv_id)) > before + 1)

    # ── 异地：她在客厅、你在厨房，同样的状态不应该播报
    print("\n== 异地不播报 ==")
    app_db.set_player_location(conv_id, "厨房")
    check("换到厨房（异地）", not chat_mod._co_located(conv_id),
          f"{app_db.get_current_location(conv_id)} / {app_db.get_player_location(conv_id)}")
    app_db.set_yuxia_state(conv_id, "睡眠")
    before = len(app_db.get_messages(conv_id))
    r = mobile.post("/api/chat", json={"conversation_id": conv_id, "message": "睡着了吗"})
    evs = sse_events(r.data.decode("utf-8"))
    check("异地叫她不播报睡醒", not any(e.get("type") == "yuxia_state" for e in evs), str(evs)[:120])
    new_state_rows = [m for m in app_db.get_messages(conv_id)[before:]
                      if m["channel"] == "state"]
    check("异地不往库里写状态旁白", not new_state_rows,
          str([m["content"] for m in new_state_rows]))

    # ── 手动拨时钟已被禁用
    r = web.post(f"/api/time/{conv_id}", json={"hour": 18, "minute": 0})
    check("手动拨时钟被拒", r.status_code == 400, str(r.status_code))

    # ── 手机端最终契约
    mobile_contract(mobile, conv_id)
    print("\n== 手机端接口契约 ==")
    r = mobile.get(f"/api/phone/{conv_id}")
    phone_msgs = (r.get_json() or {}).get("messages") or []
    check("手机面板里有她的消息", bool(phone_msgs), str(len(phone_msgs)))
    r = mobile.post(f"/api/phone/read/{conv_id}")
    check("已读后未读归零", (r.get_json() or {}).get("unread") == 0, str(r.get_json()))

    write_report(turns)
    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项")
        for f in FAILED:
            print("  -", f)
        return 1
    print(f"全部通过（{len(turns)} 轮 + 深夜/异地专项）")
    return 0


def write_report(turns):
    total = len(ROWS)
    tic_turns = sum(1 for r in ROWS if any("唔姆" in s for s in r["spoken"]))
    action_turns = sum(1 for r in ROWS if r["actions"])
    phone_turns = sum(1 for r in ROWS if r["channel"] == "phone")
    lines = [
        "# 试玩报告（独立剧本 · 真模型）",
        "",
        f"- 剧本轮数：{total}",
        f"- 出现「唔姆」的轮次：{tic_turns}（{round(100 * tic_turns / max(total, 1))}%）",
        f"- 她做出动作的轮次：{action_turns}",
        f"- 走手机通道的轮次：{phone_turns}",
        f"- 失败断言：{len(FAILED)}",
        "",
        "## 逐轮",
        "",
        "| # | 类型 | 客户端 | 通道 | 地点（她/你） | 状态 | 时间 | 说 | 动 | 秒 | 内容 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in ROWS:
        text = " ".join(r["spoken"])[:34].replace("|", "／")
        lines.append(
            f"| {r['idx']} | {r['kind']} | {r['client']} | {r['channel']} | "
            f"{r['locations']} | {r.get('state') or ''} | {r['story_time']} | "
            f"{len(r['spoken'])} | {len(r['actions'])} | {r['seconds']} | {text} |")
    if FAILED:
        lines += ["", "## 失败项", ""] + [f"- {f}" for f in FAILED]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告：{REPORT}")


if __name__ == "__main__":
    sys.exit(main())
