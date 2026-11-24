"""尤夏与青梅的日程表（动态生成，按故事日缓存）

日程不再是写死的表：每个故事日第一次用到时，由模型根据人设、真实星期几、
最近发生的事生成当天的作息，存进 `day_schedules`，之后当天都用它。
生成失败或还没生成好时，退回一份兜底模板（和以前写死的表一样），不会卡住聊天。

每个时段（block）：

    start/end   分钟（0..1440）
    activity    在做什么（给提示词看的一句话）
    location    在哪儿（地名）
    reply       她回消息的状态：free / busy / asleep
    enterable   这段时间玩家能不能进那个地方
    tag         class / meal / rest / free / sleep / other
"""
import json
import threading

from config import SCHEDULE_PROMPT

# 兜底模板：模型还没生成、或生成失败时用
FALLBACK_YUXIA = [
    (0, 7 * 60, "睡着", "尤夏的房间", "asleep", False, "sleep"),
    (7 * 60, 7 * 60 + 40, "刚起床，洗漱", "尤夏的房间", "busy", False, "morning"),
    (7 * 60 + 40, 8 * 60, "在厨房弄早饭", "厨房", "free", True, "meal"),
    (8 * 60, 11 * 60 + 40, "在上课", "大学教室", "busy", False, "class"),
    (11 * 60 + 40, 12 * 60 + 40, "在食堂吃午饭", "食堂", "free", True, "meal"),
    (12 * 60 + 40, 14 * 60, "回房间午休", "尤夏的房间", "busy", False, "rest"),
    (14 * 60, 17 * 60 + 40, "在上课", "大学教室", "busy", False, "class"),
    (17 * 60 + 40, 18 * 60 + 40, "在食堂吃晚饭", "食堂", "free", True, "meal"),
    (18 * 60 + 40, 21 * 60, "在图书馆写作业", "图书馆", "busy", False, "study"),
    (21 * 60, 23 * 60, "回公寓窝着", "公寓客厅", "free", True, "free"),
    (23 * 60, 24 * 60, "已经睡了", "尤夏的房间", "asleep", False, "sleep"),
]

FALLBACK_QINGMEI = [
    (0, 7 * 60 + 20, "睡觉", "青梅的房间", "asleep"),
    (7 * 60 + 20, 8 * 60, "洗漱、出门", "公寓玄关", "free"),
    (8 * 60, 11 * 60 + 40, "上课", "大学教室", "busy"),
    (11 * 60 + 40, 12 * 60 + 40, "午饭", "食堂", "free"),
    (12 * 60 + 40, 14 * 60, "午休", "青梅的房间", "busy"),
    (14 * 60, 17 * 60 + 40, "上课", "大学教室", "busy"),
    (17 * 60 + 40, 18 * 60 + 40, "晚饭", "食堂", "free"),
    (18 * 60 + 40, 22 * 60, "自习", "图书馆", "busy"),
    (22 * 60, 24 * 60, "回公寓", "公寓客厅", "free"),
]

VALID_REPLY = ("free", "busy", "asleep")
_inflight = {}
_lock = threading.Lock()


def day_of(minutes):
    return int(minutes) // (24 * 60)


def _fallback(who):
    if who == "yuxia":
        return [{"start": s, "end": e, "activity": a, "location": l,
                 "reply": r, "enterable": en, "tag": t}
                for s, e, a, l, r, en, t in FALLBACK_YUXIA]
    return [{"start": s, "end": e, "activity": a, "location": l,
             "reply": r, "enterable": True, "tag": "other"}
            for s, e, a, l, r in FALLBACK_QINGMEI]


def _norm_blocks(raw):
    """把模型给的时段列表洗成统一结构，按开始时间排序"""
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0, min(24 * 60, int(item.get("start"))))
            end = max(0, min(24 * 60, int(item.get("end"))))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        reply = str(item.get("reply") or "free")
        if reply not in VALID_REPLY:
            reply = "free"
        out.append({
            "start": start,
            "end": end,
            "activity": str(item.get("activity") or "")[:40],
            "location": str(item.get("location") or "")[:20],
            "reply": reply,
            "enterable": bool(item.get("enterable", True)),
            "tag": str(item.get("tag") or "other")[:12],
        })
    out.sort(key=lambda b: b["start"])
    return out


def _parse(raw):
    text = (raw or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            return None
        if isinstance(data, dict):
            return data
    return None


def _generate(db, conv_id, day, deepseek, api_key=None):
    """让模型排一天：尤夏 + 青梅各自的作息"""
    import datetime

    info = [
        f"【故事日】第 {day + 1} 天",
        f"【真实星期】周{'一二三四五六日'[datetime.datetime.now().weekday()]}",
    ]
    try:
        recent = db.get_recent_events(conv_id, limit=5)
    except Exception:
        recent = []
    if recent:
        info.append("【最近发生的事】")
        for e in recent:
            info.append(f"- {e.get('title')}：{e.get('description')}")
    facts = "\n".join(info)
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": facts}],
            system_prompt=SCHEDULE_PROMPT,
            temperature=0.6,
            api_key_override=api_key,
            max_tokens=900,
        )
    except Exception as e:
        print(f"[日程] 生成失败: {e}")
        return None
    data = _parse(raw)
    if not data:
        return None
    yuxia = _norm_blocks(data.get("yuxia"))
    qingmei = _norm_blocks(data.get("qingmei"))
    if len(yuxia) < 4:
        return None
    payload = {"yuxia": yuxia, "qingmei": qingmei or _fallback("qingmei")}
    try:
        db.set_day_schedule(conv_id, day, json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        print(f"[日程] 落库失败: {e}")
    return payload


def _generate_async(db, conv_id, day, deepseek, api_key=None):
    key = (conv_id, day)
    with _lock:
        if key in _inflight:
            return
        _inflight[key] = True

    def run():
        try:
            _generate(db, conv_id, day, deepseek, api_key)
        except Exception as e:
            print(f"[日程] 后台生成失败: {e}")
        finally:
            with _lock:
                _inflight.pop(key, None)

    threading.Thread(target=run, daemon=True).start()


def ensure_day(db, conv_id, minutes, deepseek=None, api_key=None):
    """取这一天的日程；没生成过就先给兜底模板，并在后台排一份真的"""
    day = day_of(minutes)
    try:
        cached = db.get_day_schedule(conv_id, day)
    except Exception:
        cached = None
    if cached:
        try:
            data = json.loads(cached)
            if isinstance(data, dict) and data.get("yuxia"):
                return data
        except Exception:
            pass
    if deepseek is not None:
        _generate_async(db, conv_id, day, deepseek, api_key)
    return {"yuxia": _fallback("yuxia"), "qingmei": _fallback("qingmei")}


def blocks_for(db, conv_id, minutes, who="yuxia", deepseek=None, api_key=None):
    data = ensure_day(db, conv_id, minutes, deepseek, api_key)
    return data.get(who) or _fallback(who)


def block_at(db, conv_id, minutes, who="yuxia", deepseek=None, api_key=None):
    """这一刻她/他在做什么"""
    m = int(minutes) % (24 * 60)
    for b in blocks_for(db, conv_id, minutes, who, deepseek, api_key):
        if b["start"] <= m < b["end"]:
            return b
    return {"activity": "空着", "location": "", "reply": "free",
            "enterable": True, "tag": "other", "start": 0, "end": 0}


def blocks_between(db, conv_id, from_minutes, to_minutes, who="yuxia",
                   limit=6, deepseek=None, api_key=None):
    """这段时间里已经结束的日程段（离线结算用）"""
    out = []
    if to_minutes <= from_minutes:
        return out
    day = day_of(from_minutes)
    while day * 1440 < int(to_minutes):
        data = ensure_day(db, conv_id, day * 1440, deepseek, api_key)
        for block in (data.get(who) or []):
            s = day * 1440 + block["start"]
            e = day * 1440 + block["end"]
            if e <= from_minutes or s >= to_minutes:
                continue
            if e > to_minutes:
                continue
            out.append(dict(block, start=s, end=e))
        day += 1
    return out[-limit:]


def describe(db, conv_id, minutes, who="yuxia", deepseek=None, api_key=None):
    """给提示词的一句话：她此刻在做什么"""
    b = block_at(db, conv_id, minutes, who, deepseek, api_key)
    if not b["activity"]:
        return ""
    suffix = {"asleep": "（睡着了）", "busy": "（忙，回消息会短、会慢）", "free": ""}
    return (f"【此刻的日程】{b['activity']}"
            + (f"，在{b['location']}" if b["location"] else "")
            + suffix.get(b["reply"], ""))


def player_may_enter(db, conv_id, location, minutes, deepseek=None, api_key=None):
    """玩家能不能进这个地方：上课中的教室进不去"""
    b = block_at(db, conv_id, minutes, "yuxia", deepseek, api_key)
    if b["tag"] == "class" and b["location"] == location and not b["enterable"]:
        return False, f"{b['activity']}，这时候进不去"
    return True, ""
