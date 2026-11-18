"""
聊天路由 v4

主要变化：
- 尤夏的输出只剩 对白 / 消息 / 表情包，动作与环境交给系统
- 消息分两条通道：当面（chat）与手机（phone）；不同处一地时只能手机
- 环境描写绑定「地点 + 时段」，只在进入新场景时切换
- 上下文由上层 LLM 写成自然叙述，不塞结构化标签
- 自主行动：尤夏会主动说话、发消息、甚至自己换场景
"""
import json
import os
import re
import time

from flask import Blueprint, request, Response, jsonify

import core.clock as clock
import core.locations as locations
import core.scenes as scenes
import core.stickers as stickers
from core.auth import require_auth, ensure_conversation_owner, request_api_key
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from services import autonomy, context_writer, memory_engine, time_keeper
from services.event_engine import (
    _run_event_summary, _run_random_event,
    should_run_random_event, should_run_event_summary,
)
from config import CHAT_PROMPT, CHARACTER_NAME, CHAT_CHANNEL, PHONE_CHANNEL

chat_bp = Blueprint("chat", __name__)
db = None        # 由外部注入
deepseek = None  # 由外部注入

_PUSH_CMD_RE = re.compile(r"^/推进\s*(.*)")
_MOVE_CMD_RE = re.compile(r"^/去\s*(.+)")


def _sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 分段计时（设置环境变量 YUXIA_TIMING=1 时打开，用于排查首轮延迟）
_TIMING = bool(os.environ.get("YUXIA_TIMING"))
_T0 = {"t": 0.0}


def _mark(label):
    if not _TIMING:
        return
    now = time.time()
    if not _T0["t"]:
        _T0["t"] = now
        print(f"[timing] {label}: 0 ms (起点)")
        return
    print(f"[timing] {label}: {int((now - _T0['t']) * 1000)} ms")


def _extract_json_objects(text):
    """按括号配对提取模型输出里的 JSON 对象（容忍折行、多余文字）"""
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return out


def _co_located(conv_id):
    return db.get_player_location(conv_id) == db.get_current_location(conv_id)


def _scene_payload(conv_id, api_key):
    now = time_keeper.init_clock(db, conv_id)
    view = scenes.scene_view(db, conv_id, locations.get_current(db, conv_id), now,
                             deepseek, api_key=api_key)
    view["locations"] = locations.list_names(db, conv_id)
    view["story_time"] = clock.format_time(now)
    view["unread"] = db.get_phone_unread(conv_id)
    return view


# ──────────────────────────────────────────────
# 主聊天
# ──────────────────────────────────────────────

@chat_bp.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.get_json() or {}
    conv_id = data.get("conversation_id", "")
    user_message = (data.get("message") or "").strip()
    user_id = request.user_id
    api_key = request_api_key()
    if not user_message:
        return jsonify({"error": "消息不能为空"}), 400

    _T0["t"] = 0.0
    _mark("请求进入")

    if not conv_id or not db.conversation_belongs_to(conv_id, user_id):
        conv_id = db.create_conversation(user_id)

    locations.ensure_defaults(db, conv_id)
    memory_engine.ensure_backfill(db, conv_id, time_keeper.init_clock(db, conv_id))
    _mark("会话/地点初始化")

    is_push_cmd = False
    push_match = _PUSH_CMD_RE.match(user_message)
    if push_match:
        is_push_cmd = True
        hint = push_match.group(1).strip()
        user_message = hint if hint else "（青梅轻轻戳了戳尤夏的肩膀，似乎在期待什么）"

    # 玩家主动移动：/去 地点
    moved = False
    move_match = _MOVE_CMD_RE.match(user_message)
    if move_match:
        target, created = locations.resolve(db, conv_id, move_match.group(1))
        if target:
            db.set_player_location(conv_id, target)
            user_message = f"（青梅走到了{target}）"
            moved = True

    yuxia_loc = db.get_current_location(conv_id)
    player_loc = db.get_player_location(conv_id)
    co_located = player_loc == yuxia_loc
    # 玩家可以指定用手机发（即使同处一地），否则按是否同处一地决定
    requested = (data.get("channel") or "").strip()
    channel = requested if requested in (CHAT_CHANNEL, PHONE_CHANNEL) else (
        CHAT_CHANNEL if co_located else PHONE_CHANNEL)
    can_speak = channel == CHAT_CHANNEL

    db.add_message(conv_id, "user", user_message, channel=channel, scene=player_loc)
    db.touch_conversation(conv_id)
    db.set_last_user_at(conv_id, time.time())

    all_messages = db.get_messages(conv_id)
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_conversation_title(conv_id, user_message[:20] + ("..." if len(user_message) > 20 else ""))

    now_minutes, keyword_jump = time_keeper.apply_message_tick(db, conv_id, user_message)
    story_time = clock.format_time(now_minutes)
    msg_count = db.get_message_count(conv_id)
    _mark("时钟推进")

    # 预热当前场景的环境描写（后台线程，不阻塞回复）：让前端首屏更快拿到文字
    try:
        scenes.ensure_description(db, conv_id, yuxia_loc, now_minutes, deepseek,
                                  api_key, background=True)
        if player_loc != yuxia_loc:
            scenes.ensure_description(db, conv_id, player_loc, now_minutes, deepseek,
                                      api_key, background=True)
    except Exception as e:
        print(f"[场景] 预热失败: {e}")

    memory_engine.remember_user_message(db, conv_id, user_message, now_minutes)
    _mark("记忆写入")

    # 自然情境（上层 LLM 撰写 + 缓存）
    brief = context_writer.compose(db, conv_id, now_minutes, yuxia_loc, player_loc,
                                   deepseek, api_key=api_key, turn=msg_count,
                                   force=moved or bool(keyword_jump))
    _mark("自然情境撰写")

    channel_rule = ("你们现在在同一个地方，可以当面说话。" if can_speak
                    else "你们现在不在面对面交流，只能用手机发消息，不要输出对白。")
    system_prompt = f"{CHAT_PROMPT}\n\n{channel_rule}"
    context_messages = db.get_messages(conv_id, channel=channel)[-6:]
    _mark("提示词组装完成（准备开始流式）")

    def generate():
        full = ""
        first_chunk_at = [None]
        yield _sse({"type": "conv_id", "id": conv_id})
        yield _sse({"type": "story_time", "content": story_time,
                    "jump": keyword_jump, "reason": "关键词规则" if keyword_jump else ""})
        if moved:
            yield _sse({"type": "scene_changed",
                        "scene": _scene_payload(conv_id, api_key)})

        for chunk in deepseek.chat_stream(context_messages, system_prompt,
                                          api_key_override=api_key):
            if first_chunk_at[0] is None:
                first_chunk_at[0] = time.time()
                _mark("AI A 首个 token")
            full += chunk
            yield _sse({"type": "chunk", "content": chunk})

        _mark("AI A 输出完成")
        _persist_reply(conv_id, full, can_speak, yuxia_loc, now_minutes, channel)
        yield _sse({"type": "done", "conv_id": conv_id, "msg_count": msg_count,
                    "is_push_cmd": is_push_cmd, "channel": channel,
                    "co_located": co_located, "story_time": story_time})

    return Response(generate(), mimetype="text/event-stream")


def _persist_reply(conv_id, full, can_speak, yuxia_loc, now_minutes, channel=CHAT_CHANNEL):
    """把模型输出拆成 对白/表情包（当面）与 消息（手机）分别落库"""
    clean = (full or "").strip()
    if not clean:
        return

    objects = _extract_json_objects(clean)
    if not objects:
        # 模型没按 JSON 输出（或报错）：按当前通道原样存一条，前端照常显示
        db.add_message(conv_id, "assistant", clean,
                       channel=PHONE_CHANNEL if not can_speak else channel,
                       scene=yuxia_loc)
        if not clean.startswith(("API 错误", "错误:", "无法连接", "[生成失败")):
            memory_engine.remember_assistant_message(db, conv_id, clean, now_minutes)
        return

    speech, stickers_used, phone_msgs = [], [], []
    for obj in objects:
        if obj.get("对白") and can_speak:
            speech.append(str(obj["对白"]).strip())
        if obj.get("表情包"):
            stickers_used.append(str(obj["表情包"]).strip())
        if obj.get("消息"):
            phone_msgs.append(str(obj["消息"]).strip())

    if speech or stickers_used:
        payload = {}
        if speech:
            payload["对白"] = "\n".join(speech)
        if stickers_used:
            payload["表情包"] = ",".join(stickers_used)
        db.add_message(conv_id, "assistant", json.dumps(payload, ensure_ascii=False),
                       channel=CHAT_CHANNEL, scene=yuxia_loc)

    for msg in phone_msgs:
        db.add_message(conv_id, "assistant", msg, channel=PHONE_CHANNEL, scene=yuxia_loc)

    readable = " ".join(speech + [f"（手机）{m}" for m in phone_msgs]).strip()
    if readable:
        memory_engine.remember_assistant_message(db, conv_id, readable, now_minutes)


# ──────────────────────────────────────────────
# 场景 / 地点 / 手机
# ──────────────────────────────────────────────

@chat_bp.route("/api/scene/<conv_id>", methods=["GET"])
@require_auth
def get_scene(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    return jsonify(_scene_payload(conv_id, request_api_key()))


@chat_bp.route("/api/move/<conv_id>", methods=["POST"])
@require_auth
def move(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    who = data.get("who", "player")
    target = (data.get("location") or "").strip()
    if not target:
        return jsonify({"error": "缺少目标地点"}), 400

    name, created = locations.resolve(db, conv_id, target)
    if not name:
        return jsonify({"error": "地点名不合法"}), 400

    if who == "yuxia":
        locations.switch(db, conv_id, name)
    else:
        db.set_player_location(conv_id, name)

    payload = _scene_payload(conv_id, request_api_key())
    payload["created"] = created
    return jsonify(payload)


@chat_bp.route("/api/phone/<conv_id>", methods=["GET"])
@require_auth
def get_phone(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    return jsonify({
        "messages": db.get_phone_messages(conv_id),
        "unread": db.get_phone_unread(conv_id),
        "co_located": _co_located(conv_id),
    })


@chat_bp.route("/api/phone/read/<conv_id>", methods=["POST"])
@require_auth
def phone_read(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    db.mark_phone_read(conv_id)
    return jsonify({"success": True, "unread": 0})


@chat_bp.route("/api/idle/<conv_id>", methods=["POST"])
@require_auth
def idle(conv_id):
    """玩家长时间没说话时，给尤夏一次自主行动机会"""
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    api_key = request_api_key()
    now = time_keeper.init_clock(db, conv_id)
    brief, _turn = db.get_context_brief(conv_id)
    if not brief:
        brief = context_writer.compose(
            db, conv_id, now, locations.get_current(db, conv_id),
            db.get_player_location(conv_id), deepseek, api_key=api_key, turn=0)

    try:
        msg_count = db.get_message_count(conv_id)
    except Exception:
        msg_count = 0

    acted = autonomy.maybe_act(db, conv_id, deepseek, now, brief,
                               api_key=api_key, msg_count=msg_count, idle=True)
    payload = {"acted": bool(acted), "action": (acted or {}).get("action"),
               "text": (acted or {}).get("text", ""),
               "channel": (acted or {}).get("channel", ""),
               "location": (acted or {}).get("location", ""),
               "unread": db.get_phone_unread(conv_id)}
    if acted and acted.get("action") == "move":
        payload["scene"] = _scene_payload(conv_id, api_key)
    return jsonify(payload)


# ──────────────────────────────────────────────
# 异步世界引擎（时间观察者 / 随机事件 / 事件总结 / 反思 / 自主行动）
# ──────────────────────────────────────────────

@chat_bp.route("/api/atmosphere/<conv_id>", methods=["POST"])
@require_auth
def atmosphere(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    is_push_cmd = data.get("is_push_cmd", False)
    msg_count = data.get("msg_count", 0)
    api_key = request_api_key()

    def generate():
        yield _sse({"type": "start"})
        all_messages = db.get_messages(conv_id)
        now = time_keeper.init_clock(db, conv_id)

        if not is_push_cmd and time_keeper.should_observe(msg_count):
            recent = "\n".join(
                f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}"
                f"（{'手机' if m.get('channel') == 'phone' else '当面'}）：{m['content']}"
                for m in all_messages[-6:]
            )
            jump = time_keeper.observe(db, conv_id, deepseek, recent, api_key=api_key)
            if jump:
                now = time_keeper.init_clock(db, conv_id)
                yield _sse({"type": "story_time", "content": clock.format_time(now),
                            "jump": jump["jump"], "reason": jump.get("reason", ""),
                            "from": jump["from"], "to": jump["to"]})

        if should_run_random_event(msg_count, is_push_cmd):
            yield _sse({"type": "event_organizing", "hint": "事件生成中..."})
            random_event = _run_random_event(conv_id, db, deepseek,
                                             now_minutes=now, api_key=api_key)
            if random_event:
                yield _sse({"type": "world_narration",
                            "content": random_event.get("description", ""),
                            "intensity": random_event.get("intensity", "low")})

        if should_run_event_summary(conv_id, db, is_push_cmd, msg_count, all_messages[-5:]):
            yield _sse({"type": "event_organizing", "hint": "事件总结中..."})
            summary = _run_event_summary(conv_id, db, deepseek, force_push=is_push_cmd,
                                         api_key=api_key, now_minutes=now)
            if summary:
                memory_engine.remember_events(db, conv_id, summary.get("events"), now)
                if summary.get("story_summary"):
                    yield _sse({"type": "story_summary", "content": summary["story_summary"]})
                yield _sse({"type": "event_update",
                            "events": db.get_events(conv_id, limit=50),
                            "has_push": summary.get("should_push", False),
                            "push_hint": summary.get("push_hint", "")})

        reflections = memory_engine.maybe_reflect(db, conv_id, deepseek, now, api_key=api_key)
        if reflections:
            yield _sse({"type": "reflections", "items": reflections})

        brief, _turn = db.get_context_brief(conv_id)
        acted = autonomy.maybe_act(db, conv_id, deepseek, now, brief or "",
                                   api_key=api_key, msg_count=msg_count, idle=False)
        if acted:
            if acted["action"] == "move":
                yield _sse({"type": "scene_changed",
                            "scene": _scene_payload(conv_id, api_key)})
                yield _sse({"type": "autonomy", "action": "move",
                            "content": f"尤夏去了{acted['location']}"})
            else:
                yield _sse({"type": "autonomy", "action": acted["action"],
                            "content": acted["text"], "channel": acted.get("channel", "")})

        yield _sse({"type": "done"})

    return Response(generate(), mimetype="text/event-stream")


# ──────────────────────────────────────────────
# 表情包
# ──────────────────────────────────────────────

@chat_bp.route("/api/stickers/refresh", methods=["POST"])
@require_auth
def refresh_stickers():
    stats = stickers.refresh(db)
    stats["tagged_now"] = stickers.auto_tag_all(db)
    stats["embedded"] = stickers.rebuild_embeddings(db)
    return jsonify({"success": True, **stats})


@chat_bp.route("/api/stickers/match")
def match_sticker():
    intent = request.args.get("q", "")
    results = stickers.match(db, intent, top_k=int(request.args.get("k", 3)))
    return jsonify({
        "intent": intent,
        "matches": results,
        "fallback": all(m["score"] <= 0 for m in results) if results else True,
    })


@chat_bp.route("/api/stickers/stats")
def sticker_stats():
    return jsonify({
        "total": db.count_stickers(),
        "tagged": db.count_stickers(tagged_only=True),
        "emotions": db.get_sticker_emotions(),
    })
