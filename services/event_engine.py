"""
AI C: 世界引擎 —— 偶然事件生成 + 事件/时间线记录

v3 变化：
- 时间不再由 LLM 输出：故事时间由 services.time_keeper 维护，这里只读取
- 历史压缩改用 AI 小镇式记忆检索（core.memory），不再依赖对话摘要
- 事件写入后会由 services.memory_engine 转成高重要度记忆
"""
import json
import random
import re

import core.clock as clock
import core.memory as memory
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient
from config import (
    EVENT_PROMPT, CHARACTER_NAME, EVENT_AI_INTERVAL, RANDOM_EVENT_BASE_PROB,
    RANDOM_EVENT_TIERS, RANDOM_EVENT_TIER_GUIDE,
)
from services.atmosphere import extract_location

EVENT_TYPES = ("milestone", "scene_change", "emotional_shift", "time_advance", "random", "misc")


def pick_tier():
    """按权重抽取随机事件强度：影响越深，权重越低"""
    roll = random.random()
    acc = 0.0
    for name, weight in RANDOM_EVENT_TIERS:
        acc += weight
        if roll <= acc:
            return name
    return RANDOM_EVENT_TIERS[0][0]


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
        m = re.search(r"\{.*\}", json_text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        print(f"[AI C] JSON 解析失败: {json_text[:200]}")
        return None


# ──────────────────────────────────────────────
# 触发判断
# ──────────────────────────────────────────────

def should_run_random_event(msg_count, is_push_cmd):
    """偶然事件概率触发"""
    if is_push_cmd:
        return random.random() < 0.5
    adjusted = RANDOM_EVENT_BASE_PROB + (msg_count * 0.005)
    return random.random() < min(adjusted, 0.35)


def detect_location_change(conv_id, db: ChatDB):
    """检测最近两次环境叙述中地点是否变化"""
    recent_atmo = db.get_atmosphere(conv_id, limit=2)
    if len(recent_atmo) < 2:
        return False
    loc1 = extract_location(recent_atmo[0]["content"])
    loc2 = extract_location(recent_atmo[1]["content"])
    return bool(loc1 and loc2 and loc1 != loc2)


def detect_emotional_keywords(recent_messages):
    keywords = ["对不起", "我错了", "笨蛋", "喜欢", "讨厌", "走吧", "走了", "再见",
                "等我", "过来", "不要", "够了", "别说了", "好烦", "开心", "难过"]
    for msg in recent_messages[-3:]:
        content = msg.get("content", "")
        for kw in keywords:
            if kw in content:
                return True
    return False


def should_run_event_summary(conv_id, db: ChatDB, is_push_cmd, msg_count, recent_messages):
    """事件总结智能触发"""
    if is_push_cmd:
        return True
    if detect_location_change(conv_id, db):
        return True
    if msg_count and msg_count % EVENT_AI_INTERVAL == 0:
        return True
    if detect_emotional_keywords(recent_messages):
        return True
    return False


# ──────────────────────────────────────────────
# 偶然事件生成器
# ──────────────────────────────────────────────

def _run_random_event(conv_id, db: ChatDB, deepseek: DeepSeekClient,
                      now_minutes=0, api_key=None):
    from config import RANDOM_EVENT_SYSTEM_PROMPT
    try:
        messages = [m for m in db.get_messages(conv_id)
                    if (m.get('channel') or 'chat') in ('chat', 'phone')]
        if len(messages) < 4:
            return None
        recent = messages[-6:]
        conversation_text = "\n".join(
            f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}"
            for m in recent
        )
        story_time = clock.format_time(now_minutes)
        tier = pick_tier()
        event_user_msg = (
            f"【当前故事时间】{story_time}\n"
            f"【最近对话】\n{conversation_text}\n\n"
            f"【本次事件的强度等级】{tier}——{RANDOM_EVENT_TIER_GUIDE.get(tier, '')}\n"
            "请按这个强度生成一个偶然事件（不要提高或降低强度）。输出 JSON。"
        )
        result = deepseek.chat_sync(
            messages=[{"role": "user", "content": event_user_msg}],
            system_prompt=RANDOM_EVENT_SYSTEM_PROMPT,
            temperature=0.7,
            api_key_override=api_key,
        )
        data = _parse_event_json(result)
        if not data:
            return None
        random_event = data.get("random_event")
        if random_event and isinstance(random_event, dict):
            random_event["intensity"] = tier
            db.add_event(conv_id, "random", random_event.get("title", "偶然事件"),
                         random_event.get("description", ""), "active",
                         story_time=story_time, tier=tier)
            return random_event
        return None
    except Exception as e:
        print(f"[随机事件] 生成异常: {e}")
        return None


# ──────────────────────────────────────────────
# 事件总结器
# ──────────────────────────────────────────────

def _run_event_summary(conv_id, db: ChatDB, deepseek: DeepSeekClient,
                       force_push=False, is_initializing=False,
                       api_key=None, now_minutes=0):
    """分析对话并生成事件节点（时间由系统提供，不由模型输出）"""
    try:
        messages = [m for m in db.get_messages(conv_id)
                    if (m.get('channel') or 'chat') in ('chat', 'phone')]
        existing_events = db.get_recent_events(conv_id, limit=20)
        story_time = clock.format_time(now_minutes)

        recent = messages[-10:] if not is_initializing else messages[-40:]
        recent_text = "\n".join(
            f"{'青梅' if m['role'] == 'user' else CHARACTER_NAME}：{m['content']}"
            for m in recent
        )

        # 用记忆流做长期上下文，而不是把旧对话摘要全塞进去
        query = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        mem_block = memory.build_context(db, conv_id, query, now_minutes, top_k=6)
        if is_initializing:
            mem_block = ""

        events_text = "暂无"
        if existing_events:
            active = [e for e in existing_events if e["status"] == "active"]
            completed_recent = [e for e in existing_events if e["status"] == "completed"][-5:]
            relevant = active + completed_recent
            if relevant:
                lines = []
                for e in relevant:
                    ts = f" @{e['story_time']}" if e.get("story_time") else ""
                    sl = {"active": "进行中", "completed": "已完成", "pending": "待触发"}.get(e["status"], e["status"])
                    lines.append(f"- [{sl}] [{e['event_type']}] {e['title']}: {e['description']}{ts}")
                events_text = "\n".join(lines)

        extra = ""
        if is_initializing:
            extra += "\n【注意】这是历史事件初始化任务，请一次性梳理全部对话历史，识别关键事件节点。\n"
        if force_push:
            extra += "\n【注意】用户发送了 /推进 指令，请主动推动故事。\n"

        event_user_msg = (
            f"【当前故事时间（由系统维护，直接使用，不要修改）】{story_time}\n"
            f"{mem_block}\n"
            f"【最近对话】\n{recent_text}\n\n"
            f"【已有事件记录】\n{events_text}\n\n"
            f"{extra}"
            "请分析当前故事进展，输出 JSON。"
        )

        temp = 0.7 if force_push else (0.5 if is_initializing else 0.4)
        raw = deepseek.chat_sync(
            messages=[{"role": "user", "content": event_user_msg}],
            system_prompt=EVENT_PROMPT,
            temperature=temp,
            api_key_override=api_key,
        )
        data = _parse_event_json(raw)
        if not data:
            return None

        created = []
        recent_titles = {(e["event_type"], e["title"]) for e in existing_events[-10:]}
        for event in data.get("events", []):
            etype = event.get("type", "misc")
            if etype not in EVENT_TYPES:
                etype = "misc"
            title = event.get("title", "未命名事件")
            desc = event.get("description", "")
            # 去重：同样的类型 + 标题在最近事件里已存在就不再重复记一条
            if (etype, title) in recent_titles:
                continue
            db.add_event(conv_id, etype, title, desc,
                         event.get("status", "active"), story_time=story_time)
            recent_titles.add((etype, title))
            created.append({"type": etype, "title": title, "description": desc})

        if is_initializing:
            db.mark_events_initialized(conv_id)

        return {
            "story_time": story_time,
            "story_summary": data.get("story_summary", ""),
            "events": created,
            "should_push": data.get("should_push", False),
            "push_hint": data.get("push_hint", ""),
        }
    except Exception as e:
        print(f"[AI C] 运行异常: {e}")
        return None


# ──────────────────────────────────────────────
# 兼容接口
# ──────────────────────────────────────────────

def _run_event_ai(conv_id, db: ChatDB, deepseek: DeepSeekClient,
                  force_push=False, is_initializing=False, api_key=None, now_minutes=0):
    return _run_event_summary(conv_id, db, deepseek, force_push=force_push,
                              is_initializing=is_initializing, api_key=api_key,
                              now_minutes=now_minutes)


def init_events_from_history(conv_id, db: ChatDB, deepseek: DeepSeekClient,
                             api_key=None, now_minutes=0):
    db.clear_events(conv_id)
    return _run_event_summary(conv_id, db, deepseek, force_push=False,
                              is_initializing=True, api_key=api_key,
                              now_minutes=now_minutes)


def build_event_context(conv_id, db: ChatDB, now_minutes=None):
    """给 AI A 的事件上下文（时间来自系统时钟）"""
    events = db.get_recent_events(conv_id, limit=20)
    lines = []
    if now_minutes is not None:
        lines.append(f"【故事时间】{clock.format_time(now_minutes)}")
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
