"""
历史记录路由：对话列表、详情、删除、新建
"""
import re
from flask import Blueprint, request, jsonify
from core.auth import require_auth
from core.database import ChatDB

history_bp = Blueprint("history", __name__)
db = None  # 由外部注入

_LOCATION_RE = re.compile(r'【地点】\s*(.+)')


@history_bp.route("/api/history", methods=["GET"])
@require_auth
def get_history():
    return jsonify(db.get_conversations(request.user_id))


@history_bp.route("/api/history/<conv_id>", methods=["GET"])
@require_auth
def get_conversation(conv_id):
    messages = db.get_messages(conv_id)
    atmo = db.get_atmosphere(conv_id, limit=100)
    events = db.get_events(conv_id, limit=50)
    story_time = db.get_latest_story_time(conv_id)
    story_summary = events[-1]["description"] if events else ""
    initialized = db.is_events_initialized(conv_id)
    location = "公寓客厅"
    atmo_list = [a["content"] for a in atmo]
    for a_text in reversed(atmo_list):
        m = _LOCATION_RE.search(a_text)
        if m:
            location = m.group(1).strip()
            break
    return jsonify({
        "conversation_id": conv_id,
        "messages": messages,
        "atmosphere": atmo_list,
        "location": location,
        "events": events,
        "story_time": story_time,
        "story_summary": story_summary,
        "events_initialized": initialized,
    })


@history_bp.route("/api/history/<conv_id>", methods=["DELETE"])
@require_auth
def delete_conversation(conv_id):
    db.delete_conversation(conv_id)
    return jsonify({"success": True})


@history_bp.route("/api/history/new", methods=["POST"])
@require_auth
def new_conversation():
    return jsonify({"id": db.create_conversation(request.user_id)})