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
from services import (
    autonomy, character_writer, context_writer, memory_engine, npc, scene_watcher, time_keeper,
    world_context,
)
from services.event_engine import (
    _run_event_summary, _run_random_event,
    should_run_random_event, should_run_event_summary,
)
from config import (
    CHAT_PROMPT, CHARACTER_NAME, CHAT_CHANNEL, PHONE_CHANNEL, NPC_CAP,
    STICKER_UPLOAD_MAX_FILES, STICKER_UPLOAD_MAX_MB,
    MOOD_FACES, MOOD_DEFAULT, AUTONOMY_SPEED_DEFAULT,
    AUTONOMY_SPEED_MIN, AUTONOMY_SPEED_MAX,
)

chat_bp = Blueprint("chat", __name__)
db = None        # 由外部注入
deepseek = None  # 由外部注入

_PUSH_CMD_RE = re.compile(r"^/推进\s*(.*)")
_MOVE_CMD_RE = re.compile(r"^/去\s*(.+)")
_BRING_CMD_RE = re.compile(r"^/带\s*(.+)")


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


_CURLY_QUOTES = str.maketrans({"“": '"', "”": '"'})
# 引号修好之后，还要处理"中文冒号"：{"消息"： "..."}
_COLON_AFTER_QUOTE = re.compile(r'(?<=["])\s*[：:]\s*')

# 模型偶尔会自己加个说话人前缀（"尤夏：……别揉。"）。台词里不该有名字和冒号，
# 而且一旦它这么写，多半整段都不是合法 JSON，落到兜底路径就直接显示了。
_SPEAKER_PREFIX = re.compile(r"^\s*(?:尤夏|青梅|她|我|对方|旁白)\s*[:：]\s*")


def _strip_speaker(text):
    """去掉台词开头的「尤夏：」这类前缀（只剥一层，不动正文）"""
    return _SPEAKER_PREFIX.sub("", (text or "").strip(), count=1).strip()


def _normalize_quotes(text):
    """模型偶尔会用中文引号和中文冒号当 JSON 语法（{“对白”：“……”}），先掰回来再解析"""
    out = (text or "").translate(_CURLY_QUOTES)
    return _COLON_AFTER_QUOTE.sub(": ", out)


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


def _reset_brief(conv_id):
    """地点变了，缓存的情境叙述就过期了（它还写着"你在客厅"）"""
    try:
        db.set_context_brief(conv_id, "", 0)
    except Exception:
        pass


def _log_move(conv_id, who, from_location, to_location, created=False):
    """换地方要留痕：她下一轮才知道人走了、还是跟过来了"""
    try:
        story_time = clock.format_time(time_keeper.init_clock(db, conv_id))
    except Exception:
        story_time = ""
    tier = world_context.SELF_TIER if who == "yuxia" else ""
    moved = locations.log_move(db, conv_id, who, from_location, to_location,
                               story_time, created=created, tier=tier)
    if moved and who in ("yuxia", "both"):
        # 她换了地方，之前"你此刻在做"的事就作废了（别再说自己在原来那屋煮面）
        try:
            db.clear_yuxia_activity(conv_id)
        except Exception:
            pass
    if moved:
        _reset_brief(conv_id)
    return moved


def _stored_user_content(sticker_path, sticker_desc, text):
    """玩家发表情包时，库里存成一条图片消息（前端/App 渲染成图）；普通消息原样存"""
    if not sticker_path:
        return text
    return json.dumps({
        "表情包": sticker_path,
        "url": stickers.url_for(sticker_path),
        "desc": sticker_desc,
    }, ensure_ascii=False)


def _parse_sticker_message(content):
    """图片消息（{"表情包": 路径, "url": ...}）→ dict；不是就返回 None

    注意她自己的 JSON 里也有「表情包」字段（存的是情绪词），所以必须靠 url 区分。
    """
    raw = (content or "").strip()
    if not raw.startswith("{") or '"表情包"' not in raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if isinstance(obj, dict) and obj.get("表情包") and obj.get("url"):
        return obj
    return None


def _as_context_content(row):
    """把一条库里的消息转成喂给模型的内容。

    - 玩家发的表情包转成一句"（青梅发来一个表情包：…）"；
    - 手机通道的她本人的话是纯文本，包回 {"消息": ...}，让历史看起来和输出格式一致；
    - 当面通道存的就是她输出的 JSON（对白/表情包），原样回灌，顺带强化格式；
    - NPC 的话拆成"名字：台词"，不然她会以为是自己说的。
    """
    content = row.get("content") or ""
    channel = row.get("channel") or "chat"
    if row.get("role") == "user":
        sticker = _parse_sticker_message(content)
        if sticker:
            label = sticker.get("desc") or sticker.get("表情包") or "表情包"
            return f"（青梅发来一个表情包：{label}）"
    if channel == "npc":
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return f"{data.get('name', '路人')}：{data.get('text', '')}"
        except Exception:
            pass
        return content
    if row["role"] == "assistant" and channel == PHONE_CHANNEL:
        return json.dumps({"消息": content}, ensure_ascii=False)
    return content


def _context_window(conv_id, limit=10):
    """当面 + 手机 + NPC 合成一条时间线。

    之前只取当前通道的历史：切到手机就忘了刚当面说过的话，切回当面又不记得
    手机里聊了什么，两边各说各的。
    """
    rows = [m for m in db.get_messages(conv_id)
            if (m.get("channel") or "chat") in (CHAT_CHANNEL, PHONE_CHANNEL, "npc")][-limit:]
    return [{"role": m["role"], "content": _as_context_content(m)} for m in rows]


def _scene_payload(conv_id, api_key):
    now = time_keeper.init_clock(db, conv_id)
    view = scenes.scene_view(db, conv_id, locations.get_current(db, conv_id), now,
                             deepseek, api_key=api_key)
    view["locations"] = locations.list_names(db, conv_id)
    view["story_time"] = clock.format_time(now)
    view["story_minutes"] = now
    view["unread"] = db.get_phone_unread(conv_id)
    view["paused"] = db.is_paused(conv_id)
    # 她的心情（界面头像画成对应的颜文字）与行动速度
    try:
        view["mood"] = db.get_yuxia_mood(conv_id) or MOOD_DEFAULT
        view["mood_face"] = MOOD_FACES.get(view["mood"], MOOD_FACES[MOOD_DEFAULT])
        view["speed"] = db.get_speed(conv_id, AUTONOMY_SPEED_DEFAULT)
    except Exception:
        view["mood"] = MOOD_DEFAULT
        view["mood_face"] = MOOD_FACES[MOOD_DEFAULT]
        view["speed"] = AUTONOMY_SPEED_DEFAULT
    # 现场有谁（NPC），前端可以显示"在场：老板"
    try:
        locs = {view.get("location"), view.get("player_location")}
        view["npcs"] = [n for n in db.get_npcs(conv_id) if n.get("location") in locs]
    except Exception:
        view["npcs"] = []
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
    # 玩家可以发一张表情包代替打字：sticker 传表情包相对路径
    sticker_path = (data.get("sticker") or "").strip().replace("\\", "/")
    sticker_row = db.get_sticker(sticker_path) if sticker_path else None
    sticker_desc = ""
    if sticker_path and not sticker_row:
        return jsonify({"error": "表情包不存在"}), 400
    user_id = request.user_id
    api_key = request_api_key()
    if not sticker_path and not user_message:
        return jsonify({"error": "消息不能为空"}), 400
    if sticker_row:
        # 她读到的是一句人话，库里存的则是一条"图片消息"（前端/App 渲染成图）
        sticker_desc = stickers.describe(db, sticker_path)
        user_message = f"（青梅发来一个表情包：{sticker_desc}）"

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

    # 玩家主动移动：/去 地点（一个人走）、/带 地点（拉着尤夏一起走）
    moved = False
    moved_together = False
    move_match = _MOVE_CMD_RE.match(user_message)
    bring_match = _BRING_CMD_RE.match(user_message)
    if move_match:
        target, created = locations.resolve(db, conv_id, move_match.group(1))
        if target:
            from_player = db.get_player_location(conv_id)
            db.set_player_location(conv_id, target)
            _log_move(conv_id, "player", from_player, target, created)
            user_message = f"（青梅走到了{target}）"
            moved = True
    elif bring_match:
        if db.get_current_location(conv_id) != db.get_player_location(conv_id):
            user_message = f"（青梅想拉着尤夏去{bring_match.group(1)}，可两人根本不在一起）"
        else:
            from_yuxia = db.get_current_location(conv_id)
            target, created = locations.switch_both(db, conv_id, bring_match.group(1))
            if target:
                _log_move(conv_id, "both", from_yuxia, target, created)
                user_message = f"（青梅拉着尤夏一起去了{target}）"
                moved = True
                moved_together = True

    yuxia_loc = db.get_current_location(conv_id)
    player_loc = db.get_player_location(conv_id)
    co_located = player_loc == yuxia_loc
    # 玩家可以指定用手机发（即使同处一地），否则按是否同处一地决定
    requested = (data.get("channel") or "").strip()
    channel = requested if requested in (CHAT_CHANNEL, PHONE_CHANNEL) else (
        CHAT_CHANNEL if co_located else PHONE_CHANNEL)
    can_speak = channel == CHAT_CHANNEL

    # 现场有没有玩家正在搭话的 NPC：
    # 「在跟谁说话」只交给模型判断（不再用正则猜句首称呼——以前 "hey?" 会凭空
    # 冒出一个叫 hey 的 NPC）。这里先找回现场的，认不出来就问一次小模型。
    addressed_npc = None
    if not sticker_row:
        addressed_npc = npc.resolve_addressed(db, conv_id, player_loc, user_message,
                                              deepseek, api_key=api_key)

    db.add_message(conv_id, "user", _stored_user_content(sticker_path, sticker_desc, user_message),
                   channel=channel, scene=player_loc)
    db.touch_conversation(conv_id)
    # 记下"她说完之后，玩家隔了多久才回"：等待时长按这个自适应
    prev_yuxia_at, _prev_channel = db.get_last_yuxia(conv_id)
    if prev_yuxia_at:
        latency = time.time() - prev_yuxia_at
        if 1 <= latency < 3600:
            db.update_reply_latency(conv_id, latency)
    db.set_last_user_at(conv_id, time.time())

    all_messages = db.get_messages(conv_id)
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        # 发表情包开的头：别把"（青梅发来一个表情包：…）"整句当标题
        title = "表情包" if sticker_row else user_message[:20] + (
            "..." if len(user_message) > 20 else "")
        db.update_conversation_title(conv_id, title)

    # 表情包不参与关键词换时间（图上的说明文字不该把时钟拨走）
    now_minutes, keyword_jump = time_keeper.apply_message_tick(
        db, conv_id, "" if sticker_row else user_message)
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

    channel_rule = (
        "你们现在在同一个地方，可以当面说话。当面的对白要短、平、少。"
        if can_speak else
        "你们现在不在面对面交流，只能用「消息」联系，不要输出对白。"
        "手机里的你要活泼、傲娇、带语气词和颜文字，可以连发两三条短句，"
        "和当面的冷淡形成明显反差。"
    )
    # 格式提醒放在最后：模型对 system prompt 末尾的指令最敏感
    format_rule = ("【格式】每一条输出都必须是完整的 JSON 对象，以 { 开头、以 } 结尾；"
                   "不要用纯文本回应，哪怕只有一个字或一个颜文字，也要包进 JSON。")
    # 世界块放在最前面：她得先知道刚刚出了什么事，才谈得上回应
    world = world_context.world_block(db, conv_id, now_minutes, events_limit=6)
    # 人在哪儿是每轮的实时事实：玩家在界面上换场景不会说话
    place = world_context.place_block(db, conv_id, yuxia_loc, player_loc)
    scene_here = world_context.scene_line(db, conv_id, yuxia_loc, now_minutes)
    # 她自己的动作用第二人称单独给，不能混进「最近发生的事」里
    self_acts = world_context.self_actions_block(db, conv_id, now_minutes, limit=2)
    # 现场还有谁（煎饼摊老板这类）
    npcs_here = npc.present_block(db, conv_id, player_loc)
    blocks = "\n\n".join(b for b in (world, place, scene_here, self_acts, npcs_here) if b)
    system_prompt = f"{CHAT_PROMPT}\n\n"
    if blocks:
        system_prompt += f"{blocks}\n\n"
    system_prompt += f"{channel_rule}\n\n{format_rule}"
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

        # 先说 NPC 的话：她得等人家回完，再接话才自然
        if addressed_npc:
            npc_text = npc.reply(db, conv_id, addressed_npc, user_message, deepseek,
                                 api_key=api_key)
            if npc_text:
                yield _sse({"type": "npc", "name": addressed_npc.get("name") or "店员",
                            "content": npc_text})

        context_messages = _context_window(conv_id, limit=10)
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
        # 中文引号当 JSON 引号用：掰回英文引号再试一次
        objects = _extract_json_objects(_normalize_quotes(clean))
    if not objects:
        # 模型没按 JSON 输出（或报错）：**按行拆开**再存。
        # 以前整段原样存成一条纯文本，历史里就全是纯文本，模型越聊越不回 JSON；
        # 现在把每一行包成一条「对白」（手机通道则是多条消息），结构不丢。
        fallback_channel = PHONE_CHANNEL if not can_speak else channel
        parts = [_strip_speaker(p) for p in re.split(r"[\r\n]+", clean)]
        parts = [p for p in parts if p]
        if not parts:
            return
        is_error = parts[0].startswith(("API 错误", "错误:", "无法连接", "[生成失败"))
        for part in parts[:4]:
            if can_speak:
                db.add_message(conv_id, "assistant",
                               json.dumps({"对白": part}, ensure_ascii=False),
                               channel=CHAT_CHANNEL, scene=yuxia_loc)
            else:
                db.add_message(conv_id, "assistant", part,
                               channel=fallback_channel, scene=yuxia_loc)
        db.set_last_yuxia_at(conv_id, time.time(), fallback_channel)
        if not is_error:
            memory_engine.remember_assistant_message(
                db, conv_id, " ".join(parts)[:200], now_minutes)
        return

    speech, stickers_used, phone_msgs = [], [], []
    mood = ""
    for obj in objects:
        if obj.get("对白") and can_speak:
            speech.append(_strip_speaker(str(obj["对白"])))
        if obj.get("表情包"):
            stickers_used.append(str(obj["表情包"]).strip())
        if obj.get("消息"):
            phone_msgs.append(_strip_speaker(str(obj["消息"])))
        if not mood and obj.get("心情"):
            mood = str(obj["心情"]).strip()[:8]

    # 心情只影响界面头像；存进 chat 的 JSON 里，前端能按每条消息画表情
    if mood:
        try:
            db.set_yuxia_mood(conv_id, mood)
        except Exception:
            pass

    if speech or stickers_used:
        payload = {}
        if speech:
            payload["对白"] = "\n".join(speech)
        if stickers_used:
            payload["表情包"] = ",".join(stickers_used)
        if mood:
            payload["心情"] = mood
        db.add_message(conv_id, "assistant", json.dumps(payload, ensure_ascii=False),
                       channel=CHAT_CHANNEL, scene=yuxia_loc)

    for msg in phone_msgs:
        db.add_message(conv_id, "assistant", msg, channel=PHONE_CHANNEL, scene=yuxia_loc)

    readable = " ".join(speech + [f"（手机）{m}" for m in phone_msgs]).strip()
    if readable:
        memory_engine.remember_assistant_message(db, conv_id, readable, now_minutes)

    # 记下她这次说话的通道与时刻：手机消息要给玩家更多时间，别转头就开始干活
    if phone_msgs:
        db.set_last_yuxia_at(conv_id, time.time(), PHONE_CHANNEL)
    elif speech or stickers_used:
        db.set_last_yuxia_at(conv_id, time.time(), CHAT_CHANNEL)


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
    who = (data.get("who") or "player").strip()
    target = (data.get("location") or "").strip()
    if not target:
        return jsonify({"error": "缺少目标地点"}), 400
    if who not in ("player", "yuxia", "both"):
        who = "player"

    from_yuxia = db.get_current_location(conv_id)
    from_player = db.get_player_location(conv_id)

    # 一起过去：只有两人在同一个地方时才谈得上"一起走"
    if who == "both":
        if not _co_located(conv_id):
            return jsonify({"error": "你们不在同一个地方，没法一起过去"}), 400
        name, created = locations.switch_both(db, conv_id, target)
        if not name:
            return jsonify({"error": "地点名不合法"}), 400
        _log_move(conv_id, "both", from_yuxia, name, created)
        payload = _scene_payload(conv_id, request_api_key())
        payload["created"] = created
        payload["together"] = True
        payload["who"] = who
        payload["from_location"] = from_yuxia
        payload["from_player_location"] = from_player
        payload["events"] = db.get_recent_events(conv_id, limit=50)
        return jsonify(payload)

    name, created = locations.resolve(db, conv_id, target)
    if not name:
        return jsonify({"error": "地点名不合法"}), 400

    if who == "yuxia":
        locations.switch(db, conv_id, name)
    else:
        db.set_player_location(conv_id, name)

    _log_move(conv_id, who, from_yuxia if who == "yuxia" else from_player, name, created)

    payload = _scene_payload(conv_id, request_api_key())
    payload["created"] = created
    payload["together"] = False
    payload["who"] = who
    payload["from_location"] = from_yuxia
    payload["from_player_location"] = from_player
    # 转场事件也回传一份：故事进展面板要立刻能看到，不必等下一轮
    payload["events"] = db.get_recent_events(conv_id, limit=50)
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


# ──────────────────────────────────────────────
# 动态 NPC（煎饼摊老板这类）
# ──────────────────────────────────────────────

@chat_bp.route("/api/npcs/<conv_id>", methods=["GET"])
@require_auth
def list_npcs(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    return jsonify({"npcs": db.get_npcs(conv_id),
                    "gone": db.get_npcs(conv_id, status="gone")})


@chat_bp.route("/api/npcs/<conv_id>", methods=["POST"])
@require_auth
def create_npc(conv_id):
    """手动放一个 NPC 进来（也可以靠玩家喊"老板"自动生成）"""
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:12]
    if not name:
        return jsonify({"error": "缺少名字"}), 400
    if db.count_npcs(conv_id) >= NPC_CAP:
        return jsonify({"error": f"NPC 数量已达上限（{NPC_CAP}）"}), 400

    role = (data.get("role") or "").strip()[:12]
    location = (data.get("location") or "").strip()[:20] or db.get_player_location(conv_id)
    persona = (data.get("persona") or "").strip()[:120] or f"{location}的{role or '路人'}"
    npc_id = db.add_npc(conv_id, name, role, location, persona)
    return jsonify({"npc": db.get_npc(npc_id)})


@chat_bp.route("/api/npcs/<conv_id>/<int:npc_id>/dismiss", methods=["POST"])
@require_auth
def dismiss_npc(conv_id, npc_id):
    """让某个 NPC 退场（变成 gone，不再出现在现场）"""
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    npc_row = db.get_npc(npc_id)
    if not npc_row or npc_row["conversation_id"] != conv_id:
        return jsonify({"error": "NPC 不存在"}), 404
    db.set_npc_status(npc_id, "gone")
    return jsonify({"success": True, "npcs": db.get_npcs(conv_id)})


@chat_bp.route("/api/time/<conv_id>", methods=["POST"])
@require_auth
def adjust_time(conv_id):
    """手动拨时间（前端像原神那样拖表盘）。

    只允许往前走：给的是"几点几分"，就取今天或明天最近的那一次；
    也可以直接给 minutes（绝对分钟数）。拨完记一条 time_advance 事件，
    尤夏下一轮就能从「最近发生的事」里看到天色变了。
    """
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    now = time_keeper.init_clock(db, conv_id)
    day_start = (now // 1440) * 1440

    target = None
    if data.get("minutes") is not None:
        try:
            target = int(data["minutes"])
        except (TypeError, ValueError):
            target = None
    elif data.get("hour") is not None:
        try:
            hour = max(0, min(23, int(data.get("hour"))))
            minute = max(0, min(59, int(data.get("minute") or 0)))
        except (TypeError, ValueError):
            return jsonify({"error": "时间格式不对"}), 400
        target = day_start + hour * 60 + minute
        if target <= now:          # 只往前走：今天这个点已经过了就顺延到明天
            target += 1440
    if target is None:
        return jsonify({"error": "缺少时间"}), 400

    # 上限：一次最多拨三天，避免不小心滑到很远的未来
    if target <= now:
        return jsonify({"error": "时间只能往后拨"}), 400
    if target - now > 3 * 1440:
        return jsonify({"error": "一次最多往后拨三天"}), 400

    before = clock.format_time(now)
    db.set_story_minutes(conv_id, target)
    after = clock.format_time(target)
    db.add_event(conv_id, "time_advance", f"时间被拨到了{after}",
                 f"青梅把时间从{before}拨到了{after}。", "active",
                 story_time=after)

    # 新的时段要有对应的环境描写
    try:
        yuxia_loc = db.get_current_location(conv_id)
        player_loc = db.get_player_location(conv_id)
        scenes.ensure_description(db, conv_id, yuxia_loc, target, deepseek,
                                  request_api_key(), background=True)
        if player_loc != yuxia_loc:
            scenes.ensure_description(db, conv_id, player_loc, target, deepseek,
                                      request_api_key(), background=True)
    except Exception as e:
        print(f"[时间] 场景预热失败: {e}")

    payload = _scene_payload(conv_id, request_api_key())
    payload["delta_minutes"] = target - now
    payload["from"] = before
    payload["to"] = after
    return jsonify(payload)


@chat_bp.route("/api/speed/<conv_id>", methods=["POST"])
@require_auth
def set_speed(conv_id):
    """行动速度（1 慢 … 5 快）。

    等待期仍然是动态的（FIRST × GROWTH^n），速度只是整体乘一个系数；
    任何速度下最多等 20 秒（`AUTONOMY_WAIT_MAX_SECONDS`）。
    """
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    try:
        speed = int(data.get("speed", AUTONOMY_SPEED_DEFAULT))
    except (TypeError, ValueError):
        return jsonify({"error": "速度必须是数字"}), 400
    speed = max(AUTONOMY_SPEED_MIN, min(AUTONOMY_SPEED_MAX, speed))
    db.set_speed(conv_id, speed)
    return jsonify({"speed": speed,
                    "min": AUTONOMY_SPEED_MIN, "max": AUTONOMY_SPEED_MAX})


@chat_bp.route("/api/pause/<conv_id>", methods=["POST"])
@require_auth
def pause_auto(conv_id):
    """暂停 / 继续自动推进：她不自主开口、不自己做事，随机事件与世界引擎也停转。

    普通对话不受影响——暂停只是让"世界别自己动"。
    """
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    paused = bool(data.get("paused", True))
    db.set_paused(conv_id, paused)
    return jsonify({"paused": paused})


@chat_bp.route("/api/idle/<conv_id>", methods=["POST"])
@require_auth
def idle(conv_id):
    """玩家长时间没说话时，给尤夏一次自主行动机会"""
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    if db.is_paused(conv_id):
        return jsonify({"acted": False, "paused": True,
                        "unread": db.get_phone_unread(conv_id)})

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
               "visible": (acted or {}).get("visible", True),
               "unread": db.get_phone_unread(conv_id)}
    if acted and acted.get("action") in ("move", "lead"):
        payload["scene"] = _scene_payload(conv_id, api_key)
    if acted and acted.get("action") == "act":
        payload["events"] = db.get_recent_events(conv_id, limit=50)
    if acted:
        payload["story_time"] = clock.format_time(db.get_story_minutes(conv_id))
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
        # 暂停时不推进世界：没有随机事件、没有时间观察、没有自主行动
        if db.is_paused(conv_id):
            yield _sse({"type": "done", "paused": True})
            return
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
                            "events": db.get_recent_events(conv_id, limit=50),
                            "has_push": summary.get("should_push", False),
                            "push_hint": summary.get("push_hint", "")})

        reflections = memory_engine.maybe_reflect(db, conv_id, deepseek, now, api_key=api_key)
        if reflections:
            yield _sse({"type": "reflections", "items": reflections})

        brief, _turn = db.get_context_brief(conv_id)

        # 场景观察者：读对话上下文，发现地点表之外的新场所就登记并更新位置
        discovered = scene_watcher.observe(db, conv_id, deepseek, now,
                                           brief or "", api_key=api_key)
        if discovered:
            yield _sse({"type": "scene_discovered",
                        "location": discovered["location"],
                        "created": discovered["created"],
                        "entered_by": discovered["entered_by"],
                        "reason": discovered.get("reason", "")})
            if discovered["moved_player"] or discovered["moved_yuxia"]:
                yield _sse({"type": "scene_changed",
                            "scene": _scene_payload(conv_id, api_key)})

        # 尤夏的神态描写（她本人只说话，描写由这里生成）
        state_text = character_writer.describe(db, conv_id, deepseek, now,
                                               brief or "", api_key=api_key)
        if state_text:
            yield _sse({"type": "yuxia_state", "content": state_text})

        acted = autonomy.maybe_act(db, conv_id, deepseek, now, brief or "",
                                   api_key=api_key, msg_count=msg_count, idle=False)
        if acted:
            if acted["action"] == "move":
                yield _sse({"type": "scene_changed",
                            "scene": _scene_payload(conv_id, api_key)})
                yield _sse({"type": "autonomy", "action": "move",
                            "content": f"尤夏去了{acted['location']}"})
            elif acted["action"] == "lead":
                # 她拉着你一起换地方
                yield _sse({"type": "scene_changed",
                            "scene": _scene_payload(conv_id, api_key)})
                yield _sse({"type": "autonomy", "action": "lead",
                            "content": f"尤夏拉着你去了{acted['location']}"})
            elif acted["action"] == "act":
                # 她动手做了件事：同处一地时玩家看得见，异地时只在故事进展里留痕
                yield _sse({"type": "autonomy", "action": "act",
                            "content": acted["text"] if acted.get("visible") else "",
                            "visible": bool(acted.get("visible"))})
                yield _sse({"type": "event_update",
                            "events": db.get_recent_events(conv_id, limit=50),
                            "has_push": False, "push_hint": ""})
            else:
                yield _sse({"type": "autonomy", "action": acted["action"],
                            "content": acted["text"], "channel": acted.get("channel", "")})
            # 自主行动也会推进故事时间（做事 2 分钟、说话 1 分钟），页面上的时钟要跟上
            try:
                yield _sse({"type": "story_time",
                            "content": clock.format_time(db.get_story_minutes(conv_id))})
            except Exception:
                pass

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


@chat_bp.route("/api/stickers/list", methods=["GET"])
@require_auth
def list_stickers():
    """表情包列表（手机/网页的选择面板都用它）"""
    try:
        limit = int(request.args.get("limit", 300))
    except (TypeError, ValueError):
        limit = 300
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    data = stickers.list_for_picker(
        db, limit=max(1, min(limit, 500)), offset=max(0, offset),
        emotion=(request.args.get("emotion") or "").strip() or None)
    return jsonify(data)


@chat_bp.route("/api/stickers/upload", methods=["POST"])
@require_auth
def upload_stickers():
    """一次上传多张图片（手机相册多选），落盘即入库并自动打标"""
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "没有收到图片"}), 400

    batch = []
    for storage in files:
        try:
            batch.append((storage.filename or "image", storage.read()))
        except Exception as e:
            print(f"[表情包] 读取上传失败: {e}")
    if not batch:
        return jsonify({"error": "图片读取失败"}), 400

    result = stickers.save_uploads(
        db, batch, api_key=request_api_key(),
        max_files=STICKER_UPLOAD_MAX_FILES, max_mb=STICKER_UPLOAD_MAX_MB)
    result["success"] = True
    return jsonify(result)


# ──────────────────────────────────────────────
# 后台通知（原生 App 用）
# ──────────────────────────────────────────────

@chat_bp.route("/api/notify/pending", methods=["GET"])
@require_auth
def notify_pending():
    """返回 since_id 之后她发的消息，供 App 后台轮询发系统通知。

    ?conversation_id=xxx&since_id=123
    """
    conv_id = (request.args.get("conversation_id") or "").strip()
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    try:
        since_id = int(request.args.get("since_id", 0))
    except (TypeError, ValueError):
        since_id = 0

    items = []
    last_id = since_id
    for row in db.get_messages(conv_id):
        mid = int(row.get("id") or 0)
        if mid <= since_id:
            continue
        last_id = max(last_id, mid)
        if row.get("role") != "assistant":
            continue
        channel = row.get("channel") or CHAT_CHANNEL
        if channel not in (CHAT_CHANNEL, PHONE_CHANNEL):
            continue
        text, sticker_url = _readable_reply(row.get("content") or "", channel)
        if not text and not sticker_url:
            continue
        items.append({
            "id": mid,
            "channel": channel,
            "text": text,
            "sticker": sticker_url,
            "at": row.get("created_at") or "",
        })

    return jsonify({
        "conversation_id": conv_id,
        "since_id": since_id,
        "last_id": last_id,
        "messages": items[-10:],
        "unread": db.get_phone_unread(conv_id),
        "paused": db.is_paused(conv_id),
        "location": db.get_current_location(conv_id),
        "co_located": _co_located(conv_id),
    })


def _readable_reply(content, channel):
    """把她的一条输出拆成"能放进通知里的一句话" + 表情包图片地址"""
    raw = (content or "").strip()
    if channel == PHONE_CHANNEL:
        return _strip_speaker(raw), ""
    sticker_url = ""
    try:
        obj = json.loads(raw)
    except Exception:
        return raw, ""
    if not isinstance(obj, dict):
        return raw, ""
    speech = str(obj.get("对白") or "").strip()
    emotion = str(obj.get("表情包") or "").strip()
    if emotion:
        hit = stickers.match(db, emotion, top_k=1)
        if hit:
            sticker_url = hit[0]["url"]
    return speech, sticker_url

