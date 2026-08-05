"""
聊天路由：主聊天接口 (SSE 流式) + 异步氛围/事件接口
优化项：
  - AI A JSON 格式输出 + 前端分离渲染
  - RAG 向量检索增强长期记忆
  - AI C 拆分为偶然事件生成器 + 事件总结器，各自独立触发
  - 摘要生成并入 AI C 事件总结
  - 消息读取去重
"""
import json, re
from flask import Blueprint, request, Response, jsonify
from core.auth import require_auth
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from core.embedding_client import EmbeddingClient
from core.vector_store import VectorStore
from services.event_engine import (
    _run_event_summary, _run_random_event,
    should_run_random_event, should_run_event_summary,
    build_event_context
)
from services.atmosphere import extract_location, strip_location_tag
from config import (
    CHAT_PROMPT, ATMOSPHERE_PROMPT, CHARACTER_NAME,
    EVENT_AI_INTERVAL, DEFAULT_LOCATION, DEFAULT_STORY_TIME,
    STICKER_EMOTIONS
)

chat_bp = Blueprint("chat", __name__)
db = None        # 由外部注入
deepseek = None  # 由外部注入

# RAG 相关全局实例（惰性初始化）
_vector_store = None
_embedding_client = None

def _get_vector_store():
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store

def _get_embedding_client():
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = EmbeddingClient()
    return _embedding_client

_LOCATION_RE = re.compile(r'【地点】\s*(.+)')
_PUSH_CMD_RE = re.compile(r'^/推进\s*(.*)')
_STICKER_SET = set(STICKER_EMOTIONS)


# ──────────────────────────────────────────────
# 主聊天 API（仅 AI A 流式输出 + RAG 检索）
# ──────────────────────────────────────────────

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

    # ── 消息读取去重：一次读取全部，各组件复用切片 ──
    all_messages = db.get_messages(conv_id)
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_conversation_title(conv_id, user_message[:20] + ("..." if len(user_message) > 20 else ""))

    # AI A 上下文构建
    recent_messages = all_messages[-5:]
    current_location = DEFAULT_LOCATION
    latest_atmo = db.get_atmosphere(conv_id, limit=1)
    if latest_atmo:
        m = _LOCATION_RE.search(latest_atmo[0]["content"])
        if m:
            current_location = m.group(1).strip()
    event_context = build_event_context(conv_id, db)
    push_extra = ""
    if is_push_cmd:
        push_extra = "\n\n【系统提示】故事需要向前推进了。请自然地推动情节发展。"

    # 注入过往对话摘要作为长期记忆
    summary_prefix = ""
    summaries = db.get_latest_summaries(conv_id, limit=6)
    if summaries:
        summary_text = " ".join(s["summary"] for s in summaries)
        summary_prefix = f"【过往剧情回顾】{summary_text}\n\n"

    # ── RAG 向量检索：获取最相关历史记忆 ──
    rag_prefix = ""
    try:
        vec_store = _get_vector_store()
        emb_client = _get_embedding_client()
        if vec_store.count(conv_id) > 5:
            query_emb = emb_client.embed(user_message)
            if query_emb:
                similar = vec_store.search_similar(conv_id, query_emb, top_k=5)
                if similar:
                    rag_items = []
                    for item in similar:
                        label = "青梅" if item["role"] == "user" else CHARACTER_NAME
                        rag_items.append(f"- {label}：{item['content']}")
                    rag_prefix = "【尤夏的记忆片段——与当前话题相关的往事：】\n" + "\n".join(rag_items) + "\n\n"
    except Exception as e:
        print(f"[RAG] 检索失败: {e}")

    dynamic_chat_prompt = f"{rag_prefix}{summary_prefix}【当前地点】：{current_location}\n{event_context}{push_extra}\n\n{CHAT_PROMPT}"
    msg_count = db.get_message_count(conv_id)

    def generate():
        full_response = ""
        yield f"data: {json.dumps({'type': 'conv_id', 'id': conv_id})}\n\n"

        # ── AI A 流式输出（JSON 格式）──
        for chunk in deepseek.chat_stream(recent_messages, dynamic_chat_prompt):
            full_response += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"

        if full_response:
            clean = full_response.strip()
            db.add_message(conv_id, "assistant", clean)
            db.touch_conversation(conv_id)

            # ── RAG：将用户消息和 AI 回复存入向量库 ──
            try:
                emb_client = _get_embedding_client()
                vec_store = _get_vector_store()
                # 获取最后两条消息的 id
                last_msgs = db.get_messages(conv_id)[-2:]
                for lm in last_msgs:
                    emb = emb_client.embed(lm["content"])
                    if emb:
                        vec_store.add_message(conv_id, lm["id"], lm["role"], lm["content"], emb)
            except Exception as e:
                print(f"[RAG] 向量存储失败: {e}")

        yield f"data: {json.dumps({'type': 'done', 'conv_id': conv_id, 'msg_count': msg_count, 'is_push_cmd': is_push_cmd})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


# ──────────────────────────────────────────────
# 异步氛围/事件 API（AI B + AI C）
# 前端在 AI A 流结束后调用此端点
# ──────────────────────────────────────────────

@chat_bp.route("/api/atmosphere/<conv_id>", methods=["POST"])
@require_auth
def atmosphere(conv_id):
    data = request.get_json(silent=True) or {}
    is_push_cmd = data.get("is_push_cmd", False)
    msg_count = data.get("msg_count", 0)

    def generate():
        yield f"data: {json.dumps({'type': 'start'})}\n\n"

        # ── AI C：偶然事件（概率触发，方案A）──
        all_messages = db.get_messages(conv_id)
        recent_for_event = all_messages[-5:]
        if should_run_random_event(msg_count, is_push_cmd):
            yield f"data: {json.dumps({'type': 'event_organizing', 'hint': '事件生成中...'})}\n\n"
            re2 = _run_random_event(conv_id, db, deepseek)
            if re2:
                yield f"data: {json.dumps({'type': 'world_narration', 'content': re2.get('description', ''), 'intensity': re2.get('intensity', 'low')})}\n\n"

        # ── AI C：事件总结（智能触发，方案B）──
        if should_run_event_summary(conv_id, db, is_push_cmd, msg_count, recent_for_event):
            yield f"data: {json.dumps({'type': 'event_organizing', 'hint': '事件总结中...'})}\n\n"
            ev = _run_event_summary(conv_id, db, deepseek, force_push=is_push_cmd)
            if ev:
                if ev.get("story_time"):
                    yield f"data: {json.dumps({'type': 'story_time', 'content': ev['story_time']})}\n\n"
                if ev.get("story_summary"):
                    yield f"data: {json.dumps({'type': 'story_summary', 'content': ev['story_summary']})}\n\n"
                all_events = db.get_events(conv_id, limit=50)
                yield f"data: {json.dumps({'type': 'event_update', 'events': all_events, 'has_push': ev.get('should_push', False), 'push_hint': ev.get('push_hint', '')})}\n\n"

        # ── AI B：环境叙述 ──
        atmo_history = db.get_atmosphere(conv_id, limit=5)
        story_time = db.get_latest_story_time(conv_id) or DEFAULT_STORY_TIME
        event_ctx = build_event_context(conv_id, db)
        ac = "以下是之前的连续环境描述，请保持延续感：\n"
        if atmo_history:
            for ah in atmo_history:
                ac += f"- {ah['content']}\n"
        else:
            ac += "（这是对话的开始）\n"
        latest_msgs = all_messages[-2:]
        au = f"【当前故事时间】{story_time}\n{event_ctx}\n\n{ac}\n最新的对话内容：\n"
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

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")