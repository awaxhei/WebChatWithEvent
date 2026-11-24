"""
场景系统 —— 环境描写绑定「地点 + 时段」，进入场景才切换，不再逐轮随机生成

同一地点在不同时段各有一份固定描写，首次进入时由 LLM 生成并缓存到数据库，
之后重复进入直接复用（零 token）。
"""
import json
import threading

from config import SCENE_TIME_BUCKETS, SCENE_DESCRIPTION_PROMPT
import core.pronoun as pronoun

BUCKETS = [b[0] for b in SCENE_TIME_BUCKETS]
DEFAULT_BUCKET = "白天"

_inflight = set()
_inflight_lock = threading.Lock()


def bucket_of(minutes):
    """根据故事分钟数判断时段名"""
    hour = (int(minutes) % 1440) // 60
    for name, start, end in SCENE_TIME_BUCKETS:
        if start < end:
            if start <= hour < end:
                return name
        else:  # 跨零点（夜晚）
            if hour >= start or hour < end:
                return name
    return DEFAULT_BUCKET


def _fallback(location, bucket):
    return f"{bucket}的{location}，光线和声音都安静下来，空气里有种熟悉的味道。"


def _parse_scene(raw):
    """场景输出可以是 JSON（带道具），也可以是纯文本（老缓存/模型不听话）"""
    text = (raw or "").strip().strip('"”').strip()
    props = []
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            text = str(data.get("环境") or data.get("描写") or "").strip()
            for item in (data.get("道具") or [])[:12]:
                if isinstance(item, dict):
                    props.append(item)
    if not text:
        return "", []
    for prefix in ("【地点】", "地点：", "描写：", "环境："):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text[:200], props


def generate_description(location, bucket, deepseek, api_key=None, known_props=None):
    """让 LLM 写一段该地点该时段的环境描写（纯写景，无人物动作）

    同时给一批「道具」：这些东西会落库，之后可以被角色拿动、改变状态，
    而不是写完就消失的文学修饰。
    known_props: 这里已经有的东西，重新生成描写时要沿用，别凭空换一批。
    """
    facts = f"地点：{location}\n时段：{bucket}"
    if known_props:
        listing = "、".join(f"{p['name']}（{p.get('state') or ''}）".rstrip("（）")
                            if isinstance(p, dict) else str(p) for p in known_props[:12])
        facts += (f"\n这里已经有的东西：{listing}"
                  "\n这些要保留在原处或写清现在怎么变了，不要凭空换一批东西。")
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": facts}],
            system_prompt=SCENE_DESCRIPTION_PROMPT,
            temperature=0.9,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[场景] 生成失败: {e}")
        return _fallback(location, bucket), []

    if not raw or raw.startswith("[生成失败"):
        return _fallback(location, bucket), []

    text, props = _parse_scene(raw)
    return pronoun.fix(text) or _fallback(location, bucket), props


def _known(db, conv_id, location):
    """这个地点已经有的东西（重新生成描写时要沿用）"""
    try:
        import core.props as props_mod
        return props_mod.items(db, conv_id, location)
    except Exception:
        return []


def _save_props(db, conv_id, location, props):
    if not props:
        return
    try:
        import core.props as props_mod
        props_mod.remember(db, conv_id, location, props)
    except Exception as e:
        print(f"[场景] 道具落库失败: {e}")


def ensure_description(db, conv_id, location, minutes, deepseek, api_key=None,
                       force=False, background=False):
    """取该地点该时段的环境描写；没有就生成并缓存

    background=True 时不在请求线程里生成（避免首屏等 3-7 秒），
    而是返回空串并起后台线程生成，前端可稍后重取。

    返回 (时段名, 描写文本, 是否已生成)
    """
    bucket = bucket_of(minutes)
    variants = db.get_location_variants(conv_id, location)
    cached = variants.get(bucket)
    if cached and not force:
        return bucket, cached, True

    if background:
        _spawn_generation(db, conv_id, location, bucket, deepseek, api_key)
        return bucket, cached or "", False

    text, props = generate_description(
        location, bucket, deepseek, api_key=api_key,
        known_props=_known(db, conv_id, location))
    db.set_location_variant(conv_id, location, bucket, text)
    _save_props(db, conv_id, location, props)
    return bucket, text, True


def _spawn_generation(db, conv_id, location, bucket, deepseek, api_key):
    key = (conv_id, location, bucket)
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)

    def run():
        try:
            text, props = generate_description(
                location, bucket, deepseek, api_key=api_key,
                known_props=_known(db, conv_id, location))
            db.set_location_variant(conv_id, location, bucket, text)
            _save_props(db, conv_id, location, props)
        except Exception as e:
            print(f"[场景] 后台生成失败: {e}")
        finally:
            with _inflight_lock:
                _inflight.discard(key)

    threading.Thread(target=run, daemon=True).start()


def scene_view(db, conv_id, location, minutes, deepseek, api_key=None, background=True):
    """给前端的场景视图：双方各自所在地点与描写

    background=True 时未生成的描写会让 pending=True，前端稍后重取。
    """
    bucket, yuxia_desc, ready = ensure_description(
        db, conv_id, location, minutes, deepseek, api_key=api_key, background=background)
    player_loc = db.get_player_location(conv_id)
    co_located = player_loc == location

    pending = not ready
    if co_located:
        player_desc = yuxia_desc
    else:
        _b, player_desc, player_ready = ensure_description(
            db, conv_id, player_loc, minutes, deepseek, api_key=api_key, background=background)
        pending = pending or not player_ready

    return {
        "location": location,               # 尤夏所在地
        "bucket": bucket,
        "description": yuxia_desc,          # 兼容旧字段
        "yuxia_description": yuxia_desc,
        "player_location": player_loc,
        "player_description": player_desc,
        "co_located": co_located,
        "pending": pending,
    }
