"""
AI C: 世界引擎 - 事件分析、初始化、上下文构建
拆分职责：
  - _run_random_event: 概率触发生成偶然事件（轻量）
  - _run_event_summary: 智能触发生成事件节点 + 时间线推进 + 对话摘要
"""
import json, re, random
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from config import EVENT_PROMPT, DEFAULT_STORY_TIME, CHARACTER_NAME, EVENT_AI_INTERVAL, RANDOM_EVENT_BASE_PROB
from services.atmosphere import extract_location


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


# ──────────────────────────────────────────────
# 触发判断辅助函数
# ──────────────────────────────────────────────

def should_run_random_event(msg_count, is_push_cmd):
    """偶然事件概率触发（方案A）"""
    if is_push_cmd:
        return random.random() < 0.5  # /推进时 50% 概率
    base_prob = RANDOM_EVENT_BASE_PROB  # 基础概率（默认 0.15）
    adjusted = base_prob + (msg_count * 0.005)  # 随轮数微增
    return random.random() < min(adjusted, 0.35)


def detect_location_change(conv_id, db: ChatDB):
    """检测最近两次环境叙述中地点是否变化"""
    recent_atmo = db.get_atmosphere(conv_id, limit=2)
    if len(recent_atmo) < 2:
        return False
    loc1 = extract_location(recent_atmo[0]["content"])
    loc2 = extract_location(recent_atmo[1]["content"])
    return loc1 and loc2 and loc1 != loc2


def detect_emotional_keywords(recent_messages):
    """检测情感关键词转折"""
    keywords = ["对不起", "我错了", "笨蛋", "喜欢", "讨厌", "走吧", "走了", "再见",
                 "等我", "过来", "不要", "够了", "别说了", "好烦", "开心", "难过"]
    for msg in recent_messages[-3:]:
        content = msg.get("content", "")
        for kw in keywords:
            if kw in content:
                return True
    return False


def should_run_event_summary(conv_id, db: ChatDB, is_push_cmd, msg_count, recent_messages):
    """事件总结智能触发（方案B）"""
    if is_push_cmd:
        return True
    if detect_location_change(conv_id, db):
        return True
    if msg_count % EVENT_AI_INTERVAL == 0:
        return True
    if detect_emotional_keywords(recent_messages):
        return True
    return False


# ──────────────────────────────────────────────
# 偶然事件生成器
# ──────────────────────────────────────────────

def _run_random_event(conv_id, db: ChatDB, deepseek: DeepSeekClient):
    """仅生成偶然事件，轻量调用"""
    from config import RANDOM_EVENT_SYSTEM_PROMPT
    try:
        messages = db.get_messages(conv_id)
        if len(messages) < 4:
            return None
        recent = messages[-6:]
        conversation_text = "\n".join(
            f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}"
            for m in recent
        )
        current_story_time = db.get_latest_story_time(conv_id) or DEFAULT_STORY_TIME
        event_user_msg = (
            f"【当前故事时间】{current_story_time}\n"
            f"【最近对话】\n{conversation_text}\n\n"
            "请判断是否有合适的偶然事件可生成。输出 JSON。"
        )
        result = deepseek.chat_sync(
            messages=[{"role": "user", "content": event_user_msg}],
            system_prompt=RANDOM_EVENT_SYSTEM_PROMPT,
            temperature=0.7
        )
        data = _parse_event_json(result)
        if not data:
            return None
        random_event = data.get("random_event")
        if random_event and isinstance(random_event, dict):
            db.add_event(conv_id, "random", random_event.get("title", "偶然事件"),
                         random_event.get("description", ""), "active", story_time=current_story_time)
            return random_event
        return None
    except Exception as e:
        print(f"[随机事件] 生成异常: {e}")
        return None


# ──────────────────────────────────────────────
# 事件总结器（含摘要生成）
# ──────────────────────────────────────────────

def _run_event_summary(conv_id, db: ChatDB, deepseek: DeepSeekClient,
                       force_push=False, is_initializing=False):
    """分析对话生成事件节点 + 推进时间线 + 生成对话摘要"""
    try:
        messages = db.get_messages(conv_id)
        existing_events = db.get_events(conv_id, limit=20)  # 从 100 降至 20
        current_story_time = db.get_latest_story_time(conv_id) or DEFAULT_STORY_TIME
        msg_limit = len(messages) if is_initializing else 20  # 从 30 降至 20
        msg_slice = messages[-msg_limit:]
        # 使用摘要代替原始消息
        existing_summaries = db.get_latest_summaries(conv_id, limit=6)
        if existing_summaries and not is_initializing:
            summary_lines = [s["summary"] for s in existing_summaries]
            summary_text = " ".join(summary_lines)
            recent_count = min(5, len(msg_slice))
            recent_text = "\n".join(
                f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}"
                for m in msg_slice[-recent_count:]
            )
            history_text = f"【过往剧情摘要】\n{summary_text}\n\n【最近对话】\n{recent_text}"
        else:
            history_text = "\n".join(
                f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}"
                for m in msg_slice
            )
        # 只传活跃事件 + 最近 5 条已完成事件
        events_text = "暂无"
        if existing_events:
            active_events = [e for e in existing_events if e["status"] == "active"]
            completed_recent = [e for e in existing_events if e["status"] == "completed"][-5:]
            relevant_events = active_events + completed_recent
            if relevant_events:
                lines = []
                for e in relevant_events:
                    ts = f" @{e['story_time']}" if e.get('story_time') else ""
                    sl = {"active": "进行中", "completed": "已完成", "pending": "待触发"}.get(e["status"], e["status"])
                    lines.append(f"- [{sl}] [{e['event_type']}] {e['title']}: {e['description']}{ts}")
                events_text = "\n".join(lines)
        init_extra = push_extra = ""
        if is_initializing:
            init_extra = "\n【注意】这是历史事件初始化任务。请一次性分析全部对话历史，识别所有关键事件节点并生成完整的时间线。从「第一天早晨 8:00」开始。\n"
        if force_push:
            push_extra = "\n【注意】用户发送了 /推进 指令，请在本次分析中主动推进故事！\n"
        msg_count = db.get_message_count(conv_id)
        # 获取最后一轮摘要的 end_msg_id，用于计算新消息范围
        last_end = db.get_last_summary_end_id(conv_id)
        new_msgs_for_summary = db.get_messages_since(conv_id, last_end)
        summary_lines_for_prompt = ""
        if new_msgs_for_summary and len(new_msgs_for_summary) >= 3:
            lines = "\n".join(
                f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}"
                for m in new_msgs_for_summary
            )
            summary_lines_for_prompt = f"\n【本轮对话内容（需压缩为摘要）】\n{lines}\n"
        event_user_msg = (
            f"以下是对话历史：\n```\n{history_text}\n```\n\n"
            f"以下是已有的事件记录：\n{events_text}\n\n"
            f"对话轮数（用户发言次数）：{msg_count}\n"
            f"当前故事时间：{current_story_time}\n"
            f"{summary_lines_for_prompt}"
            f"{init_extra}{push_extra}"
            "请分析当前故事进展，输出 JSON。"
        )
        temp = 0.7 if force_push else (0.5 if is_initializing else 0.4)
        event_result = deepseek.chat_sync(
            messages=[{"role": "user", "content": event_user_msg}],
            system_prompt=EVENT_PROMPT,
            temperature=temp
        )
        data = _parse_event_json(event_result)
        if not data:
            return None
        current_time = data.get("story_time", current_story_time)
        for event in data.get("events", []):
            etype = event.get("type", "misc")
            if etype not in ('milestone', 'scene_change', 'emotional_shift', 'time_advance', 'random', 'misc'):
                etype = 'misc'
            db.add_event(conv_id, etype, event.get("title", "未命名事件"),
                         event.get("description", ""),
                         event.get("status", "active"),
                         story_time=event.get("story_time", current_time))
        # 保存对话摘要（从 AI C 输出中提取或独立处理）
        conv_summary = data.get("conversation_summary", "")
        if not conv_summary and new_msgs_for_summary and len(new_msgs_for_summary) >= 3:
            # 如果没有从 AI C 获得摘要，使用简洁方式生成
            try:
                lines = "\n".join(
                    f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}"
                    for m in new_msgs_for_summary
                )
                conv_summary = deepseek.chat_sync(
                    [{"role": "user", "content": f"将以下对话压缩为2-3句话的剧情摘要，保留关键事件、人名、地点和情绪变化：\n\n{lines}"}],
                    temperature=0.3
                )
            except Exception:
                pass
        if conv_summary and not conv_summary.startswith("[生成失败") and new_msgs_for_summary:
            db.add_summary(conv_id, conv_summary.strip(),
                           new_msgs_for_summary[0]["id"], new_msgs_for_summary[-1]["id"])
        if is_initializing:
            db.mark_events_initialized(conv_id)
        return {
            "story_time": current_time,
            "story_summary": data.get("story_summary", ""),
            "events": data.get("events", []),
            "should_push": data.get("should_push", False),
            "push_hint": data.get("push_hint", ""),
        }
    except Exception as e:
        print(f"[AI C] 运行异常: {e}")
        return None


# ──────────────────────────────────────────────
# 保留兼容接口
# ──────────────────────────────────────────────

def _run_event_ai(conv_id, db: ChatDB, deepseek: DeepSeekClient,
                  force_push=False, is_initializing=False):
    """兼容旧接口：同时执行事件总结"""
    return _run_event_summary(conv_id, db, deepseek, force_push=force_push, is_initializing=is_initializing)


def init_events_from_history(conv_id, db: ChatDB, deepseek: DeepSeekClient):
    db.clear_events(conv_id)
    return _run_event_summary(conv_id, db, deepseek, force_push=False, is_initializing=True)


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