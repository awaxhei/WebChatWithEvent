"""
场景系统 —— 环境描写绑定「地点 + 时段」，进入场景才切换，不再逐轮随机生成

同一地点在不同时段各有一份固定描写，首次进入时由 LLM 生成并缓存到数据库，
之后重复进入直接复用（零 token）。
"""
import json
import threading

from config import SCENE_TIME_BUCKETS, SCENE_DESCRIPTION_PROMPT

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


def generate_description(location, bucket, deepseek, api_key=None):
    """让 LLM 写一段该地点该时段的环境描写（纯写景，无人物动作）"""
    user_msg = f"地点：{location}\n时段：{bucket}"
    try:
        text = deepseek.chat_sync(
            [{"role": "user", "content": user_msg}],
            system_prompt=SCENE_DESCRIPTION_PROMPT,
            temperature=0.9,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[场景] 生成失败: {e}")
        return _fallback(location, bucket)

    if not text or text.startswith("[生成失败"):
        return _fallback(location, bucket)

    text = text.strip().strip('"”').strip()
    # 去掉模型可能带出的标签或标题
    for prefix in ("【地点】", "地点：", "描写：", "环境："):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text[:200] or _fallback(location, bucket)


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

    text = generate_description(location, bucket, deepseek, api_key=api_key)
    db.set_location_variant(conv_id, location, bucket, text)
    return bucket, text, True


def _spawn_generation(db, conv_id, location, bucket, deepseek, api_key):
    key = (conv_id, location, bucket)
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)

    def run():
        try:
            text = generate_description(location, bucket, deepseek, api_key=api_key)
            db.set_location_variant(conv_id, location, bucket, text)
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
