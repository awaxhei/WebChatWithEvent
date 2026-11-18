"""
尤夏的自主行动（v7：她自己判断 + 能动手做事 + 不原地打转）

v6 的问题（玩家实测反馈）：
1. 她读不到刚发生的随机事件 —— 事件只写进 events 表，提示词里没有；
   而带缓存的情境叙述要好几轮才刷新，于是她只能接着聊旧话题；
2. 一件事能说三遍 —— 没有"我最近已经说过/做过什么"的记忆和去重；
3. 她只能说话和走路，不能真的做点什么。

v7：
- 提示词里实时带上「最近发生的事」「你此刻在做」「你最近自己做过的事」
- 新增 act 行动：她可以真的动手做一件事（擦灶台、烧水、找东西），
  写进 activity（喂给后续所有提示词）并记一条事件
- 文本去重：与最近自己说过/做过的高度相似就丢弃，避免车轱辘话
- 连续自主次数把 act 也算进去，防止无限"做事"刷屏
"""
import json
import re
import time

import core.clock as clock
import core.locations as locations
import services.world_context as world_context
from config import (
    AUTONOMY_ENABLED, AUTONOMY_PROMPT, AUTONOMY_ALLOW_NEW_LOCATION,
    AUTONOMY_MAX_CONSECUTIVE, AUTONOMY_MIN_INTERVAL_SECONDS,
    AUTONOMY_ACT_MINUTES, AUTONOMY_ACT_WINDOW_MINUTES, AUTONOMY_ACT_PER_WINDOW,
    AUTONOMY_SPEAK_MINUTES, AUTONOMY_AFTER_ACT_GRACE_SECONDS,
    AUTONOMY_SPEAK_WAIT_SECONDS, AUTONOMY_PHONE_WAIT_SECONDS,
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
VALID_ACTIONS = {"none", "speak", "phone", "move", "act"}

_TAIL_KEEP = AUTONOMY_MAX_CONSECUTIVE + 4  # 流水要够长，才能把连续次数算满

# 与最近说过/做过的话相似到这个程度，就认为是在重复
_SIMILARITY_LIMIT = 0.68


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


# ──────────────────────────────────────────────
# 去重：她是不是在重复自己
# ──────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[\s，。、！？…~～,.!?\"'“”‘’（）()「」【】:：;；—\-]+")


def _normalize(text):
    return _PUNCT_RE.sub("", (text or "")).lower()


def _bigrams(text):
    s = _normalize(text)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _similar(a, b):
    """字符二元组：Jaccard 抓整体改写，containment 抓"多加/少说几个字"

    "去关水阀，把地上的水擦掉" vs "去关水阀，顺手把地上的水擦掉"
    Jaccard 只有 0.69，但后者几乎完整包含前者——只靠 Jaccard 会漏掉这种重复。
    """
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    jaccard = inter / (len(ba) + len(bb) - inter)
    containment = inter / min(len(ba), len(bb))
    return max(jaccard, containment * 0.95)


def _too_similar(text, recent_texts):
    if not text:
        return True
    for old in recent_texts:
        if not old:
            continue
        if _normalize(text) and _normalize(text) in _normalize(old):
            return True
        if _similar(text, old) >= _SIMILARITY_LIMIT:
            return True
    return False


def _readable(content):
    """她存在 chat 通道里的是 {"对白": ...} 这样的 JSON，取出来做比较"""
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            parts = [str(obj.get(k, "")).strip() for k in ("对白", "消息") if obj.get(k)]
            return " ".join(parts) or content
    except Exception:
        pass
    return content


def _recent_self_texts(db, conv_id, limit=6):
    """她最近说过 + 做过的话（跨通道），用于去重"""
    texts = []
    for m in reversed(db.get_messages(conv_id)):
        if m["role"] != "assistant":
            continue
        if (m.get("channel") or "chat") not in ("chat", "phone"):
            continue
        texts.append(_readable(m["content"]))
        if len(texts) >= limit:
            break
    try:
        for entry in reversed(db.get_autonomy_tail(conv_id)):
            if entry.get("text"):
                texts.append(entry["text"])
    except Exception:
        pass
    return texts[:limit + 4]


# ──────────────────────────────────────────────
# 触发限制：只防刷屏，是否行动由模型判断
# ──────────────────────────────────────────────

def _assistant_streak(db, conv_id):
    """统计"最后一条用户消息之后"尤夏一共输出了几次（含回复、自主发言、动手）"""
    streak = 0
    for msg in reversed(db.get_messages(conv_id)):
        if msg["role"] == "user":
            break
        if (msg.get("channel") or "chat") in ("chat", "phone"):
            streak += 1
    last_user_at = db.get_last_user_at(conv_id)
    try:
        for entry in db.get_autonomy_tail(conv_id):
            # 用 >=：Windows 上 time.time() 精度约 15ms，
            # 刚发完消息就动手时两个时间戳可能完全相等
            if float(entry.get("at") or 0) >= last_user_at:
                streak += 1
    except Exception:
        pass
    return streak


def _autonomy_after_last_user(db, conv_id):
    """玩家最后一次开口之后，她自己做过的所有事（按时间正序）"""
    last_user_at = db.get_last_user_at(conv_id)
    try:
        tail = db.get_autonomy_tail(conv_id)
    except Exception:
        return []
    return [t for t in tail if float(t.get("at") or 0) >= last_user_at]


def _wait_seconds_needed(db, conv_id):
    """她说完话之后，该等玩家多久才能再自己动。

    - 普通说话（对白）：等 AUTONOMY_SPEAK_WAIT_SECONDS
    - 发手机消息：等 AUTONOMY_PHONE_WAIT_SECONDS（玩家得先看到红点再打开手机）
    - 已经动过手又补过话：等 AUTONOMY_AFTER_ACT_GRACE_SECONDS，把话筒彻底交回去
    """
    at, channel = db.get_last_yuxia(conv_id)
    if not at:
        return (0, 0.0)
    recent = _autonomy_after_last_user(db, conv_id)
    if any(t.get("action") == "act" for t in recent) and recent[-1].get("action") != "act":
        return (AUTONOMY_AFTER_ACT_GRACE_SECONDS, at)
    if channel == "act":
        # 刚动完手还没说话：允许她马上补一句（这是交互感的关键），别等太久
        return (AUTONOMY_MIN_INTERVAL_SECONDS, at)
    if channel == "phone":
        return (AUTONOMY_PHONE_WAIT_SECONDS, at)
    return (AUTONOMY_SPEAK_WAIT_SECONDS, at)


def _waiting_for_player(db, conv_id, idle):
    """她刚说完话 → 先等玩家；等不及了（超过等待时长）才允许自己再动。"""
    need, at = _wait_seconds_needed(db, conv_id)
    if not need or not at:
        return False
    return (time.time() - at) < need


def _allowed(db, conv_id, idle, allow_wait=False):
    if not AUTONOMY_ENABLED:
        return False
    # 玩家按了暂停：连模型都不问，省额度也给玩家一个安静的世界
    if db.is_paused(conv_id):
        return False
    if _assistant_streak(db, conv_id) >= 1 + AUTONOMY_MAX_CONSECUTIVE:
        return False
    # 她刚发完消息 / 刚说完话：等玩家回，别转头就开始干活
    # （allow_wait=True 是给"跟过去"开的口子）
    if not allow_wait and _waiting_for_player(db, conv_id, idle):
        return False
    if idle:
        last = db.get_last_user_at(conv_id)
        if time.time() - last < AUTONOMY_MIN_INTERVAL_SECONDS:
            return False
    return True


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

def _scene_line(db, conv_id, yuxia_loc, now_minutes):
    try:
        import core.scenes as scenes
        bucket = scenes.bucket_of(now_minutes)
        desc = db.get_location_variants(conv_id, yuxia_loc).get(bucket, "")
    except Exception:
        bucket, desc = "", ""
    return f"此刻的场景（{bucket}）：{desc}\n" if desc else ""


def _build_prompt(db, conv_id, now_minutes, context_brief, yuxia_loc,
                  co_located, idle, idle_seconds, can_act=True, follow_only=False):
    recent = [m for m in db.get_messages(conv_id)
              if (m.get("channel") or "chat") in ("chat", "phone")][-8:]
    recent_text = "\n".join(
        f"{'青梅' if m['role'] == 'user' else '你'}"
        f"（{'手机' if m.get('channel') == 'phone' else '当面'}）：{_readable(m['content'])}"
        for m in recent
    )
    known = "、".join(locations.list_names(db, conv_id)[-20:])

    parts = [
        context_brief or "",
        ("青梅已经 %d 秒没有说话了。" % idle_seconds) if idle else "青梅刚刚说完话。",
        world_context.time_line(now_minutes),
        f"你在：{yuxia_loc}",
    ]
    scene_line = _scene_line(db, conv_id, yuxia_loc, now_minutes)
    if scene_line:
        parts.append(scene_line.rstrip("\n"))

    events = world_context.recent_events_block(db, conv_id, limit=6, now_minutes=now_minutes)
    if events:
        parts.append(events)

    activity = world_context.activity_line(db, conv_id, now_minutes)
    if activity:
        parts.append(activity)

    done = world_context.recent_activity_block(db, conv_id, limit=4)
    if done:
        parts.append(done)

    parts.append(f"最近的消息：\n{recent_text or '（还没有说话）'}")
    parts.append(f"已知地点：{known}")
    parts.append(f"你们当前{'' if co_located else '不'}在同一个地方。")
    if not can_act:
        # 提前告诉模型"这段时间别再动手"，比它选完 act 再被服务端丢掉划算
        parts.append("你这段时间已经动过手了，这次只能说话（speak / phone）或者不动（none）。")
    if follow_only:
        parts.append("你刚说完话，对方还在等你。现在只允许两个选择："
                     "如果你答应了要过去、或者本来就该过去，就 move 到对方在的地方；"
                     "否则 none。不要说话，也不要做别的事。")
    parts.append("请判断你此刻要不要接着做点什么。")
    return "\n\n".join(p for p in parts if p)


def maybe_act(db, conv_id, deepseek, now_minutes, context_brief, api_key=None,
              msg_count=0, idle=False):
    """问她要不要自主行动；返回行动详情或 None"""
    yuxia_loc = db.get_current_location(conv_id)
    player_loc = db.get_player_location(conv_id)
    co_located = yuxia_loc == player_loc

    # 她刚说完话、玩家还在场：这时唯一该允许的是"跟过去"（她嘴上答应了过去）
    follow_only = (not idle) and (not co_located) and _waiting_for_player(db, conv_id, idle)
    if not _allowed(db, conv_id, idle, allow_wait=follow_only):
        return None

    idle_seconds = int(time.time() - db.get_last_user_at(conv_id))

    user_msg = _build_prompt(db, conv_id, now_minutes, context_brief, yuxia_loc,
                             co_located, idle, idle_seconds,
                             can_act=_can_act(db, conv_id, now_minutes),
                             follow_only=follow_only)

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

    # 等待期只认"走过去"，别的话等她该等的时间过去
    if follow_only and action not in ("move", "none"):
        return None

    # 通道约束：不同处一地时不能"当面说话"
    if action == "speak" and not co_located:
        action = "phone"

    # 车轱辘话拦截：和最近说过/做过的高度重合就作罢
    if action in ("speak", "phone", "act") and text:
        if _too_similar(text, _recent_self_texts(db, conv_id)):
            print(f"[自主行动] 与最近内容重复，已丢弃：{text[:24]}")
            return None

    if action == "move":
        return _do_move(db, conv_id, decision, yuxia_loc, now_minutes)

    if action == "act" and text:
        if not _can_act(db, conv_id, now_minutes):
            print("[自主行动] 这段时间已经动过手了，跳过 act")
            return None
        return _do_act(db, conv_id, text, yuxia_loc, now_minutes, co_located)

    if action in ("speak", "phone") and text:
        channel = "phone" if action == "phone" else "chat"
        db.add_message(conv_id, "assistant", text, channel=channel, scene=yuxia_loc)
        db.push_autonomy(conv_id, {"action": action, "text": text, "at": time.time(),
                                   "at_minutes": int(now_minutes)},
                         keep=_TAIL_KEEP)
        # 说话也占一点时间，否则一整天停在同一个分钟里
        try:
            db.set_story_minutes(conv_id, int(now_minutes) + AUTONOMY_SPEAK_MINUTES)
        except Exception:
            pass
        db.set_last_yuxia_at(conv_id, time.time(), channel)
        return {"action": action, "text": text, "channel": channel, "visible": True}

    return None


def _act_budget_left(db, conv_id, now_minutes):
    """同一时间窗内的动手次数预算。

    没有这个限制时，闲置轮询每 8 秒一次、而 act 不推进故事时间，
    会出现"07:03 起床 → 07:03 开冰箱 → 07:03 拿锅 → 07:03 敲蛋"这种
    一分钟内做完一整顿早饭、且完全盖过对话的情况。
    """
    try:
        tail = db.get_autonomy_tail(conv_id)
    except Exception:
        return True
    recent = [t for t in tail
              if t.get("action") == "act"
              and int(now_minutes) - int(t.get("at_minutes") or 0) < AUTONOMY_ACT_WINDOW_MINUTES]
    return len(recent) < AUTONOMY_ACT_PER_WINDOW


def _can_act(db, conv_id, now_minutes):
    """能不能动手：预算之内，并且不连着动两次（中间必须有话）"""
    if not _act_budget_left(db, conv_id, now_minutes):
        return False
    try:
        tail = db.get_autonomy_tail(conv_id)
    except Exception:
        return True
    return not (tail and tail[-1].get("action") == "act")


def _do_move(db, conv_id, decision, yuxia_loc, now_minutes):
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
    db.clear_yuxia_activity(conv_id)
    db.add_event(conv_id, "scene_change",
                 f"尤夏去了{new_loc}" + ("（新场景）" if created else ""),
                 "她自己走开了。", "active",
                 story_time=clock.format_time(now_minutes))
    db.push_autonomy(conv_id, {"action": "move", "text": f"去了{new_loc}", "at": time.time(),
                               "at_minutes": int(now_minutes)},
                     keep=_TAIL_KEEP)
    # 走路也要花时间
    try:
        db.set_story_minutes(conv_id, int(now_minutes) + AUTONOMY_ACT_MINUTES)
    except Exception:
        pass
    db.set_last_yuxia_at(conv_id, time.time(), "act")
    return {"action": "move", "location": new_loc, "text": "", "created": created, "visible": True}


_TITLE_SPLIT = re.compile(r"[，,。；;！!？?、]")


def _short_title(text, limit=16):
    """事件标题：取第一个句读之前的部分，避免从中间截断"""
    head = _TITLE_SPLIT.split(text.strip())[0].strip() or text.strip()
    return head[:limit]


def _do_act(db, conv_id, text, yuxia_loc, now_minutes, co_located):
    """她真的动手做一件事：更新「此刻在做」，并记一条事件"""
    text = text.strip()[:60]
    if not text:
        return None
    db.set_yuxia_activity(conv_id, text, now_minutes)
    # tier=self 标记这条事件是她自己干的，不能再当成"世界发生的事"喂回给她
    db.add_event(conv_id, "misc", _short_title(text), text, "completed",
                 story_time=clock.format_time(now_minutes),
                 tier=world_context.SELF_TIER)
    db.push_autonomy(conv_id, {"action": "act", "text": text, "at": time.time(),
                               "at_minutes": int(now_minutes)},
                     keep=_TAIL_KEEP)
    # 做事要占时间：否则多次行动全挤在同一分钟，看起来像一口气干完一整顿早饭
    try:
        db.set_story_minutes(conv_id, int(now_minutes) + AUTONOMY_ACT_MINUTES)
    except Exception:
        pass
    db.set_last_yuxia_at(conv_id, time.time(), "act")
    # 不在同一个地方时，玩家看不到，只在故事进展里留痕
    return {"action": "act", "text": text, "activity": text,
            "location": yuxia_loc, "visible": bool(co_located)}
