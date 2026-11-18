"""
尤夏的自主行动（v4）

她不必等玩家输入：可以主动开口、发手机消息、或者自己走去别的场景。
- 每轮结束后按概率尝试（受冷却轮次限制）
- 玩家长时间没动静时，由前端轮询 /api/idle 触发（受真实时间阈值限制）
- 行动类型由 LLM 判断，服务端负责校验与落库
"""
import json
import random
import re
import time

import core.clock as clock
import core.locations as locations
from config import (
    AUTONOMY_ENABLED, AUTONOMY_CHANCE, AUTONOMY_COOLDOWN_TURNS,
    AUTONOMY_PROMPT, IDLE_CHANCE, IDLE_THRESHOLD_SECONDS,
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


def _gate(db, conv_id, msg_count, idle):
    if not AUTONOMY_ENABLED:
        return False
    if idle:
        last = db.get_last_user_at(conv_id)
        if time.time() - last < IDLE_THRESHOLD_SECONDS:
            return False
        return random.random() < IDLE_CHANCE
    if msg_count % max(1, AUTONOMY_COOLDOWN_TURNS) != 0:
        return False
    return random.random() < AUTONOMY_CHANCE


def maybe_act(db, conv_id, deepseek, now_minutes, context_brief, api_key=None,
              msg_count=0, idle=False):
    """尝试一次自主行动；返回行动详情或 None"""
    if not _gate(db, conv_id, msg_count, idle):
        return None

    yuxia_loc = db.get_current_location(conv_id)
    player_loc = db.get_player_location(conv_id)
    co_located = yuxia_loc == player_loc

    recent = db.get_messages(conv_id)[-6:]
    recent_text = "\n".join(
        f"{'青梅' if m['role'] == 'user' else '你'}"
        f"（{'手机' if m.get('channel') == 'phone' else '当面'}）：{m['content']}"
        for m in recent
    )
    known = "、".join(locations.list_names(db, conv_id)[-20:])
    user_msg = (
        f"{context_brief}\n\n"
        f"最近的消息：\n{recent_text or '（还没有说话）'}\n\n"
        f"你可以去的地点：{known}\n"
        f"你们当前{'' if co_located else '不'}在同一个地方。\n"
        "请判断你此刻要不要主动做点什么。"
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
    if action == "phone" and co_located and text:
        pass  # 同处一地也可以发手机消息（她的典型行为）

    if action == "move":
        target = decision["location"]
        if not target:
            return None
        new_loc, created = locations.resolve(db, conv_id, target)
        if not new_loc or new_loc == yuxia_loc:
            return None
        locations.switch(db, conv_id, new_loc)
        db.add_event(conv_id, "scene_change", f"尤夏去了{new_loc}",
                     "她自己走开了。", "active",
                     story_time=clock.format_time(now_minutes))
        return {"action": "move", "location": new_loc, "text": "", "created": created}

    if action in ("speak", "phone") and text:
        channel = "phone" if action == "phone" else "chat"
        db.add_message(conv_id, "assistant", text, channel=channel, scene=yuxia_loc)
        return {"action": action, "text": text, "channel": channel}

    return None
