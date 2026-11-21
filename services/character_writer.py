"""
两人动作描写（v6）

她自己只输出 对白 / 消息 / 表情包，不写动作。
动作由这里根据上下文单独生成，作为旁白挂在发言之间——
现在写的是**两个人**：尤夏做了什么、青梅做了什么、两人之间怎么呼应，
而不是只盯着尤夏一个人的神态。
"""
import re

from config import CHARACTER_DESC_ENABLED, CHARACTER_DESC_PROMPT
import core.pronoun as pronoun

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_STRIP_PREFIXES = (
    "描写：", "动作：", "神态：", "旁白：", "两人：", "尤夏和青梅：",
    "尤夏：", "【描写】", "【动作】",
)

# 描写上限：提示词要求 60~130 字，模型偶尔写超，硬切会把句子劈成两半
MAX_DESC_CHARS = 200
# 截断时优先停在这些标点后面（句号在前）
_CLIP_MARKS = ("。", "！", "？", "；", "…", "，", "、")


def _clip(text, limit=MAX_DESC_CHARS):
    """超长时在最近的句读处截断，别留下半句话"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for mark in _CLIP_MARKS:
        cut = head.rfind(mark)
        if cut >= limit * 0.6:
            return head[:cut + 1]
    return head


def _clean(text):
    if not text:
        return ""
    text = _FENCE_RE.sub("", text.strip()).strip()
    text = text.replace("\n", " ").strip()
    # 去掉模型可能加的包裹符号与标签
    for prefix in _STRIP_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    text = text.strip('"“”「」『』 ')
    return pronoun.fix(_clip(text))


def describe(db, conv_id, deepseek, now_minutes, context_brief, api_key=None):
    """生成一两句"两人此刻的动作"；失败返回空串"""
    if not CHARACTER_DESC_ENABLED:
        return ""

    # 只看对话通道：上一次的 state 描写不再回灌，否则"他"这种错会一直自我强化
    recent = [m for m in db.get_messages(conv_id)
              if (m.get("channel") or "chat") in ("chat", "phone")][-6:]
    recent_text = "\n".join(
        f"{'青梅' if m['role'] == 'user' else '尤夏'}"
        f"（{'手机' if m.get('channel') == 'phone' else '当面'}）：{m['content']}"
        for m in recent
    )

    yuxia_loc = db.get_current_location(conv_id)
    player_loc = db.get_player_location(conv_id)
    co_located = yuxia_loc == player_loc
    where = (f"尤夏在{yuxia_loc}，青梅在{player_loc}。"
             + ("两人此刻在一起。" if co_located else "两人此刻不在一起（靠手机联系）。"))

    world = ""
    try:
        import core.clock as clock
        import services.world_context as world_context
        world = world_context.recent_events_block(
            db, conv_id, limit=3, now_minutes=now_minutes)
        if world:
            world = f"\n\n{world}"
        time_line = f"\n\n【此刻】{clock.format_time(now_minutes)}"
    except Exception:
        time_line = ""

    user_msg = (
        f"{context_brief}\n"
        f"{where}{time_line}"
        f"{world}\n\n"
        f"最近的对话：\n{recent_text or '（还没有对话）'}\n\n"
        "请写一两句两个人此刻的动作描写。"
    )

    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": user_msg}],
            system_prompt=CHARACTER_DESC_PROMPT,
            temperature=0.9,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[动作描写] 调用失败: {e}")
        return ""

    if not raw or raw.startswith("[生成失败"):
        return ""

    text = _clean(raw)
    if len(text) < 6:
        return ""
    # 落库到独立通道，重进会话时也能看到
    db.add_message(conv_id, "assistant", text, channel="state", scene=yuxia_loc)
    return text
