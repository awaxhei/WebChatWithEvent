"""
聊天路由：主聊天接口 (SSE 流式)
"""
import json, re
from flask import Blueprint, request, Response, jsonify
from core.auth import require_auth
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from services.event_engine import _run_event_ai as run_event_ai, build_event_context
from services.atmosphere import extract_location, strip_location_tag
from config import CHAT_PROMPT, ATMOSPHERE_PROMPT, CHARACTER_NAME, EVENT_AI_INTERVAL

chat_bp = Blueprint("chat", __name__)
db = None       # 由外部注入
deepseek = None # 由外部注入

_TRUNCATE_RE = re.compile(r'(?:用户|User|Assistant)[：:]', re.IGNORECASE)
_LOCATION_RE = re.compile(r'【地点】\s*(.+)')
_PUSH_CMD_RE = re.compile(r'^/推进\s*(.*)')


@chat_bp.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.get_json()
    conv_id = data.get("conversation_id", "")
    user_message = data.get("message", "").strip()
    user_id = request.user_id
    if not user_message:
        return jsonify({"error": "消息不能为空"}), 400

    is_push_cmd = False
    push_cmd_match = _PUSH_CMD_RE.match(user_message)
    if push_cmd_match:
        is_push_cmd = True
        hint = push_cmd_match.group(1).strip()
        user_message = hint if hint else "（青梅轻轻戳了戳尤夏的肩膀，似乎在期待什么...）"

    if not conv_id:
        conv_id = db.create_conversation(user_id)
    elif not db.conversation_belongs_to(conv_id, user_id):
        conv_id = db.create_conversation(user_id)

    db.add_message(conv_id, "user", user_message)
    db.touch_conversation(conv_id)
    messages = db.get_messages(conv_id)
    user_msgs = [m for m in messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_conversation_title(conv_id, user_message[:20] + ("..." if len(user_message) > 20 else ""))

    recent_messages = messages[-20:]
    current_location = "公寓客厅"
    latest_atmo = db.get_atmosphere(conv_id, limit=1)
    if latest_atmo:
        m = _LOCATION_RE.search(latest_atmo[0]["content"])
        if m:
            current_location = m.group(1).strip()
    event_context = build_event_context(conv_id, db)
    push_extra = ""
    if is_push_cmd:
        push_extra = "\n\n【系统提示】故事需要向前推进了。请自然地推动情节发展。"
    dynamic_chat_prompt = f"【当前地点】：{current_location}\n{event_context}{push_extra}\n\n{CHAT_PROMPT}"
    msg_count = db.get_message_count(conv_id)
    should_run_event_ai = is_push_cmd or (msg_count % EVENT_AI_INTERVAL == 0)

    def generate():
        full_response = ""
        yield f"data: {json.dumps({'type': 'conv_id', 'id': conv_id})}\n\n"
        for chunk in deepseek.chat_stream(recent_messages, dynamic_chat_prompt):
            full_response += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        if full_response:
            clean = full_response.strip()
            m = _TRUNCATE_RE.search(clean)
            if m:
                clean = clean[:m.start()].strip()
            db.add_message(conv_id, "assistant", clean)
            db.touch_conversation(conv_id)
        # AI B
        all_messages = db.get_messages(conv_id)
        atmo_history = db.get_atmosphere(conv_id, limit=5)
        latest_msgs = all_messages[-2:]
        ac = "以下是之前的连续环境描述，请保持延续感：\n"
        if atmo_history:
            for ah in atmo_history:
                ac += f"- {ah['content']}\n"
        else:
            ac += "（这是对话的开始）\n"
        au = f"{ac}\n最新的对话内容：\n"
        for msg in latest_msgs:
            label = "用户" if msg["role"] == "user" else CHARACTER_NAME
            au += f"{label}：{msg['content']}\n"
        au += "\n请输出当前时刻的环境描写（仅旁白文字，不超过60字）："
        atmo_text = deepseek.chat_sync([{"role": "user", "content": au}], ATMOSPHERE_PROMPT, temperature=0.8)
        if atmo_text and not atmo_text.startswith("[生成失败"):
            loc_match = _LOCATION_RE.search(atmo_text)
            if loc_match:
                loc = loc_match.group(1).strip()
                yield f"data: {json.dumps({'type': 'location', 'content': loc})}\n\n"
                db.add_atmosphere(conv_id, atmo_text)
                ad = _LOCATION_RE.sub("", atmo_text).strip()
                if ad:
                    yield f"data: {json.dumps({'type': 'atmosphere', 'content': ad})}\n\n"
            else:
                db.add_atmosphere(conv_id, atmo_text)
                yield f"data: {json.dumps({'type': 'atmosphere', 'content': atmo_text})}\n\n"
        # AI C
        if should_run_event_ai:
            yield f"data: {json.dumps({'type': 'event_organizing'})}\n\n"
            ev = run_event_ai(conv_id, db, deepseek, force_push=is_push_cmd)
            if ev:
                re2 = ev.get("random_event")
                if re2 and isinstance(re2, dict):
                    yield f"data: {json.dumps({'type': 'world_narration', 'content': re2.get('description',''), 'intensity': re2.get('intensity','low')})}\n\n"
                if ev.get("story_time"):
                    yield f"data: {json.dumps({'type': 'story_time', 'content': ev['story_time']})}\n\n"
                if ev.get("story_summary"):
                    yield f"data: {json.dumps({'type': 'story_summary', 'content': ev['story_summary']})}\n\n"
                all_events = db.get_events(conv_id, limit=50)
                yield f"data: {json.dumps({'type': 'event_update', 'events': all_events, 'has_push': ev.get('should_push',False), 'push_hint': ev.get('push_hint','')})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")