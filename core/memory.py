"""
记忆流（参考斯坦福 AI 小镇 Generative Agents）

每条记忆包含：内容、类型（观察/反思/计划）、重要度、发生的故事时间、向量。
检索得分 = 时效性 * 0.5 + 重要度 * 0.3 + 相关性 * 0.2

设计要点：
- 检索靠"打分排序"，而不是把整段历史摘要塞进 prompt
- 零散观察累计到一定重要度后，由 LLM 归纳成"反思"（更高层认知）
- 没有 embedding 能力时，相关性自动降级为中文词面相似度，不至于失效
"""
import json
import re

import core.textvec as tv
from config import (
    MEMORY_TOP_K, MEMORY_RECENCY_DECAY,
    MEMORY_WEIGHT_RECENCY, MEMORY_WEIGHT_IMPORTANCE, MEMORY_WEIGHT_RELEVANCE,
    MEMORY_MAX_CHARS, MEMORY_REFLECTION_THRESHOLD,
)

OBSERVATION = "observation"
REFLECTION = "reflection"
PLAN = "plan"

_LABEL = {OBSERVATION: "记忆", REFLECTION: "认知", PLAN: "打算"}

_IMPORTANT_RE = re.compile(
    r"喜欢|讨厌|对不起|抱歉|约定|第一次|永远|别走|不要走|抱|哭|生气|谢谢|"
    r"喜欢上|心动|表白|害羞|担心|等我|做梦|生日|纪念日"
)


def estimate_importance(text):
    """没有 LLM 时的便宜重要度估计（1-10）"""
    base = 3
    if not text:
        return base
    if len(text) > 60:
        base += 1
    if len(text) > 120:
        base += 1
    if _IMPORTANT_RE.search(text):
        base += 2
    if text.count("\n") >= 3:
        base += 1
    return max(1, min(10, base))


def add(db, conv_id, text, kind=OBSERVATION, importance=None, story_minutes=0, meta=None):
    """写入一条记忆（自动算重要度并生成向量）"""
    text = (text or "").strip()
    if not text:
        return None
    imp = importance if importance is not None else estimate_importance(text)
    imp = max(1, min(10, int(imp)))
    vec = tv.embed(text)
    return db.add_memory(conv_id, kind, text, imp, story_minutes, tv.to_blob(vec),
                         json.dumps(meta or {}, ensure_ascii=False))


def search(db, conv_id, query, now_minutes, top_k=None):
    """按 AI 小镇公式检索最相关的记忆"""
    top_k = top_k or MEMORY_TOP_K
    rows = db.get_memories(conv_id, limit=600)
    if not rows:
        return []

    query_vec = tv.embed(query) if query else None
    scored = []
    for row in rows:
        age_hours = max(0.0, (now_minutes - (row.get("story_minutes") or 0)) / 60.0)
        recency = MEMORY_RECENCY_DECAY ** age_hours
        importance = (row.get("importance") or 5) / 10.0
        if query:
            relevance = tv.similarity(query, query_vec, row["text"],
                                      tv.from_blob(row.get("embedding")))
        else:
            relevance = 0.0
        score = (MEMORY_WEIGHT_RECENCY * recency
                 + MEMORY_WEIGHT_IMPORTANCE * importance
                 + MEMORY_WEIGHT_RELEVANCE * relevance)
        row = dict(row)
        row["score"] = round(score, 4)
        row["recency"] = round(recency, 4)
        row["relevance"] = round(relevance, 4)
        scored.append(row)

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_k]


def build_context(db, conv_id, query, now_minutes, top_k=None):
    """把检索结果格式化成可直接注入 prompt 的记忆片段"""
    items = search(db, conv_id, query, now_minutes, top_k=top_k)
    if not items:
        return ""

    lines, total = [], 0
    for item in items:
        label = _LABEL.get(item.get("kind", OBSERVATION), "记忆")
        line = f"- [{label}] {item['text']}"
        if total + len(line) > MEMORY_MAX_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return "【尤夏记得的相关往事（按相关程度排序）】\n" + "\n".join(lines) + "\n"


def pending_weight(db, conv_id):
    """自上次反思以来累计的重要度，用于决定是否触发反思"""
    last = db.get_latest_reflection(conv_id)
    since_id = 0
    if last:
        try:
            since_id = int((json.loads(last.get("meta") or "{}")).get("covers_upto") or 0)
        except Exception:
            since_id = 0
    return db.get_observation_importance_sum(conv_id, since_id)


def should_reflect(db, conv_id):
    return pending_weight(db, conv_id) >= MEMORY_REFLECTION_THRESHOLD


def observations_for_reflection(db, conv_id, limit=20):
    """取出待反思的观察（上次反思之后的）"""
    last = db.get_latest_reflection(conv_id)
    since_id = 0
    if last:
        try:
            since_id = int((json.loads(last.get("meta") or "{}")).get("covers_upto") or 0)
        except Exception:
            since_id = 0
    rows = db.get_observations_since(conv_id, since_id, limit=limit)
    return rows


def save_reflections(db, conv_id, reflections, covers_upto, story_minutes):
    for text in reflections:
        add(db, conv_id, text, kind=REFLECTION,
            importance=9, story_minutes=story_minutes,
            meta={"covers_upto": covers_upto})


def backfill_if_empty(db, conv_id, now_minutes, step_minutes=3):
    """老会话首次升级到 v3 时，把历史消息导成记忆（只做一次）"""
    if db.count_memories(conv_id) > 0:
        return 0
    messages = db.get_messages(conv_id)
    if not messages:
        return 0
    recent = messages[-60:]
    start = max(0, now_minutes - len(recent) * step_minutes)
    count = 0
    for i, msg in enumerate(recent):
        speaker = "青梅" if msg["role"] == "user" else "尤夏"
        text = f"{speaker}：{msg['content']}"
        imp = estimate_importance(msg["content"]) + (1 if msg["role"] == "assistant" else 0)
        add(db, conv_id, text, kind=OBSERVATION, importance=imp,
            story_minutes=start + i * step_minutes)
        count += 1
    return count
