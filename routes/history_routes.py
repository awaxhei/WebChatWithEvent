"""
历史记录路由：对话列表、详情、删除、新建
"""
from flask import Blueprint, request, jsonify

import core.clock as clock
import core.locations as locations
from core.auth import require_auth, ensure_conversation_owner
from core.database import ChatDB
from services.time_keeper import init_clock

history_bp = Blueprint("history", __name__)
db = None  # 由外部注入


@history_bp.route("/api/history", methods=["GET"])
@require_auth
def get_history():
    return jsonify(db.get_conversations(request.user_id))


@history_bp.route("/api/history/<conv_id>", methods=["GET"])
@require_auth
def get_conversation(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied

    try:
        db.touch_last_seen(conv_id)        # 打开历史 = 人回来了
    except Exception:
        pass
    messages = db.get_messages(conv_id)
    atmo = db.get_atmosphere(conv_id, limit=100)
    events = db.get_recent_events(conv_id, limit=50)
    now = init_clock(db, conv_id)
    location = locations.get_current(db, conv_id)
    player_location = db.get_player_location(conv_id)

    return jsonify({
        "conversation_id": conv_id,
        "messages": messages,
        "phone": db.get_phone_messages(conv_id),
        "unread": db.get_phone_unread(conv_id),
        "atmosphere": [a["content"] for a in atmo],
        "location": location,
        "player_location": player_location,
        "co_located": player_location == location,
        "events": events,
        "story_time": clock.format_time(now),
        "story_summary": events[-1]["description"] if events else "",
        "events_initialized": db.is_events_initialized(conv_id),
    })


@history_bp.route("/api/history/<conv_id>", methods=["DELETE"])
@require_auth
def delete_conversation(conv_id):
    denied = ensure_conversation_owner(db, conv_id, request.user_id)
    if denied:
        return denied
    db.delete_conversation(conv_id)
    return jsonify({"success": True})


MAX_BULK_DELETE = 200


@history_bp.route("/api/history/delete", methods=["POST"])
@require_auth
def delete_conversations():
    """批量删除会话：只删自己的，别人的跳过并报数"""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "没有要删除的会话"}), 400
    ids = [str(i) for i in ids if str(i).strip()][:MAX_BULK_DELETE]

    deleted, skipped = [], []
    for conv_id in ids:
        if ensure_conversation_owner(db, conv_id, request.user_id):
            skipped.append(conv_id)       # 不是自己的 / 不存在
            continue
        db.delete_conversation(conv_id)
        deleted.append(conv_id)
    return jsonify({"success": True, "deleted": len(deleted),
                    "deleted_ids": deleted, "skipped": len(skipped)})


@history_bp.route("/api/history/new", methods=["POST"])
@require_auth
def new_conversation():
    return jsonify({"id": db.create_conversation(request.user_id)})
