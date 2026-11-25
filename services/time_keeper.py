"""
故事时钟 —— 跟随真实世界的确定性时钟

分工：
- 服务器持有唯一的时间真值（conversations.story_minutes）
- 钟点就是本机此刻的钟点：新会话从现在的真实时间开始，第 N 天按会话建立那天起算
- 不打字时间也在走：读时钟时按上次落库的真实时刻补上经过的分钟数
- 按轮次加分钟、关键词跳时、时间观察者都已停用：模型和打字次数都改不了钟点，
  否则故事时间会跑到真实时间前面，手机上看到的就不再是真实世界
"""
import datetime as _dt
import json
import re
import time

import core.clock as clock
from config import (
    TIME_FOLLOW_REAL,
    TIME_KEYWORD_JUMP,
    TIME_OBSERVER_ENABLED, TIME_OBSERVER_INTERVAL, TIME_OBSERVER_PROMPT,
)

ALLOWED_JUMPS = {"NONE", "NEXT_MORNING", "NOON", "AFTERNOON", "EVENING", "LATE_NIGHT", "NEXT_DAY"}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


# 一次读取最多补多少分钟：防系统时钟被改坏时故事时间飞掉。
# 正常使用就是真实流逝，隔几天再打开也照样对得上。
IDLE_DRIFT_MAX_MINUTES = 31 * 24 * 60


def real_minutes(conv_id=None, db=None):
    """此刻的真实时间换算成故事分钟：第 N 天（按会话建立那天起算）+ 当前钟点"""
    now = _dt.datetime.now()
    day_index = 0
    if db is not None and conv_id:
        try:
            created = db.get_conversation_created_at(conv_id)
            if created:
                day_index = max(0, (now.date() - created.date()).days)
        except Exception:
            day_index = 0
    return day_index * 1440 + now.hour * 60 + now.minute


def init_clock(db, conv_id):
    """读取会话时钟。

    新会话从此刻的真实钟点开始（以前固定从 7:00 开始，于是手机上不管几点打开
    都是第一天早上），之后按真实流逝推进。所以「第N天 HH:MM」就是此刻的世界。
    """
    minutes = db.get_story_minutes(conv_id)

    if not minutes:
        if TIME_FOLLOW_REAL:
            minutes = real_minutes(conv_id, db)
        else:
            recovered = None
            try:
                recovered = clock.parse_display_time(db.get_latest_story_time(conv_id) or "")
            except Exception:
                recovered = None
            minutes = recovered or clock.initial_minutes()
        db.set_story_minutes(conv_id, minutes)
        return minutes

    try:
        anchored, at = db.get_story_anchor(conv_id)
    except Exception:
        anchored, at = int(minutes), 0.0
    if at:
        drift_min = int((time.time() - at) // 60)
        if drift_min > 0:
            return int(minutes) + min(drift_min, IDLE_DRIFT_MAX_MINUTES)
    return minutes


def apply_message_tick(db, conv_id, user_text):
    """取当前故事时间；跟随真实时间时不再按轮次加分钟，也不做关键词跳时。

    返回 (当前分钟数, 命中的跳跃类型或 None)
    """
    now = init_clock(db, conv_id)

    if TIME_FOLLOW_REAL:
        # 把补上的真实流逝落库，锚点随之更新，消息的时间戳就和真实时间一致
        try:
            db.set_story_minutes(conv_id, now)
        except Exception:
            pass
        return now, None

    now = clock.tick(now)

    jump = clock.detect_keyword_jump(user_text) if TIME_KEYWORD_JUMP else None
    applied = None
    if jump:
        new_now, delta = clock.apply_jump(now, jump)
        if delta > 0:
            now = new_now
            applied = jump

    db.set_story_minutes(conv_id, now)
    return now, applied


def should_observe(msg_count):
    """是否该询问时间观察者（隔轮询问，省 token）。

    跟随真实时间时，模型判出来的跳跃下一次读取就会被真实钟点覆盖，
    所以直接不问，省一次调用。
    """
    if TIME_FOLLOW_REAL or not TIME_OBSERVER_ENABLED:
        return False
    return msg_count % max(1, TIME_OBSERVER_INTERVAL) == 0


def _parse_intent(raw_text):
    if not raw_text or raw_text.startswith("[生成失败"):
        return None
    text = _FENCE_RE.sub("", raw_text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    jump = str(data.get("jump") or "NONE").strip().upper()
    if jump not in ALLOWED_JUMPS:
        jump = "NONE"
    return {"jump": jump, "reason": str(data.get("reason") or "")[:80]}


def observe(db, conv_id, deepseek, recent_text, api_key=None):
    """让时间观察者判断是否需要跳跃；需要则应用并返回详情"""
    if TIME_FOLLOW_REAL:
        return None            # 钟点由真实时间决定，模型说了不算
    now = init_clock(db, conv_id)
    user_msg = (
        f"【当前故事时间】{clock.format_time(now)}\n"
        f"【最近的对话】\n{recent_text}\n\n"
        "请判断这段对话是否隐含了较大的时间推进，输出 JSON。"
    )
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": user_msg}],
            system_prompt=TIME_OBSERVER_PROMPT,
            temperature=0.1,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[时间观察者] 调用失败: {e}")
        return None

    intent = _parse_intent(raw)
    if not intent or intent["jump"] == "NONE":
        return None

    new_now, delta = clock.apply_jump(now, intent["jump"])
    if delta <= 0:
        return None

    db.set_story_minutes(conv_id, new_now)
    return {
        "jump": intent["jump"],
        "reason": intent["reason"],
        "minutes": delta,
        "from": clock.format_time(now),
        "to": clock.format_time(new_now),
    }


def current_time_text(db, conv_id):
    return clock.format_time(init_clock(db, conv_id))
