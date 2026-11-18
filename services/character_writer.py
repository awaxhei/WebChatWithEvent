"""
尤夏的神态描写（v5）

她自己只输出 对白 / 消息 / 表情包，不写动作。
神态与细微动作由这里根据上下文单独生成一句话，作为旁白式描写挂在她的发言旁。
"""
import re

from config import CHARACTER_DESC_ENABLED, CHARACTER_DESC_PROMPT

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _clean(text):
    if not text:
        return ""
    text = _FENCE_RE.sub("", text.strip()).strip()
    text = text.replace("\n", " ").strip()
    # 去掉模型可能加的包裹符号与标签
    for prefix in ("描写：", "神态：", "尤夏：", "【描写】"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    text = text.strip('"“”「」『』 ')
    return text[:120]


def describe(db, conv_id, deepseek, now_minutes, context_brief, api_key=None):
    """生成一句尤夏此刻的神态描写；失败返回空串"""
    if not CHARACTER_DESC_ENABLED:
        return ""

    recent = db.get_messages(conv_id)[-4:]
    recent_text = "\n".join(
        f"{'青梅' if m['role'] == 'user' else '尤夏'}"
        f"（{'手机' if m.get('channel') == 'phone' else '当面'}）：{m['content']}"
        for m in recent
    )

    user_msg = (
        f"{context_brief}\n\n"
        f"最近的对话：\n{recent_text or '（还没有对话）'}\n\n"
        "请写一句尤夏此刻的神态描写。"
    )

    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": user_msg}],
            system_prompt=CHARACTER_DESC_PROMPT,
            temperature=0.9,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[神态描写] 调用失败: {e}")
        return ""

    if not raw or raw.startswith("[生成失败"):
        return ""

    text = _clean(raw)
    if len(text) < 6:
        return ""
    # 落库到独立通道，重进会话时也能看到
    db.add_message(conv_id, "assistant", text, channel="state",
                   scene=db.get_current_location(conv_id))
    return text
