"""
世界上下文：把「刚刚发生了什么」和「她此刻在做什么」拼成一段提示词素材。

之前这些信息散在各处，而且都是过期的：
- 随机事件只写进了 events 表，聊天提示词里根本没有，
  所以她看不到"水槽水管爆开"这种事，只能继续聊刚才的话题；
- 情境叙述（context_brief）是带缓存的，事件发生后要等好几轮才刷新；
- 她自己在做什么也没有任何地方记录。

这里统一从数据库实时取，每个需要"知道世界发生了什么"的调用方都用它。
"""
import core.clock as clock
from config import CHARACTER_NAME

# 事件的类型标签：让她一眼看出哪些是"眼前刚出的事"
_TYPE_HINT = {
    "random": "突发",
    "milestone": "关键进展",
    "scene_change": "换了地方",
    "emotional_shift": "情绪变化",
    "time_advance": "时间流逝",
}

# 她自己的 act 事件也进事件表（故事进展面板要显示），
# 但不能当成"世界发生的事"再喂回给她——那是她自己干的。
SELF_TIER = "self"


def _age_label(event, now_minutes):
    """把事件的时间戳换算成"距现在多久"。

    事件表里只有「第一天 07:10」这样的绝对时间，模型很容易把它读成"早上发生过的旧事"，
    于是明明刚响的敲门声，她会回一句"哦……早上那个"。
    """
    if now_minutes is None or not event.get("story_time"):
        return ""
    ev_minutes = clock.parse_display_time(event["story_time"])
    if ev_minutes is None:
        return ""
    delta = int(now_minutes) - int(ev_minutes)
    if delta <= 0:
        return "刚刚"
    if delta <= 5:
        return "刚刚"
    if delta <= 60:
        return f"{delta} 分钟前"
    if delta <= 240:
        return f"{delta // 60} 小时前"
    return ""


def recent_events_block(db, conv_id, limit=6, with_time=True, exclude_self=True,
                        now_minutes=None):
    """最近发生的事（时间正序，最后一条最新）"""
    try:
        events = db.get_recent_events(conv_id, limit=limit * 4 if exclude_self else limit)
    except Exception:
        return ""
    if exclude_self:
        events = [e for e in events if e.get("tier") != SELF_TIER][-limit:]
    if not events:
        return ""
    lines = ["【最近发生的事】（按时间先后，越靠下越新）"]
    for e in events:
        tag = _TYPE_HINT.get(e.get("event_type"), "")
        tag = f"（{tag}）" if tag else ""
        age = _age_label(e, now_minutes) if with_time else ""
        when = f"{age}｜" if age else (f"{e['story_time']}｜" if (with_time and e.get("story_time")) else "")
        desc = (e.get("description") or "").strip()
        title = (e.get("title") or "").strip()
        body = f"{title}：{desc}" if desc and desc != title else title
        lines.append(f"- {when}{body}{tag}")
    return "\n".join(lines)


def _strip_third_person(text):
    """她自己的动作用第三人称写（旁白要这么显示），但提示词里得是第二人称，
    否则模型会跟着用第三人称说话——问它"谁在沙发上"就回一个"……尤夏。" """
    t = (text or "").strip()
    for prefix in ("尤夏", "她"):
        if t.startswith(prefix):
            t = t[len(prefix):].lstrip("，,、 ")
            break
    return t


def activity_line(db, conv_id, now_minutes=None):
    """她此刻在做什么；跨时段（超过 180 分钟）就当已经做完了"""
    try:
        text, at = db.get_yuxia_activity(conv_id)
    except Exception:
        return ""
    if not text:
        return ""
    if now_minutes is not None and at and (int(now_minutes) - int(at)) > 180:
        return ""
    return f"【你此刻在做】{_strip_third_person(text)}"


def self_actions_block(db, conv_id, now_minutes=None, limit=2, window=60):
    """她刚才自己做过的事（第二人称，少量），供对话提示词使用

    这一段是**玩家亲眼看到的画面**：她接下来开口时，手里的东西、身边的位置
    都得接着它，否则会出现「一会儿被子一会儿椅子」这种前后矛盾。
    """
    try:
        tail = db.get_autonomy_tail(conv_id)
    except Exception:
        return ""
    acts = [t for t in tail if t.get("action") == "act" and t.get("text")]
    if now_minutes is not None:
        keep = []
        for t in acts:
            at = int(t.get("at_minutes") or 0)
            if not at or int(now_minutes) - at <= window:
                keep.append(t)
        acts = keep
    acts = acts[-limit:]
    if not acts:
        return ""
    lines = ["【你刚才做的动作】（玩家亲眼看到，这就是此刻的画面）"]
    for t in acts:
        lines.append(f"- {_strip_third_person(t['text'])}")
    lines.append("接下来这几句话得接着它：你手上拿的、身边占着的，还是这些东西，"
                 "不要换成另一个名词；要换就先写一个放下/递过去的动作。")
    return "\n".join(lines)


def recent_activity_block(db, conv_id, limit=4):
    """她最近自己做过的事（来自自主行动流水，用于防重复）"""
    try:
        tail = db.get_autonomy_tail(conv_id)
    except Exception:
        return ""
    acts = [t for t in tail if (t.get("action") in ("act", "speak", "phone") and t.get("text"))]
    if not acts:
        return ""
    lines = ["【你最近自己做过的事】（不要重复）"]
    for t in acts[-limit:]:
        kind = {"act": "动手做了", "speak": "当面说过", "phone": "发过消息"}.get(t["action"], "做过")
        lines.append(f"- {kind}：{_strip_third_person(t['text'])}")
    return "\n".join(lines)


def world_block(db, conv_id, now_minutes=None, events_limit=6):
    """给对话/自主行动提示词用的世界块：此刻时间 + 最近发生的事 + 她正在做的事"""
    parts = []
    tl = time_line(now_minutes)
    if tl:
        parts.append(tl)
    ev = recent_events_block(db, conv_id, limit=events_limit, now_minutes=now_minutes)
    if ev:
        parts.append(ev)
    act = activity_line(db, conv_id, now_minutes)
    if act:
        parts.append(act)
    return "\n".join(parts)


def time_line(now_minutes):
    if now_minutes is None:
        return ""
    return f"【此刻】{clock.format_time(now_minutes)}"


def place_block(db, conv_id, yuxia_location, player_location):
    """【你此刻在哪】—— 地点是每轮都得重新告诉她的实时事实。

    玩家在界面上换场景时不会说话，「最近发生的事」里那条事件也会随轮次滚出去，
    所以位置本身必须每轮现取，不能指望缓存的情境叙述（它可能还写着"你在客厅"）。
    """
    if not yuxia_location:
        return ""
    lines = [f"【你此刻在哪】{yuxia_location}"]
    if player_location:
        if player_location == yuxia_location:
            lines.append(f"【青梅此刻在哪】{yuxia_location}——和你在同一个地方，可以当面说话。")
        else:
            lines.append(f"【青梅此刻在哪】{player_location}——你们不在一起，只能用手机。")
    return "\n".join(lines)


def scene_line(db, conv_id, location, now_minutes):
    """此地的样子（该地点该时段的描写，已缓存在库里，不额外花钱）"""
    if not location or now_minutes is None:
        return ""
    try:
        import core.scenes as scenes
        bucket = scenes.bucket_of(now_minutes)
        desc = (db.get_location_variants(conv_id, location) or {}).get(bucket, "")
    except Exception:
        return ""
    if not desc:
        return ""
    return f"【此地的样子（{bucket}，你清楚就行，不要复述）】{desc[:120]}"


__all__ = [
    "recent_events_block", "activity_line", "recent_activity_block",
    "self_actions_block", "world_block", "time_line", "place_block", "scene_line",
    "CHARACTER_NAME", "SELF_TIER",
]
