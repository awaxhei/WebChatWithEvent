"""
记忆系统编排 —— 写入 / 反思 / 事件落记忆

- 每轮对话把用户与尤夏的话写成"观察"记忆
- 世界引擎产生的里程碑、情绪转折等事件也会写成高重要度记忆
- 累计重要度超过阈值后，触发一次"反思"：由 LLM 归纳成更高层的认知
"""
import json
import re

import core.memory as memory
from config import MEMORY_REFLECTION_PROMPT

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

# 哪些事件值得进入长期记忆
_MEMORABLE_EVENTS = {"milestone", "emotional_shift", "scene_change"}


def ensure_backfill(db, conv_id, now_minutes):
    """老会话首次进入 v3 时，把历史消息导入记忆流（只做一次）"""
    try:
        return memory.backfill_if_empty(db, conv_id, now_minutes)
    except Exception as e:
        print(f"[记忆] 回填失败: {e}")
        return 0


def _extract_json_objects(text):
    """从模型输出里按括号配对提取 JSON 对象（容忍折行与多余文字）"""
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    out.append(json.loads(text[start:i + 1]))
                except json.JSONDecodeError:
                    pass
                start = -1
    return out


def readable_assistant_text(text):
    """把 AI A 的 JSON 输出转成"人能读的"一句话再写进记忆，避免记忆里全是花括号"""
    text = (text or "").strip()
    if not text.startswith("{"):
        return text
    parts = []
    for obj in _extract_json_objects(text):
        if not isinstance(obj, dict):
            continue
        if obj.get("动作"):
            parts.append(str(obj["动作"]))
        if obj.get("对白"):
            parts.append(str(obj["对白"]))
        if obj.get("消息"):
            parts.append("【消息】" + str(obj["消息"]))
        if obj.get("表情包"):
            parts.append(f"【表情包:{obj['表情包']}】")
    return " ".join(parts).strip() or text


def remember_user_message(db, conv_id, text, now_minutes):
    return memory.add(db, conv_id, f"青梅：{text}", kind=memory.OBSERVATION,
                      story_minutes=now_minutes)


def remember_assistant_message(db, conv_id, text, now_minutes):
    return memory.add(db, conv_id, f"尤夏：{readable_assistant_text(text)}",
                      kind=memory.OBSERVATION, story_minutes=now_minutes)


def remember_events(db, conv_id, events, now_minutes):
    """把世界引擎记录的重要事件写成高重要度记忆"""
    count = 0
    for event in events or []:
        etype = event.get("type") or event.get("event_type") or "misc"
        if etype not in _MEMORABLE_EVENTS:
            continue
        title = event.get("title") or ""
        desc = event.get("description") or ""
        if not title and not desc:
            continue
        text = f"{title}：{desc}" if title and desc else (title or desc)
        memory.add(db, conv_id, text, kind=memory.OBSERVATION,
                   importance=8, story_minutes=now_minutes,
                   meta={"event_type": etype})
        count += 1
    return count


def _parse_reflections(raw_text):
    if not raw_text or raw_text.startswith("[生成失败"):
        return []
    text = _FENCE_RE.sub("", raw_text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    items = data.get("reflections") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [str(x).strip()[:120] for x in items if str(x).strip()][:3]


def maybe_reflect(db, conv_id, deepseek, now_minutes, api_key=None):
    """累计重要度足够时，让 LLM 把零散观察归纳成高层认知"""
    if not memory.should_reflect(db, conv_id):
        return []

    observations = memory.observations_for_reflection(db, conv_id, limit=20)
    if len(observations) < 3:
        return []

    lines = "\n".join(f"- {o['text']}" for o in observations)
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": f"以下是最近的记忆片段：\n{lines}\n\n请归纳，输出 JSON。"}],
            system_prompt=MEMORY_REFLECTION_PROMPT,
            temperature=0.4,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[反思] 调用失败: {e}")
        return []

    reflections = _parse_reflections(raw)
    if not reflections:
        return []

    memory.save_reflections(db, conv_id, reflections,
                            covers_upto=observations[-1]["id"],
                            story_minutes=now_minutes)
    return reflections


def context(db, conv_id, query, now_minutes):
    """检索相关记忆，格式化后注入 prompt"""
    return memory.build_context(db, conv_id, query, now_minutes)
