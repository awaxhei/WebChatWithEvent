"""
自然情境撰写（v4）

尤夏看到的上下文不再是【方括号】+ 列表，而是一段自然的中文叙述。
结构化状态 → 上层 LLM 写成叙述 → 缓存若干轮复用，避免每轮多花一次调用。
"""
import json

import core.clock as clock
from config import CONTEXT_WRITER_INTERVAL, CONTEXT_WRITER_PROMPT

_PHONE_LABEL = {True: "在同一个地方", False: "不在同一个地方"}


def _facts(db, conv_id, now_minutes, location, player_location, recent_msgs, events, memories, phone_unread):
    """内部事实清单（只给上下文撰写 LLM 看，不直接喂给尤夏）"""
    co_located = location == player_location
    lines = [
        f"故事时间：{clock.format_time(now_minutes)}",
        f"你现在所在的地点：{location}",
        f"青梅所在的地点：{player_location}",
        f"你们当前{_PHONE_LABEL[co_located]}，"
        + ("可以当面说话。" if co_located else "只能通过手机联系。"),
    ]

    if events:
        lines.append("最近发生的事：")
        for e in events[-4:]:
            lines.append(f"- {e['title']}：{e['description']}")

    if memories:
        lines.append("你记得的近期片段：")
        for m in memories[:4]:
            lines.append(f"- {m['text']}")

    if recent_msgs:
        lines.append("刚才说的话：")
        for m in recent_msgs[-4:]:
            speaker = "青梅" if m["role"] == "user" else "你"
            channel = "手机" if m.get("channel") == "phone" else "当面"
            lines.append(f"- {speaker}（{channel}）：{m['content']}")

    if phone_unread:
        lines.append(f"手机上有 {phone_unread} 条青梅发来的未读消息。")

    return "\n".join(lines)


def _fallback(brief_location, player_location, now_minutes):
    co_located = brief_location == player_location
    base = f"现在是{clock.format_time(now_minutes)}，你在{brief_location}，青梅在{player_location}。"
    if co_located:
        return base + "你们在一个地方，可以当面说话。"
    return base + "你们不在一起，只能靠手机联系。"


def compose(db, conv_id, now_minutes, location, player_location, deepseek,
            api_key=None, turn=0, force=False):
    """取自然情境叙述；带缓存，按轮次或强制刷新"""
    cached_text, cached_turn = db.get_context_brief(conv_id)
    if cached_text and not force and (turn - cached_turn) < CONTEXT_WRITER_INTERVAL:
        return cached_text

    recent_msgs = db.get_messages(conv_id)[-8:]
    events = db.get_events(conv_id, limit=20)[-4:]
    memories = []
    try:
        import core.memory as memory
        memories = memory.search(db, conv_id,
                                 recent_msgs[-1]["content"] if recent_msgs else "",
                                 now_minutes, top_k=4)
    except Exception:
        memories = []

    facts = _facts(db, conv_id, now_minutes, location, player_location,
                   recent_msgs, events, memories, db.get_phone_unread(conv_id))

    try:
        text = deepseek.chat_sync(
            [{"role": "user", "content": facts}],
            system_prompt=CONTEXT_WRITER_PROMPT,
            temperature=0.6,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[情境] 撰写失败: {e}")
        text = ""

    if not text or text.startswith("[生成失败"):
        text = _fallback(location, player_location, now_minutes)

    text = text.strip()
    db.set_context_brief(conv_id, text, turn)
    return text
