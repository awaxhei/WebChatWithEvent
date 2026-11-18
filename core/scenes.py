"""
场景系统 —— 环境描写绑定「地点 + 时段」，进入场景才切换，不再逐轮随机生成

同一地点在不同时段各有一份固定描写，首次进入时由 LLM 生成并缓存到数据库，
之后重复进入直接复用（零 token）。
"""
import json

from config import SCENE_TIME_BUCKETS, SCENE_DESCRIPTION_PROMPT

BUCKETS = [b[0] for b in SCENE_TIME_BUCKETS]
DEFAULT_BUCKET = "白天"


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


def ensure_description(db, conv_id, location, minutes, deepseek, api_key=None, force=False):
    """取该地点该时段的环境描写；没有就生成并缓存

    返回 (时段名, 描写文本, 是否新生成)
    """
    bucket = bucket_of(minutes)
    variants = db.get_location_variants(conv_id, location)
    cached = variants.get(bucket)
    if cached and not force:
        return bucket, cached, False

    text = generate_description(location, bucket, deepseek, api_key=api_key)
    db.set_location_variant(conv_id, location, bucket, text)
    return bucket, text, True


def scene_view(db, conv_id, location, minutes, deepseek, api_key=None):
    """给前端的场景视图：双方各自所在地点与描写"""
    bucket, yuxia_desc, _ = ensure_description(db, conv_id, location, minutes,
                                               deepseek, api_key=api_key)
    player_loc = db.get_player_location(conv_id)
    co_located = player_loc == location

    if co_located:
        player_desc = yuxia_desc
    else:
        _bucket, player_desc, _ = ensure_description(db, conv_id, player_loc, minutes,
                                                     deepseek, api_key=api_key)

    return {
        "location": location,               # 尤夏所在地
        "bucket": bucket,
        "description": yuxia_desc,          # 兼容旧字段
        "yuxia_description": yuxia_desc,
        "player_location": player_loc,
        "player_description": player_desc,
        "co_located": co_located,
    }
