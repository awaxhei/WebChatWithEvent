"""
事件路由：事件初始化、查询
"""
from flask import Blueprint, request, jsonify
from core.auth import require_auth
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from services.event_engine import init_events_from_history

event_bp = Blueprint("events", __name__)
db = None       # 由外部注入
deepseek = None # 由外部注入


@event_bp.route("/api/events/init/<conv_id>", methods=["POST"])
@require_auth
def init_events(conv_id):
    try:
        messages = db.get_messages(conv_id)
        if not messages:
            return jsonify({"success": False, "error": "该会话没有对话记录"}), 400
        if db.is_events_initialized(conv_id):
            return jsonify({"success": False, "error": "该会话已完成事件初始化"}), 400
        result = init_events_from_history(conv_id, db, deepseek)
        if not result:
            return jsonify({"success": False, "error": "AI C 分析失败"}), 500
        return jsonify({
            "success": True,
            "story_time": result.get("story_time", ""),
            "story_summary": result.get("story_summary", ""),
            "events": db.get_events(conv_id, limit=50),
            "has_push": result.get("should_push", False),
            "push_hint": result.get("push_hint", ""),
            "random_event": result.get("random_event"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@event_bp.route("/api/events/<conv_id>", methods=["GET"])
@require_auth
def get_events(conv_id):
    return jsonify({
        "events": db.get_events(conv_id, limit=50),
        "story_time": db.get_latest_story_time(conv_id),
        "initialized": db.is_events_initialized(conv_id),
    })