"""
尤夏的动作描写（v12）

她自己只输出 对白 / 消息 / 表情包，动作由这里根据上下文单独生成，作为旁白挂在发言之间。

只写尤夏本人：以前这里写「两个人」，结果旁白替玩家翻身、关窗、捡毯子，
玩家的话就不作数了。现在玩家的动作只在一种情况下才写——**玩家这一句本身
就是动作**（「（钻进被窝）」这种），而且只写这个动作带出的结果。
"""
import json
import re

from config import CHARACTER_DESC_ENABLED, CHARACTER_DESC_PROMPT
import core.pronoun as pronoun

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_STRIP_PREFIXES = (
    "描写：", "动作：", "神态：", "旁白：", "两人：", "尤夏和青梅：",
    "尤夏：", "【描写】", "【动作】",
)

# 描写上限：提示词要求 20~45 字，模型偶尔写超，硬切会把句子劈成两半
MAX_DESC_CHARS = 70
# 玩家的动作写在括号里（「（钻进被窝）」「(把杯子推过去)」）
_ACTION_RE = re.compile(r"[（(]([^）)]{1,40})[）)]")
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


def _parse_output(raw):
    """输出可以是 JSON（旁白 + 道具状态），也可以是纯文本（模型不听话时）"""
    text = (raw or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            desc = str(data.get("旁白") or data.get("描写") or "").strip()
            items = [it for it in (data.get("道具") or []) if isinstance(it, dict)][:6]
            return _clean(desc), items
    return _clean(text), []


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


def player_action(db, conv_id):
    """玩家这一句是不是一个动作（括号里写的）；是就返回动作内容"""
    try:
        rows = [m for m in db.get_messages(conv_id) if m["role"] == "user"]
    except Exception:
        return ""
    if not rows:
        return ""
    hit = _ACTION_RE.search(rows[-1].get("content") or "")
    return (hit.group(1).strip()[:40] if hit else "")


def describe(db, conv_id, deepseek, now_minutes, context_brief, api_key=None):
    """生成一句"她此刻的动作"；失败返回空串（顺带返回要更新的道具状态）"""
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

    # 玩家这一句是动作吗？只有这种时候才允许旁白写她
    act = ""
    try:
        act = player_action(db, conv_id)
    except Exception:
        act = ""
    player_part = (
        f"   玩家这一句本身是一个动作：「{act}」。可以顺着写这个动作带出的结果（只一件），"
        "但仍然不要替她加别的动作、不要写她的心理。\n"
        if act else
        "   玩家这一句是在说话/没有动作，那就只写尤夏，一个字都不要写青梅。\n"
    )

    # 此地的实物：让她动的是真的东西
    props_line = ""
    try:
        import core.props as props_mod
        props_line = props_mod.block(db, conv_id, yuxia_loc)
    except Exception:
        props_line = ""

    user_msg = (
        f"{context_brief}\n"
        f"{where}{time_line}"
        f"{world}"
        + (f"\n\n{props_line}" if props_line else "")
        + f"\n\n最近的对话：\n{recent_text or '（还没有对话）'}\n\n"
        "请写一句尤夏此刻的动作描写。"
    )

    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": user_msg}],
            system_prompt=CHARACTER_DESC_PROMPT.replace("{player_action}", player_part),
            temperature=0.9,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[动作描写] 调用失败: {e}")
        return ""

    if not raw or raw.startswith("[生成失败"):
        return ""

    text, prop_items = _parse_output(raw)
    if len(text) < 4:
        return ""
    # 她动到的东西改状态：道具是真的，改了要记住
    if prop_items:
        try:
            import core.props as props_mod
            props_mod.remember(db, conv_id, yuxia_loc, prop_items)
        except Exception as e:
            print(f"[动作描写] 道具更新失败: {e}")
    # 落库到独立通道，重进会话时也能看到
    db.add_message(conv_id, "assistant", text, channel="state", scene=yuxia_loc)
    return text
