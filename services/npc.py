"""
动态 NPC —— 煎饼摊老板、便利店店员这类只应付简单问答的角色

设计取舍：
- NPC 绑定在**地点**上：玩家走到那个地点时他们在场，走开就不在
- 只做简单问答：一次一两句话，用小模型（flash）现生成，不维护长期人设
- 动态增减：现场没有这个人、但玩家确实在跟第三人说话 → 让模型判断后补一个
- 上限 NPC_CAP，防止一路走一路刷出几百个路人

「玩家在跟谁说话」这件事**只交给模型判断**：
之前用正则猜"句首称呼"，`hey?` 会被当成在叫一个叫 hey 的人，
凭空冒出一个 NPC 回你一句"？"。现在没有任何正则参与判断，
唯一的机械匹配是"这句话里提到了现场某个人的名字"——那只是找回已有角色，
不会创建新角色。

和尤夏的关系：NPC 说的话也会进对话历史（channel='npc'），
尤夏能看到、也能接话，但她不替 NPC 说话。
"""
import json
import re
import time

import core.locations as locations
from config import (
    NPC_ENABLED, NPC_PROMPT, NPC_PHONE_PROMPT, NPC_CAP, NPC_LLM_DETECT,
    NPC_DETECT_PROMPT, CHARACTER_NAME,
)

# 只用来剥掉模型偶尔带出的 ``` 围栏 / 从文字里捞出 JSON（属于解析，不是判断）
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

# 常见身份后缀：用来把"算命先生"这类称呼拆出身份
ROLE_SUFFIXES = (
    "老板娘", "老板", "店主", "摊主", "店长", "经理", "掌柜", "店员", "店员",
    "服务员", "收银员", "师傅", "保安", "门卫", "司机", "快递员", "外卖员",
    "大爷", "阿姨", "大叔", "大妈", "小哥", "小姐", "先生", "女士", "医生",
    "护士", "警察", "老师", "同学", "摊贩", "小贩", "路人",
)

# 同一地点最多几个 NPC（连同全局上限一起用）
PER_LOCATION_CAP = 3

# 这些不是"第三人"：类型上就不可能是名字（代词/她自己）。
# 不做「英文口头禅黑名单」：名字只需要满足形状（至少一个汉字），
# hey/hi/ok 这些自然就被挡在外面，也不会漏掉别的口头禅。
RESERVED_NAMES = {
    "你", "您", "我", "她", "他", "它", "你们", "我们", "大家", "对方", "自己",
    "尤夏", "青梅", "主人", "路人", "路人甲", "路人乙",
}

# 「家里不站人」：私人空间不会有陌生人在场搭话。
# 是不是私人空间由 locations.private 这个数据字段决定（默认地点建表时标好，
# 新地点默认私人），不再按名字里有没有「房间/客厅」去猜。
def is_private(db, conv_id, location):
    """这个地点是不是私人空间（只有你们两个人，不会有第三人凭空出现）"""
    return locations.is_private(db, conv_id, location)

# 只用于清理历史数据：把以前被正则误当成称呼建出来的口头禅 NPC 退场。
# 它不参与任何判断，新 NPC 的名字完全由模型给。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_bogus_name(name):
    """不是"第三人"的名字：代词、她自己、或者根本不像名字（没有汉字）"""
    n = (name or "").strip()
    if not n:
        return False
    if n in RESERVED_NAMES:
        return True
    return not _CJK_RE.search(n)


def _role_of(name):
    """把称呼拆成身份：煎饼摊老板 → 老板；算命先生 → 算命先生（"先生"太泛）"""
    for suffix in ROLE_SUFFIXES:
        if name == suffix:
            return suffix
        if name.endswith(suffix):
            return name if suffix in _GENERIC_ROLE else suffix
    return name


# 这些后缀太泛，单独拿出来当身份反而丢信息
_GENERIC_ROLE = {"先生", "女士", "小姐", "同学", "师傅", "大爷", "阿姨",
                 "大叔", "大妈", "小哥", "路人"}


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
    """这句话是不是在跟现场的某个 NPC 说（**不创建任何人**）

    只做两件机械的事：找回"句子里提到名字/身份"的现场 NPC、
    以及"上一轮就是他在说话"的延续。都找不到就返回 None，
    要不要新增角色交给 detect_addressed 的模型判断。
    """
    if not NPC_ENABLED or not message:
        return None
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        return None
    # 先把误建的 NPC 退场（口头禅、代词、叫她的昵称）：不管当时在哪儿建出来的
    kept = []
    for npc in present:
        if _is_bogus_name(npc.get("name")):
            try:
                db.set_npc_status(npc["id"], "gone")
            except Exception:
                pass
            continue
        kept.append(npc)
    present = kept
    if is_private(db, conv_id, location):
        # 私人空间里只认「剧情明确点名」的人（妈妈、室友），不认路边刷出来的路人
        present = [n for n in present if n.get("declared")]
        if not present:
            return None
    # 句子里提到了现场某个人的名字 / 身份 → 就是在跟他说
    for npc in present:
        if _hit(message, npc.get("name")):
            return npc
    for npc in present:
        if _hit(message, npc.get("role")):
            return npc
    # 其余情况（包括"接着上一位 NPC 说话"）都不在这里猜，交给模型判断。
    # 以前有个"上一轮是 NPC 就继续找他"的机械规则，结果只要喊过一次老板，
    # 之后每一句都被老板接走——105 轮试玩里有 71 轮变成了和摊主的对话。
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


def _prune(db, conv_id, location):
    """名册是有上限的，不是固定清单：挤满时先请最久没说话的那位退场

    这样无论玩家喊出多少新角色，表也不会无限膨胀。
    """
    try:
        present = db.get_npcs(conv_id, location=location)
        if len(present) >= PER_LOCATION_CAP:
            victim = min(present, key=lambda n: (n.get("last_spoke_at") or 0, n["id"]))
            db.set_npc_status(victim["id"], "gone")
        active = db.get_npcs(conv_id)
        if len(active) >= NPC_CAP:
            victim = min(active, key=lambda n: (n.get("last_spoke_at") or 0, n["id"]))
            db.set_npc_status(victim["id"], "gone")
    except Exception:
        pass


def _create(db, conv_id, location, name):
    """按称呼建一个 NPC；同名/同身份已存在就直接复用"""
    name = (name or "").strip()[:12]
    # 没有称呼、或者称呼根本不是人（代词、口头禅、路人）→ 不建，
    # 也不再按地点编一个出来（「路人」就是这么冒出来的）
    if not name or _is_bogus_name(name):
        return None
    role = _role_of(name)
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        present = []
    for npc in present:
        if npc.get("name") == name or npc.get("role") == role:
            return npc

    # 先腾位置（数量超限时最久没说话的退场），再生成
    _prune(db, conv_id, location)
    if db.count_npcs(conv_id) > NPC_CAP:
        return None
    persona = f"{location}的{role}，说话随意、简短，只管自己眼前的事。"
    npc_id = db.add_npc(conv_id, name, role, location, persona)
    return db.get_npc(npc_id)


def spawn_named(db, conv_id, location, name):
    """按判别结果给出的称呼生成（称呼可能写在句子中间）

    没有称呼就不编人：以前这里会兜底成「路人」，
    于是只要模型误判一次，房间里就多一个叫「路人」的人站着不走。
    """
    if not NPC_ENABLED or is_private(db, conv_id, location):
        return None
    return _create(db, conv_id, location, name)


def ensure_person(db, conv_id, name, role="", location="", phone=False, persona=""):
    """登记一个**明确被提到**的人：在场的人 / 电话联系人

    和 spawn_named 的区别：这条路径是模型在输出里点名声明的人物（「妈妈」
    「煎饼摊老板」），不是在猜玩家在跟谁说话，所以私人空间也照收——
    她家里来了人，是剧情说清楚的，不是路边随便冒出来的路人。
    同名的人只登记一次，之后拿到的都是同一个人。
    """
    if not NPC_ENABLED:
        return None
    name = (name or "").strip()[:12]
    if not name or _is_bogus_name(name):
        return None
    role = (role or "").strip()[:20] or _role_of(name)
    try:
        existing = db.find_npc(conv_id, name)
    except Exception:
        existing = None
    if existing:
        if not existing.get("declared"):
            try:
                db.set_npc_declared(existing["id"])
                existing["declared"] = 1
            except Exception:
                pass
        if phone and not existing.get("phone"):
            db.set_npc_phone(existing["id"], True)
            existing["phone"] = 1
        if location and not phone and existing.get("location") != location:
            db.set_npc_location(existing["id"], location)
            existing["location"] = location
        if persona and not existing.get("persona"):
            existing["persona"] = persona
        return existing

    try:
        if db.count_npcs(conv_id) >= NPC_CAP:
            return None
        npc_id = db.add_npc(conv_id, name, role, "" if phone else location,
                            persona or f"{role}，说话随意、简短，只管自己眼前的事。",
                            phone=1 if phone else 0, declared=1)
        return db.get_npc(npc_id)
    except Exception as e:
        print(f"[NPC] 登记失败: {e}")
        return None


def phone_contacts(db, conv_id):
    try:
        return db.get_phone_contacts(conv_id)
    except Exception:
        return []


def contacts_block(db, conv_id):
    """给提示词用：手机里能联系到谁"""
    people = phone_contacts(db, conv_id)
    if not people:
        return ""
    names = "、".join(f"{p['name']}（{p['role']}）" if p.get("role") and p["role"] != p["name"]
                     else p["name"] for p in people)
    return f"【手机里的联系人】{names}"


def resolve_phone_addressed(db, conv_id, message):
    """玩家这句话是不是在手机里对某个联系人说的（只做名字/身份的找回）"""
    if not NPC_ENABLED or not message:
        return None
    for person in phone_contacts(db, conv_id):
        if _hit(message, person.get("name")):
            return person
    for person in phone_contacts(db, conv_id):
        role = person.get("role") or ""
        if len(role) >= 2 and _hit(message, role):
            return person
    return None


def phone_reply(db, conv_id, npc, user_message, deepseek, api_key=None,
                story_minutes=0, listener=""):
    """让电话联系人用手机回一条消息

    消息写进 phone 通道并带上 speaker：前端把它渲染成那个人的气泡，
    尤夏也能在历史里看到「妈妈：……」，不会以为是自己说的。
    """
    if not NPC_ENABLED or not npc:
        return ""
    name = npc.get("name") or "对方"
    system = (
        NPC_PHONE_PROMPT
        .replace("{name}", name)
        .replace("{role}", npc.get("role") or "")
        .replace("{persona}", npc.get("persona") or "")
        .replace("{listener}", listener or CHARACTER_NAME)
    )
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": f"消息：{user_message}"}],
            system_prompt=system,
            temperature=0.8,
            api_key_override=api_key,
        )
    except Exception as e:
        print(f"[NPC] 手机回复失败: {e}")
        return ""
    if not raw or raw.startswith("[生成失败"):
        return ""
    text = _clean(raw, limit=60)
    if len(text) < 1:
        return ""
    db.add_message(conv_id, "assistant", text, channel="phone",
                   speaker=name, story_minutes=int(story_minutes or 0))
    db.touch_npc(npc["id"], time.time())
    return text


def reply(db, conv_id, npc, user_message, deepseek, api_key=None):
    """让 NPC 回一两句话；失败返回空串（不打断主流程）"""
    if not NPC_ENABLED or not npc:
        return ""
    if is_private(db, conv_id, npc.get("location")) and not npc.get("declared"):
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


# 判别时给模型看多少条最近的对话。以前只给 4 条：她上一句怎么应你的都看不见，
# 于是「小可爱」被当成现场真有个人。现在给足一整段，昵称、内梗、刚才点到的人都看得出来。
DETECT_CONTEXT_ROWS = 14


def _detect_recent(db, conv_id, limit=None):
    """把最近的对话排成判别模型能读的样子（含她的动作与第三人）"""
    try:
        rows = [m for m in db.get_messages(conv_id)
                if (m.get("channel") or "chat") in ("chat", "phone", "npc", "action")
                ][-(limit or DETECT_CONTEXT_ROWS):]
    except Exception:
        return "（还没有对话）"
    lines = []
    for m in rows:
        channel = m.get("channel") or "chat"
        if channel == "npc":
            try:
                data = json.loads(m["content"])
                lines.append(f"{data.get('name', '路人')}（第三人）：{data.get('text', '')}")
            except Exception:
                continue
        elif channel == "action":
            lines.append(f"（尤夏的动作）{m['content']}")
        else:
            speaker = "青梅" if m["role"] == "user" else "尤夏"
            who = (m.get("speaker") or "").strip()
            if who:
                speaker = who      # 手机里第三人发的话，别记成她说的
            lines.append(f"{speaker}（{'手机' if channel == 'phone' else '当面'}）：{m['content']}")
    return "\n".join(lines) or "（还没有对话）"


def detect_addressed(db, conv_id, location, message, deepseek, api_key=None):
    """让模型判断"这句话是不是在跟第三人说话、叫的是谁、依据是什么"。

    这是**唯一**的判断入口，没有任何词表：不猜「哪些称呼一定是在叫她」，
    而是把够长的上下文和现场情况一起给模型，并要求它**引一句依据**。
    引不出依据就当没这回事——凭空造出一个 NPC 比漏判更伤。
    返回 {"addressed": bool, "who": str, "why": str} 或 None。
    """
    if not NPC_LLM_DETECT or not message:
        return None
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        present = []
    names = "、".join(n.get("name") or "" for n in present) or "（现场暂时没有第三人）"
    try:
        player_loc = db.get_player_location(conv_id)
    except Exception:
        player_loc = ""
    co_located = bool(location) and location == player_loc
    place = (f"{location}（你和青梅{'在一起' if co_located else '不在一起'}）"
             if location else "（不明）")

    recent = _detect_recent(db, conv_id)

    system = (NPC_DETECT_PROMPT
              .replace("{present}", names)
              .replace("{place}", place)
              .replace("{recent}", recent))
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": message}],
            system_prompt=system,
            temperature=0.0,
            api_key_override=api_key,
            max_tokens=120,
            thinking=False,
        )
    except Exception as e:
        print(f"[NPC] 判别失败: {e}")
        return None
    if not raw or raw.startswith("[生成失败"):
        return None
    text = raw.strip()
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
    addressed = bool(data.get("addressed"))
    who = str(data.get("who") or "").strip()[:12]
    why = str(data.get("why") or "").strip()[:60]
    return {"addressed": addressed, "who": who, "why": why}


def resolve_addressed(db, conv_id, location, message, deepseek=None, api_key=None):
    """玩家这句话是对谁说的：「先找回现场的」→「再让模型判断要不要新增」。

    判断完全由模型做，这里只负责把结论落到名册上：
    - 私人空间里只有你们两个人 → 一律不计（尤夏房间里不该站着路人）
    - 句子里提到现场某人的名字 → 直接找他（不花 token）
    - 否则问一次模型；模型说"在跟第三人说话"**并且给出了称呼**才建人
    """
    if not NPC_ENABLED or not message:
        return None
    # 先走一遍「找回现场的」，它顺手把误建的 NPC 退场（昵称、口头禅、代词）
    found = find_addressed(db, conv_id, location, message)
    if found:
        return found
    if deepseek is None or is_private(db, conv_id, location):
        return None
    decision = detect_addressed(db, conv_id, location, message, deepseek, api_key=api_key)
    if not decision:
        return None
    who = (decision.get("who") or "").strip()

    if not decision.get("addressed"):
        return None

    # 说"在对第三人说话"却引不出依据：当作没听见，不凭空造人
    if len((decision.get("why") or "").strip()) < 2:
        return None
    if not who:
        # 有依据但没给称呼：同样不替他编一个（「路人」就是这么来的）
        return None
    existing = db.find_npc(conv_id, who, location)
    if existing:
        return existing
    if _is_bogus_name(who):
        return None
    # 这句话里已经点了她的名字，又没对上现场的谁 → 当成在跟她说话。
    # 判别模型看过上下文，但「嘿嘿尤夏小美女」这种还是会被它当成在叫别人。
    if "尤夏" in message:
        return None
    return spawn_named(db, conv_id, location, who)


def present_block(db, conv_id, location):
    """给尤夏的世界块用：现场还有谁"""
    if not NPC_ENABLED:
        return ""
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        return ""
    # 私人空间里只列剧情明确点名的人（妈妈、室友），不列路边刷出来的路人
    if is_private(db, conv_id, location):
        present = [n for n in present if n.get("declared")]
    if not present:
        return ""
    names = "、".join(f"{n['name']}（{n['role']}）" if n.get("role") and n["role"] != n["name"]
                     else n["name"] for n in present)
    return f"【现场还有】{names}"
