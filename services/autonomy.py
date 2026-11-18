"""
尤夏的自主行动（v6：由她自己判断，不掷骰子）

- 每轮对话结束后都会问她一次"要不要接着做点什么"，但不给概率，由她决定 none / speak / phone / move
- 玩家长时间不说话时，前端定时轮询 /api/idle，同样由她判断（只保留最小间隔防刷）
- 唯一硬限制是"连续自主输出上限"，避免她自言自语刷屏
"""
import json
import re
import time

import core.clock as clock
import core.locations as locations
from config import (
    AUTONOMY_ENABLED, AUTONOMY_PROMPT, AUTONOMY_ALLOW_NEW_LOCATION,
    AUTONOMY_MAX_CONSECUTIVE, AUTONOMY_MIN_INTERVAL_SECONDS,
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
VALID_ACTIONS = {"none", "speak", "phone", "move"}


def _parse(raw_text):
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
    action = str(data.get("action") or "none").lower()
    if action not in VALID_ACTIONS:
        action = "none"
    return {
        "action": action,
        "text": str(data.get("text") or "").strip()[:120],
        "location": str(data.get("location") or "").strip()[:20],
    }


def _assistant_streak(db, conv_id):
    """统计"最后一条用户消息之后"尤夏一共说了几句（含正常回复）"""
    streak = 0
    for msg in reversed(db.get_messages(conv_id)):
        if msg["role"] == "user":
            break
        if (msg.get("channel") or "chat") in ("chat", "phone"):
            streak += 1
    return streak


def _allowed(db, conv_id, idle):
    """只保留防刷与防刷屏的硬限制，是否行动交给 LLM 判断"""
    if not AUTONOMY_ENABLED:
        return False

    # 连续自主输出的硬上限（正常回复算 1 句）
    if _assistant_streak(db, conv_id) >= 1 + AUTONOMY_MAX_CONSECUTIVE:
        return False

    if idle:
        last = db.get_last_user_at(conv_id)
        if time.time() - last < AUTONOMY_MIN_INTERVAL_SECONDS:
            return False
    return True


def maybe_act(db, conv_id, deepseek, now_minutes, context_brief, api_key=None,
              msg_count=0, idle=False):
    """问她要不要自主行动；返回行动详情或 None"""
    if not _allowed(db, conv_id, idle):
        return None

    yuxia_loc = db.get_current_location(conv_id)
    player_loc = db.get_player_location(conv_id)
    co_located = yuxia_loc == player_loc

    recent = [m for m in db.get_messages(conv_id) if (m.get("channel") or "chat") in ("chat", "phone")][-8:]
    recent_text = "\n".join(
        f"{'青梅' if m['role'] == 'user' else '你'}"
        f"（{'手机' if m.get('channel') == 'phone' else '当面'}）：{m['content']}"
        for m in recent
    )
    known = "、".join(locations.list_names(db, conv_id)[-20:])

    idle_seconds = int(time.time() - db.get_last_user_at(conv_id))
    timing = (f"青梅已经 {idle_seconds} 秒没有说话了。" if idle
              else "青梅刚刚说完话。")

    user_msg = (
        f"{context_brief}\n\n"
        f"{timing}\n"
        f"最近的消息：\n{recent_text or '（还没有说话）'}\n\n"
        f"已知地点：{known}\n"
        f"你们当前{'' if co_located else '不'}在同一个地方。\n"
        "请判断你此刻要不要接着做点什么。"
    )

    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": user_msg}],
            system_prompt=AUTONOMY_PROMPT,
            temperature=0.8,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[自主行动] 调用失败: {e}")
        return None

    decision = _parse(raw)
    if not decision or decision["action"] == "none":
        return None

    action = decision["action"]
    text = decision["text"]

    # 通道约束：不同处一地时不能"当面说话"
    if action == "speak" and not co_located:
        action = "phone"

    if action == "move":
        target = decision["location"]
        if not target:
            return None

        names = locations.list_names(db, conv_id)
        candidate = locations.normalize(target)
        if not candidate:
            return None
        if candidate not in names:
            if not AUTONOMY_ALLOW_NEW_LOCATION:
                return None
            if len(names) >= locations.MAX_LOCATIONS_PER_CONV:
                return None

        new_loc, created = locations.resolve(db, conv_id, target)
        if not new_loc or new_loc == yuxia_loc:
            return None
        locations.switch(db, conv_id, new_loc)
        db.add_event(conv_id, "scene_change",
                     f"尤夏去了{new_loc}" + ("（新场景）" if created else ""),
                     "她自己走开了。", "active",
                     story_time=clock.format_time(now_minutes))
        return {"action": "move", "location": new_loc, "text": "", "created": created}

    if action in ("speak", "phone") and text:
        channel = "phone" if action == "phone" else "chat"
        db.add_message(conv_id, "assistant", text, channel=channel, scene=yuxia_loc)
        return {"action": action, "text": text, "channel": channel}

    return None
