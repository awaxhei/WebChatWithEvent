"""
自然情境撰写（v4）

尤夏看到的上下文不再是【方括号】+ 列表，而是一段自然的中文叙述。

延迟策略：情境撰写是一次额外的 LLM 调用，不能卡在玩家等回复的路径上。
- 有缓存且未过期 → 直接用
- 首轮或缓存过期 → 立刻返回兜底叙述/旧缓存，**在后台线程刷新**，下一轮生效
- CONTEXT_WRITER_BACKGROUND=False 时退回同步撰写
"""
import threading

import core.clock as clock
import core.pronoun as pronoun
from config import (
    CONTEXT_WRITER_INTERVAL, CONTEXT_WRITER_PROMPT, CONTEXT_WRITER_BACKGROUND,
)

_PHONE_LABEL = {True: "在同一个地方", False: "不在同一个地方"}

_inflight = {}
_inflight_lock = threading.Lock()


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

    try:
        activity, at = db.get_yuxia_activity(conv_id)
        if activity and (not at or int(now_minutes) - int(at) <= 180):
            lines.append(f"你此刻正在做的事：{activity}")
    except Exception:
        pass

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


def _fallback(location, player_location, now_minutes):
    co_located = location == player_location
    base = f"现在是{clock.format_time(now_minutes)}，你在{location}，青梅在{player_location}。"
    if co_located:
        return base + "你们在一个地方，可以当面说话。"
    return base + "你们不在一起，只能靠手机联系。"


def _generate(db, conv_id, now_minutes, location, player_location, deepseek, api_key, turn):
    """真正调用 LLM 撰写并写入缓存（可能在后台线程里执行）"""
    # 只看两个对话通道：state（旁白描写）不该被当成"刚才说的话"再喂回去
    recent_msgs = [m for m in db.get_messages(conv_id)
                   if (m.get("channel") or "chat") in ("chat", "phone")][-8:]
    events = db.get_recent_events(conv_id, limit=4)
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

    db.set_context_brief(conv_id, pronoun.fix(text.strip()), turn)
    return pronoun.fix(text.strip())


def _refresh_async(db, conv_id, now_minutes, location, player_location, deepseek, api_key, turn):
    """后台刷新：同一会话同一时刻只跑一个"""
    with _inflight_lock:
        if _inflight.get(conv_id):
            return
        _inflight[conv_id] = True

    def run():
        try:
            _generate(db, conv_id, now_minutes, location, player_location,
                      deepseek, api_key, turn)
        except Exception as e:
            print(f"[情境] 后台刷新失败: {e}")
        finally:
            with _inflight_lock:
                _inflight.pop(conv_id, None)

    threading.Thread(target=run, daemon=True).start()


def compose(db, conv_id, now_minutes, location, player_location, deepseek,
            api_key=None, turn=0, force=False):
    """取自然情境叙述；带缓存，必要时后台刷新"""
    cached_text, cached_turn = db.get_context_brief(conv_id)
    stale = force or not cached_text or (turn - cached_turn) >= CONTEXT_WRITER_INTERVAL

    if cached_text and not stale:
        return cached_text

    if CONTEXT_WRITER_BACKGROUND:
        # 先返回能用的内容，别让玩家等
        immediate = cached_text if (cached_text and not force) else \
            _fallback(location, player_location, now_minutes)
        _refresh_async(db, conv_id, now_minutes, location, player_location,
                       deepseek, api_key, turn)
        return immediate

    return _generate(db, conv_id, now_minutes, location, player_location,
                     deepseek, api_key, turn)
