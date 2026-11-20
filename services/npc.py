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

from config import NPC_ENABLED, NPC_PROMPT, NPC_CAP, NPC_LLM_DETECT, NPC_DETECT_PROMPT

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

# 判定"玩家在喊人"：句首一个称呼 + 标点/空白，或者 @名字
# 第一个捕获组是 @，第二个是名字本身
_VOCATIVE_RE = re.compile(
    r"^[\s（(「【\"'“]*(@?)([\u4e00-\u9fa5A-Za-z0-9]{2,8})[，,：:！!？?、\s]")
# 这些词虽然出现在句首，但不是称呼
_VOCATIVE_STOP = {
    "你", "您", "尤夏", "我", "喂", "那个", "这个", "我说", "然后", "所以",
    "不过", "而且", "就是", "今天", "明天", "昨天", "现在", "刚才", "老板们",
    "嗯", "啊", "诶", "哎", "呃", "唉", "嘿", "哈", "嗷", "哦", "喔", "呐",
    "草", "靠", "啧", "那个", "等等", "等下",
}
# 纯英文的口头禅：不带 @ 时一律不当称呼
# （"hey?" 曾经被当成"你在叫一个叫 hey 的人"，于是凭空冒出一个 NPC）
_LATIN_ONLY_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")
_EN_INTERJECTIONS = {
    "hey", "hi", "hello", "yo", "ok", "okay", "yes", "no", "yeah", "nope",
    "thanks", "thank", "please", "pls", "lol", "hmm", "huh", "um", "uh",
    "wait", "well", "sorry", "good", "nice", "omg", "wow", "gg", "nb",
    "emmm", "emd", "emm", "abc", "lmao", "rofl", "sup", "bro",
}
_HAN_RE = re.compile(r"[\u4e00-\u9fa5]")
# 称呼里不该出现的字（动词/语气词/指示词）——用来把"给我来个煎饼"挡在外面
_VOCATIVE_BLOCK = set("给我你您他她它们吗呢吧了啊呀哦啦要帮买卖吃喝看做走拿这那怎么谁是")
# 常见身份后缀：用来把"算命先生"这类称呼拆出身份
ROLE_SUFFIXES = (
    "老板娘", "老板", "店主", "摊主", "店长", "经理", "掌柜", "店员", "店员",
    "服务员", "收银员", "师傅", "保安", "门卫", "司机", "快递员", "外卖员",
    "大爷", "阿姨", "大叔", "大妈", "小哥", "小姐", "先生", "女士", "医生",
    "护士", "警察", "老师", "同学", "摊贩", "小贩", "路人",
)


def _is_bogus_name(name):
    """是不是被误当成称呼的英文口头禅（hey / hi / ok …）"""
    n = (name or "").strip()
    return bool(_LATIN_ONLY_RE.match(n)) and n.lower() in _EN_INTERJECTIONS


def extract_vocative(message):
    """从玩家这句话里抽出他在喊谁。

    不限于固定词表：句首「称呼 + 标点」的写法（"算命先生，帮我看看"）
    或者 @名字 都能识别，这样表里没有的名字也能当场出现。

    两条硬规则（都是被实战坑出来的）：
    - 不带 @ 的纯英文一律不算称呼：hey / hi / ok / wait 是口头禅，
      以前 "hey?" 会凭空生成一个叫 hey 的 NPC
    - 语气词（嗯/啊/诶…）即使在词表外也不当称呼
    """
    text = (message or "").strip()
    if not text:
        return None
    m = _VOCATIVE_RE.match(text)
    if not m:
        return None

    explicit = m.group(1) == "@"
    cand = m.group(2)
    if cand in _VOCATIVE_STOP:
        return None
    if set(cand) & _VOCATIVE_BLOCK:
        return None
    if not explicit:
        # 没打 @ 又没有一个汉字 → 口头禅，不是称呼
        if not _HAN_RE.search(cand):
            return None
        if cand.lower() in _EN_INTERJECTIONS:
            return None
    elif _is_bogus_name(cand):
        return None
    return cand


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
    """玩家这句话是不是在跟现场的某个 NPC 说"""
    if not NPC_ENABLED or not message:
        return None
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        return None
    # 0) 先把以前误建的口头禅 NPC 退场（"hey?" 那次留下的）
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
    # 1) 点名找（名字优先，其次身份）
    for npc in present:
        if _hit(message, npc.get("name")):
            return npc
    for npc in present:
        if _hit(message, npc.get("role")):
            return npc
    # 2) 喊了一个现场没有的新称呼（"城管，……"）
    #    这时绝不能走"接着上一轮"的分支，否则新角色永远出不来
    vocative = extract_vocative(message)
    if vocative:
        for npc in present:
            if npc.get("name") == vocative or npc.get("role") == vocative:
                return npc
        return None
    # 3) 没点名，但上一轮就是 NPC 说的 → 当作在跟他继续聊
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
    if not name:
        return None
    # 口头禅不是人名：hey / hi / ok 这类一律不建
    if _is_bogus_name(name):
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


def spawn_placeholder(db, conv_id, location, message):
    """现场没有这个人，但玩家喊了他的称呼 → 当场生成一个

    称呼不限于固定词表：句首「算命先生，……」这种写法也能认出来。
    """
    if not NPC_ENABLED:
        return None
    name = extract_vocative(message)
    if not name:
        return None
    return _create(db, conv_id, location, name)


def _default_name(location):
    """判别结果只说了"在对第三人说话"却没给称呼时，按地点猜一个"""
    loc = location or ""
    if any(k in loc for k in ("摊", "市场", "集市")):
        return "摊主"
    if any(k in loc for k in ("店", "铺", "馆", "吧", "超市", "商场")):
        return "店员"
    if any(k in loc for k in ("站", "场", "厅", "银行", "医院", "学校")):
        return "工作人员"
    return "路人"


def spawn_named(db, conv_id, location, name):
    """按判别结果给出的称呼生成（称呼可能写在句子中间）"""
    if not NPC_ENABLED:
        return None
    return _create(db, conv_id, location, name or _default_name(location))


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


def detect_addressed(db, conv_id, location, message, deepseek, api_key=None):
    """用一个小模型判断"这句话是不是在跟第三人说话"。

    只在固定规则识别不出称呼时用：玩家把称呼写在句子中间、或者干脆没写称呼，
    规则会漏掉，这里补一次判断。关掉思考、限制 120 token，尽量不拖慢首字。
    返回 {"addressed": bool, "who": str} 或 None。
    """
    if not NPC_LLM_DETECT or not message:
        return None
    try:
        present = db.get_npcs(conv_id, location=location)
    except Exception:
        present = []
    names = "、".join(n.get("name") or "" for n in present) or "（现场暂时没有第三人）"
    system = NPC_DETECT_PROMPT.replace("{present}", names)
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
    return {"addressed": addressed, "who": who}


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
