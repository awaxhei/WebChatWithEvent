"""
AI C: 世界引擎 - 事件分析、初始化、上下文构建
"""
import json, re
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from config import EVENT_PROMPT, DEFAULT_STORY_TIME, CHARACTER_NAME


def _parse_event_json(raw_text):
    if not raw_text or raw_text.startswith("[生成失败"):
        return None
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        json_text = re.sub(r'^```(?:json)?\s*\n?', '', json_text)
        json_text = re.sub(r'\n?```\s*$', '', json_text)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        print(f"[AI C] JSON 解析失败: {json_text[:200]}")
        return None


def _run_event_ai(conv_id, db: ChatDB, deepseek: DeepSeekClient, force_push=False, is_initializing=False):
    try:
        messages = db.get_messages(conv_id)
        existing_events = db.get_events(conv_id, limit=100)
        current_story_time = db.get_latest_story_time(conv_id) or DEFAULT_STORY_TIME
        msg_limit = len(messages) if is_initializing else 30
        msg_slice = messages[-msg_limit:]
        # 使用摘要代替原始消息：读取过往摘要 + 保留最近5条原文
        existing_summaries = db.get_latest_summaries(conv_id, limit=5)
        if existing_summaries and not is_initializing:
            summary_lines = [s["summary"] for s in existing_summaries]
            summary_text = " ".join(summary_lines)
            recent_count = min(5, len(msg_slice))
            recent_text = "\n".join(
                f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}" for m in msg_slice[-recent_count:]
            )
            history_text = f"【过往剧情摘要】\n{summary_text}\n\n【最近对话】\n{recent_text}"
        else:
            history_text = "\n".join(
                f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}" for m in msg_slice
            )
        events_text = "暂无"
        if existing_events:
            lines = []
            for e in existing_events:
                ts = f" @{e['story_time']}" if e.get('story_time') else ""
                sl = {"active": "进行中", "completed": "已完成", "pending": "待触发"}.get(e["status"], e["status"])
                lines.append(f"- [{sl}] [{e['event_type']}] {e['title']}: {e['description']}{ts}")
            events_text = "\n".join(lines)
        init_extra = push_extra = ""
        if is_initializing:
            init_extra = "\n【注意】这是历史事件初始化任务。请一次性分析全部对话历史，识别所有关键事件节点并生成完整的时间线。从「第一天早晨 8:00」开始。\n"
        if force_push:
            push_extra = "\n【注意】用户发送了 /推进 指令，请在本次分析中主动推进故事！如果有合适的关联线索，可以生成 random_event。\n"
        event_user_msg = (
            f"以下是对话历史：\n```\n{history_text}\n```\n\n"
            f"以下是已有的事件记录：\n{events_text}\n\n"
            f"对话轮数（用户发言次数）：{db.get_message_count(conv_id)}\n"
            f"当前故事时间：{current_story_time}\n"
            f"{init_extra}{push_extra}"
            "请分析当前故事进展，输出 JSON。"
        )
        temp = 0.7 if force_push else (0.5 if is_initializing else 0.4)
        event_result = deepseek.chat_sync(messages=[{"role": "user", "content": event_user_msg}], system_prompt=EVENT_PROMPT, temperature=temp)
        data = _parse_event_json(event_result)
        if not data:
            return None
        current_time = data.get("story_time", current_story_time)
        for event in data.get("events", []):
            etype = event.get("type", "misc")
            if etype not in ('milestone', 'scene_change', 'emotional_shift', 'time_advance', 'random', 'misc'):
                etype = 'misc'
            db.add_event(conv_id, etype, event.get("title", "未命名事件"), event.get("description", ""),
                         event.get("status", "active"), story_time=event.get("story_time", current_time))
        random_event = data.get("random_event")
        if random_event and isinstance(random_event, dict):
            db.add_event(conv_id, "random", random_event.get("title", "偶然事件"), random_event.get("description", ""),
                         "active", story_time=current_time)
        if is_initializing:
            db.mark_events_initialized(conv_id)
        return {
            "story_time": current_time,
            "story_summary": data.get("story_summary", ""),
            "events": data.get("events", []),
            "random_event": random_event,
            "should_push": data.get("should_push", False),
            "push_hint": data.get("push_hint", ""),
        }
    except Exception as e:
        print(f"[AI C] 运行异常: {e}")
        return None


def init_events_from_history(conv_id, db: ChatDB, deepseek: DeepSeekClient):
    db.clear_events(conv_id)
    return _run_event_ai(conv_id, db, deepseek, force_push=False, is_initializing=True)


def build_event_context(conv_id, db: ChatDB):
    events = db.get_events(conv_id, limit=20)
    lines = []
    story_time = db.get_latest_story_time(conv_id)
    if story_time:
        lines.append(f"【故事时间】{story_time}")
    if events:
        active = [e for e in events if e["status"] == "active"]
        completed = [e for e in events if e["status"] == "completed" and e["event_type"] != "random"]
        pending = [e for e in events if e["status"] == "pending"]
        lines.append("【故事进展】")
        if active:
            lines.append("当前进行中的事件：")
            for e in active[-5:]:
                lines.append(f"  → {e['title']}（{e['description']}）")
        if pending:
            lines.append("即将发生/待回应的事件：")
            for e in pending[-3:]:
                lines.append(f"  ⏳ {e['title']}（{e['description']}）")
        if completed:
            lines.append("近期已完成的事件：")
            for e in completed[-3:]:
                lines.append(f"  ✓ {e['title']}")
    return "\n".join(lines)