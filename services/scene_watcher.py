"""
场景观察者（v5）

职责：读对话上下文，发现「地点表之外的新场所」，并把它登记进地点表。
与自主行动的区别：
- 自主行动是"尤夏主动做点什么"（概率触发）
- 场景观察者每轮都跑，只做一件事：**跟随剧情把新地点登记下来并更新位置**
"""
import json
import re

import core.clock as clock
import core.locations as locations
from config import SCENE_WATCHER_ENABLED, SCENE_WATCHER_PROMPT

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
VALID_ENTERED = {"none", "player", "yuxia", "both"}


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

    entered = str(data.get("entered_by") or "none").strip().lower()
    if entered not in VALID_ENTERED:
        entered = "none"
    return {
        "new_location": str(data.get("new_location") or "").strip()[:20],
        "entered_by": entered,
        "reason": str(data.get("reason") or "")[:80],
    }


def observe(db, conv_id, deepseek, now_minutes, context_brief, api_key=None):
    """读上下文判断是否出现新场景；有则登记并更新双方位置。

    返回行动详情（含 scene 需要的字段）或 None。
    """
    if not SCENE_WATCHER_ENABLED:
        return None

    yuxia_loc = db.get_current_location(conv_id)
    player_loc = db.get_player_location(conv_id)
    known = "、".join(locations.list_names(db, conv_id))
    recent = db.get_messages(conv_id)[-6:]
    recent_text = "\n".join(
        f"{'青梅' if m['role'] == 'user' else '尤夏'}"
        f"（{'手机' if m.get('channel') == 'phone' else '当面'}）：{m['content']}"
        for m in recent
    )

    user_msg = (
        f"{context_brief}\n\n"
        f"地点表：{known}\n"
        f"尤夏现在在：{yuxia_loc}\n"
        f"青梅现在在：{player_loc}\n\n"
        f"最近的对话：\n{recent_text or '（还没有对话）'}\n\n"
        "请判断对话里是否出现了地点表之外的新场所，输出 JSON。"
    )

    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": user_msg}],
            system_prompt=SCENE_WATCHER_PROMPT,
            temperature=0.2,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[场景观察] 调用失败: {e}")
        return None

    decision = _parse(raw)
    if not decision or not decision["new_location"]:
        return None

    name = locations.normalize(decision["new_location"])
    if not name:
        return None

    existing = set(locations.list_names(db, conv_id))
    name, created = locations.resolve(db, conv_id, name)
    if not name:
        return None

    entered = decision["entered_by"]
    moved_player = moved_yuxia = False
    if entered in ("player", "both"):
        db.set_player_location(conv_id, name)
        moved_player = True
    if entered in ("yuxia", "both"):
        locations.switch(db, conv_id, name)
        moved_yuxia = True

    # 只是被提到、没人进去：登记但不改位置
    db.add_event(conv_id, "scene_change",
                 f"发现新场景：{name}" + ("" if created else "（已有）"),
                 decision.get("reason") or "对话里出现了新的场所。", "active",
                 story_time=clock.format_time(now_minutes))

    return {
        "location": name,
        "created": created,
        "is_new_to_table": name not in existing,
        "entered_by": entered,
        "moved_player": moved_player,
        "moved_yuxia": moved_yuxia,
        "reason": decision.get("reason", ""),
    }
