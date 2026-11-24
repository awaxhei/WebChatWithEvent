"""离线结算：你不在的时候，她照常过日子

时间锚定真实世界之后，「你离开了多久」是能算出来的。按日程段的边界结算：

- 只结算**已经结束**的段（还在上的课只说明「她还在上课」）
- 每段用一次模型调用，生成这段时间里发生的事 + 她想发给你的消息
- 她真的会发消息：分享、问吃什么、约时间——频率比你在时低，内容贴着日程走
- 睡觉的段不发消息，也不打扰
- 回来第一句话之前把这段补进历史，你看历史就是「这段时间发生了什么」

结算的结果写进库里（事件 + 手机消息），通知服务会照常把它们推给手机。
"""
import json
import threading
import time

from config import (
    OFFLINE_SETTLE_MIN_MINUTES, OFFLINE_MAX_MESSAGES, OFFLINE_PROMPT,
)
import core.clock as clock
import services.schedule as schedule

_inflight = {}
_lock = threading.Lock()


def gap_minutes(db, conv_id, now_minutes):
    """玩家离开多久了（故事分钟）"""
    try:
        seen = float(db.get_last_seen_at(conv_id) or 0)
    except Exception:
        seen = 0.0
    if not seen:
        return 0
    return max(0, int((time.time() - seen) // 60))


def should_settle(db, conv_id, now_minutes):
    """离开够久、而且有已经结束的日程段，才值得花一次调用"""
    gap = gap_minutes(db, conv_id, now_minutes)
    if gap < OFFLINE_SETTLE_MIN_MINUTES:
        return 0
    from_minutes = int(now_minutes) - gap
    blocks = schedule.blocks_between(from_minutes, now_minutes, "yuxia", limit=4)
    return len(blocks)


def _facts(db, conv_id, now_minutes, gap, blocks):
    yuxia_loc = db.get_current_location(conv_id)
    lines = [
        f"【你离开了 {gap} 分钟】",
        f"【现在】{clock.format_time(now_minutes)}",
        f"【她此刻】{schedule.block_at(now_minutes, 'yuxia')['activity']}",
        f"【她这段时间的日程】",
    ]
    for b in blocks:
        lines.append(f"- {clock.format_time(b['start'])}–{clock.format_time(b['end'])}："
                     f"{b['activity']}（在{b['location']}）")
    try:
        events = db.get_recent_events(conv_id, limit=4)
    except Exception:
        events = []
    if events:
        lines.append("【最近发生的事】")
        for e in events[-4:]:
            lines.append(f"- {e['title']}：{e['description']}")
    try:
        recent = [m for m in db.get_messages(conv_id)
                  if (m.get("channel") or "chat") in ("chat", "phone")][-6:]
    except Exception:
        recent = []
    if recent:
        lines.append("【你们最后说的话】")
        for m in recent:
            who = "青梅" if m["role"] == "user" else "尤夏"
            lines.append(f"- {who}：{m.get('content')}")
    return "\n".join(lines)


def _parse(raw):
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def settle(db, conv_id, deepseek, now_minutes, api_key=None, force=False):
    """结算一次离线；返回写进库的消息条数

    她睡着的时候不结算、更不推送：夜里不该被消息吵醒。
    """
    if schedule.block_at(now_minutes, "yuxia")["reply"] == "asleep" and not force:
        return 0
    if not force and should_settle(db, conv_id, now_minutes) <= 0:
        return 0
    gap = gap_minutes(db, conv_id, now_minutes)
    if gap < OFFLINE_SETTLE_MIN_MINUTES and not force:
        return 0
    from_minutes = int(now_minutes) - max(gap, 1)
    blocks = schedule.blocks_between(from_minutes, now_minutes, "yuxia", limit=4)
    if not blocks:
        return 0

    facts = _facts(db, conv_id, now_minutes, gap, blocks)
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": facts}],
            system_prompt=OFFLINE_PROMPT,
            temperature=0.8,
            api_key_override=api_key,
            max_tokens=700,
        )
    except Exception as e:
        print(f"[离线] 结算失败: {e}")
        return 0
    data = _parse(raw)
    if not data:
        return 0

    written = 0
    for ev in (data.get("events") or [])[:3]:
        title = str(ev.get("title") or "").strip()[:24]
        desc = str(ev.get("description") or "").strip()[:200]
        if not title:
            continue
        try:
            # event_type 受 CHECK 约束限制：她自己的日常归到 misc（tier=self 标明是她做的）
            db.add_event(conv_id, "misc", title, desc, "completed",
                         story_time=clock.format_time(now_minutes), tier="self")
        except Exception:
            pass

    msgs = [m for m in (data.get("messages") or []) if isinstance(m, dict)]
    for item in msgs[:OFFLINE_MAX_MESSAGES]:
        text = str(item.get("text") or "").strip()[:120]
        if not text:
            continue
        at = item.get("minutes")
        try:
            when = int(at) if at is not None else int(now_minutes)
        except Exception:
            when = int(now_minutes)
        when = max(from_minutes, min(when, int(now_minutes)))
        db.add_message(conv_id, "assistant", text, channel="phone",
                       scene=schedule.block_at(when, "yuxia")["location"],
                       story_minutes=when)
        written += 1

    mood = str(data.get("mood") or "").strip()
    if mood:
        try:
            db.set_yuxia_mood(conv_id, mood)
        except Exception:
            pass
    return written


def settle_async(db, conv_id, deepseek, now_minutes, api_key=None):
    """后台结算：别让「你回来了」这句话等它"""
    with _lock:
        if _inflight.get(conv_id):
            return False
        _inflight[conv_id] = True

    def run():
        try:
            settle(db, conv_id, deepseek, now_minutes, api_key=api_key)
        except Exception as e:
            print(f"[离线] 后台结算失败: {e}")
        finally:
            with _lock:
                _inflight.pop(conv_id, None)

    threading.Thread(target=run, daemon=True).start()
    return True
