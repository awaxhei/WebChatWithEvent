"""
时间系统编排 —— 确定性时钟 + 时间观察者

分工：
- 服务器：持有唯一的时间真值（conversations.story_minutes），每轮自动 +N 分钟
- 关键词规则：命中"晚安/午饭/放学"等词时按固定规则跳（不经过 LLM）
- 时间观察者 LLM：只允许返回一个跳跃枚举，具体分钟数仍由 core.clock 换算
  因此 LLM 永远无法把时间写错，也无法让时间倒退
"""
import json
import re
import time

import core.clock as clock
from config import (
    TIME_KEYWORD_JUMP,
    TIME_OBSERVER_ENABLED, TIME_OBSERVER_INTERVAL, TIME_OBSERVER_PROMPT,
)

ALLOWED_JUMPS = {"NONE", "NEXT_MORNING", "NOON", "AFTERNOON", "EVENING", "LATE_NIGHT", "NEXT_DAY"}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


# 超过这个间隔（分钟）没说话，就认为时间在别的世界线里流过了，静默推过去
IDLE_DRIFT_MAX_MINUTES = 6 * 60


def init_clock(db, conv_id):
    """读取会话时钟；老会话首次进入时尝试从事件文本恢复，否则按 config 初始化

    时间已经**锚定真实世界**：读出来的故事时间 = 上次落库的故事时间
    + 距上次落库真实过去的时间。所以不打字的时候，时间也在走。
    """
    minutes = db.get_story_minutes(conv_id)
    if not minutes:
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
    """每轮基础推进 + 关键词跳跃。

    返回 (当前分钟数, 命中的跳跃类型或 None)
    """
    now = init_clock(db, conv_id)
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
    """是否该询问时间观察者（隔轮询问，省 token）"""
    if not TIME_OBSERVER_ENABLED:
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
