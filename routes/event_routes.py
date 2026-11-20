"""
事件路由：事件初始化、查询
"""
from flask import Blueprint, request, jsonify

import core.clock as clock
from core.auth import require_auth, ensure_conversation_owner, request_api_key
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from services.event_engine import init_events_from_history
from services.time_keeper import init_clock

event_bp = Blueprint("events", __name__)
db = None       # 由外部注入
deepseek = None # 由外部注入


@event_bp.route("/api/events/init/<conv_id>", methods=["POST"])
@require_auth
def init_events(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    try:
        messages = db.get_messages(conv_id)
        if not messages:
            return jsonify({"success": False, "error": "该会话没有对话记录"}), 400
        if db.is_events_initialized(conv_id):
            return jsonify({"success": False, "error": "该会话已完成事件初始化"}), 400

        now = init_clock(db, conv_id)
        result = init_events_from_history(conv_id, db, deepseek,
                                          api_key=request_api_key(), now_minutes=now)
        if not result:
            return jsonify({"success": False, "error": "AI C 分析失败"}), 500
        return jsonify({
            "success": True,
            "story_time": clock.format_time(now),
            "story_summary": result.get("story_summary", ""),
            "events": db.get_recent_events(conv_id, limit=50),
            "has_push": result.get("should_push", False),
            "push_hint": result.get("push_hint", ""),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@event_bp.route("/api/events/<conv_id>", methods=["GET"])
@require_auth
def get_events(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    now = init_clock(db, conv_id)
    events = db.get_recent_events(conv_id, limit=50)
    return jsonify({
        "events": events,
        "story_time": clock.format_time(now),
        "initialized": db.is_events_initialized(conv_id),
        "location": db.get_current_location(conv_id),
        # 和 /api/history 一致：用最新事件的描述当"故事摘要"（网页事件栏顶部那行）
        "story_summary": events[-1]["description"] if events else "",
    })
