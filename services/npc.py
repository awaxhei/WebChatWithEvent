"""
动态 NPC —— 煎饼摊老板、便利店店员这类只应付简单问答的角色

设计取舍：
- NPC 绑定在**地点**上：玩家走到那个地点时他们在场，走开就不在
- 只做简单问答：一次一两句话，用小模型（flash）现生成，不维护长期人设
- 动态增减：玩家喊「老板」而现场没有 NPC 时自动补一个；
  也可以显式创建 / 让某个 NPC 退场（active → gone）
- 上限 NPC_CAP，防止一路走一路刷出几百个路人

和尤夏的关系：NPC 说的话也会进对话历史（channel='npc'），
尤夏能看到、也能接话，但她不替 NPC 说话。
"""
import json
import re
import time

from config import NPC_ENABLED, NPC_PROMPT, NPC_CAP

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

# 玩家用这些称呼喊人时，说明现场应该有个 NPC
ROLE_HINTS = {
    "老板": "老板",
    "老板娘": "老板娘",
    "店主": "店主",
    "摊主": "摊主",
    "店员": "店员",
    "服务员": "服务员",
    "收银": "收银员",
    "前台": "前台",
    "师傅": "师傅",
    "保安": "保安",
    "门卫": "门卫",
    "司机": "司机",
    "快递": "快递员",
    "外卖": "外卖员",
    "大爷": "大爷",
    "阿姨": "阿姨",
}

# 同一地点最多几个 NPC（连同全局上限一起用）
PER_LOCATION_CAP = 3


def _clean(text, limit=80):
    if not text:
        return ""
    text = _FENCE_RE.sub("", str(text).strip()).strip()
    text = text.replace("\n", " ").strip().strip('"“”「」『』 ')
    for prefix in ("回答：", "台词：", "老板：", "【回答】"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text[:limit]


def _hit(haystack, needle):
    return bool(needle) and needle in (haystack or "")


def find_addressed(db, conv_id, location, message):
    """玩家这句话是不是在跟现场的某个 NPC 说"""
    if not NPC_ENABLED or not message:
        return None
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        return None
    # 1) 点名找（名字优先，其次身份）
    for npc in present:
        if _hit(message, npc.get("name")):
            return npc
    for npc in present:
        if _hit(message, npc.get("role")):
            return npc
    # 2) 没点名，但上一轮就是 NPC 说的 → 当作在跟他继续聊
    if "尤夏" in (message or ""):
        return None
    last_npc = _recent_npc(db, conv_id)
    if not last_npc:
        return None
    for npc in present:
        if npc.get("name") == last_npc.get("name"):
            return npc
    return None


def _recent_npc(db, conv_id, window=3):
    """往回看几条：如果最近一轮是 NPC 在说话，返回它的 {name, text}

    尤夏紧跟着接话时，她的回复会排在 NPC 后面，所以只看"最后一条"会漏；
    这里往回扫，碰到玩家自己的消息就停（说明已经换话题 / 换人说话了）。
    """
    try:
        rows = [m for m in db.get_messages(conv_id)
                if (m.get("channel") or "chat") in ("chat", "phone", "npc")][-window:]
    except Exception:
        return None
    for m in reversed(rows):
        channel = m.get("channel") or "chat"
        if channel == "npc":
            try:
                data = json.loads(m["content"])
                if isinstance(data, dict) and data.get("text"):
                    return data
            except Exception:
                return None
        if m["role"] == "user":
            return None
    return None


def _role_in_message(message):
    for word, role in ROLE_HINTS.items():
        if word in (message or ""):
            return word, role
    return None, None


def spawn_placeholder(db, conv_id, location, message):
    """现场没有这个人，但玩家喊了职位 → 动态补一个"""
    if not NPC_ENABLED:
        return None
    word, role = _role_in_message(message)
    if not role:
        return None
    if db.count_npcs(conv_id) >= NPC_CAP:
        return None
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        present = []
    if len(present) >= PER_LOCATION_CAP:
        return None
    for npc in present:
        if npc.get("role") == role or npc.get("name") == word:
            return npc
    persona = f"{location}的{role}，只管自己这一摊，说话随意、简短、不寒暄。"
    npc_id = db.add_npc(conv_id, word, role, location, persona)
    return db.get_npc(npc_id)


def reply(db, conv_id, npc, user_message, deepseek, api_key=None):
    """让 NPC 回一两句话；失败返回空串（不打断主流程）"""
    if not NPC_ENABLED or not npc:
        return ""
    system = (
        NPC_PROMPT
        .replace("{name}", npc.get("name") or "店员")
        .replace("{role}", npc.get("role") or "")
        .replace("{location}", npc.get("location") or "")
        .replace("{persona}", npc.get("persona") or "")
    )
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": f"顾客说：{user_message}"}],
            system_prompt=system,
            temperature=0.8,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[NPC] 调用失败: {e}")
        return ""
    if not raw or raw.startswith("[生成失败"):
        return ""
    text = _clean(raw)
    if len(text) < 2:
        return ""
    db.add_message(conv_id, "assistant",
                   json.dumps({"name": npc.get("name") or "店员", "text": text},
                              ensure_ascii=False),
                   channel="npc", scene=npc.get("location") or "")
    db.touch_npc(npc["id"], time.time())
    return text


def present_block(db, conv_id, location):
    """给尤夏的世界块用：现场还有谁"""
    if not NPC_ENABLED:
        return ""
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        return ""
    if not present:
        return ""
    names = "、".join(f"{n['name']}（{n['role']}）" if n.get("role") and n["role"] != n["name"]
                     else n["name"] for n in present)
    return f"【现场还有】{names}"
