"""
身体接触的热度 —— 让「贴在一起」的这一段写得像真的

以前每轮的动作数被压得很死：一两下、短句、不要重复。聊天里正常，
但两个人正贴在一起的时候，她的反应反而比平时还少，读起来像在旁边看着。

这里只做一件事：读最近几条消息，判断此刻的身体接触到了哪一档。
- 0 没有接触
- 1 有接触（抱着、牵着、碰到耳朵）
- 2 亲密接触（亲、吻、贴着、喘息、衣服/床这些明显更近的词）

对话与旁白据此放宽条数、换一种写法；随机事件的门控仍然只认第 2 档。
"""
import json

# 明显的亲密信号：命中一个就算第 2 档
STRONG = (
    "亲吻", "吻", "亲了", "亲了亲", "做爱", "脱衣服", "脱掉", "脱光", "洗澡", "浴室",
    "胸", "大腿", "腰", "喘", "呻吟", "裸", "湿了", "舔", "床上", "被窝里",
    "压着", "按住", "体内", "进入", "颤", "酥", "敏感", "绷紧", "腿软", "发烫",
)
# 身体接触的动词：命中一个算第 1 档，两个以上算第 2 档
CONTACT = (
    "抱", "亲", "吻", "摸", "贴", "搂", "蹭", "牵", "靠", "拉", "按", "压",
    "环住", "圈住", "抓住", "碰", "触", "拂", "抱紧", "抱抱", "挨着", "挤在",
)

CONTEXT_ROWS = 10

_FIELDS = ("对白", "动作", "消息")


def _read(row):
    content = row.get("content") or ""
    if (row.get("channel") or "chat") == "chat" and content.lstrip().startswith("{"):
        try:
            obj = json.loads(content)
        except Exception:
            return content
        if isinstance(obj, dict):
            return " ".join(str(obj.get(k) or "") for k in _FIELDS).strip()
    return content


def recent_text(db, conv_id, limit=None):
    limit = limit or CONTEXT_ROWS
    try:
        rows = [m for m in db.get_messages(conv_id)
                if (m.get("channel") or "chat") in ("chat", "phone", "action", "state")
                ][-limit:]
    except Exception:
        return ""
    return "\n".join(_read(m) for m in rows if _read(m))


def level(db, conv_id, limit=None):
    """这一档接触有多近：0 / 1 / 2

    以她自己声明的 `接触` 为准（存在 conversations.contact_level）；文字扫描只是
    在还没有声明时（这一轮刚接触）给她一个起步的提示。门控用声明值，不用扫描。
    """
    stored = 0
    try:
        stored, _at = db.get_contact_level(conv_id)
    except Exception:
        stored = 0
    scanned = _scan(db, conv_id, limit)
    return max(stored, scanned)


def _scan(db, conv_id, limit=None):
    text = recent_text(db, conv_id, limit)
    if not text:
        return 0
    if any(w in text for w in STRONG):
        return 2
    hits = [w for w in CONTACT if w in text]
    if len(hits) >= 2:
        return 2
    return 1 if hits else 0


# 她声明的接触程度 → 数值。只认这三个词，避免模型自由发挥。
CONTACT_MARKERS = {
    "无": 0, "没有": 0, "没碰到": 0, "分开": 0,
    "碰到": 1, "轻碰": 1, "挨着": 1, "靠着": 1,
    "亲密": 2, "很亲密": 2, "贴着": 2, "缠着": 2,
}


def parse_marker(value):
    """把回复里的 `接触` 解析成 0/1/2；不认识返回 None（不改之前的状态）"""
    text = str(value or "").strip()
    if not text:
        return None
    for key, level_value in CONTACT_MARKERS.items():
        if key in text:
            return level_value
    return None


def record(db, conv_id, value, story_minutes=0):
    """把这一轮声明的 `接触` 写进会话，供随机事件门控读取"""
    parsed = parse_marker(value)
    if parsed is None:
        return None
    try:
        db.set_contact_level(conv_id, parsed, story_minutes)
    except Exception:
        return None
    return parsed


def is_intimate(db, conv_id, limit=None):
    return level(db, conv_id, limit) >= 2


def chat_block(heat, co_located=True):
    """给对话提示词的一段：这一轮该怎么写她的反应"""
    if heat <= 0:
        return ""
    if heat == 1:
        return (
            "【此刻的身体接触】你们靠得很近，有碰到对方。\n"
            "- 她的反应可以多一条：被碰到的那一下（缩、僵、耳朵转过去、手上的劲松了），"
            "写进 `动作`，一两句就够\n"
            "- 对白还是短句，不必因为靠得近就写成一大段"
        )
    if not co_located:
        return ""
    return (
        "【此刻的身体接触】两个人正在亲密接触，还没停。这一轮写得比平时多一点：\n"
        "- 对白可以三五条，短、碎、带停顿和气息（「……别」「等一下」「你手好烫」这种），"
        "不要写成完整的说明句，也不要每句都成段\n"
        "- `动作` 可以给两到三条，每条只写一个能看见的身体反应：呼吸乱了、手指抓到哪里、"
        "肩背绷住又软下去、耳朵压平、尾巴缠上来、声音断掉、往怀里缩或者躲一下\n"
        "- 反应要像她本人：嘴上还在硬，身体先诚实；被碰到敏感的地方会先躲再回来\n"
        "- 她不是被动承受：也会主动凑回去、勾住衣角、把人拉低一点，做完还要装作若无其事\n"
        "- 这一轮允许输出到六条 JSON（平时是一到四条）；仍然一句一条、一条最多 20 字\n"
        "- 不替青梅做动作，不写青梅的心理；也不写环境、不写总结句"
    )


def desc_ask(heat):
    """给旁白（character_writer）的收尾要求"""
    if heat >= 2:
        return (
            "此刻两个人正在身体接触。请写尤夏此刻的身体反应：两到三句连续的细节"
            "（呼吸、手指、肩背、耳朵和尾巴、声音、贴或躲），60 到 90 字。"
            "不写环境、不写心理、不替青梅动作；如果她这一轮已经用「动作」写过，就写另一处反应。"
        )
    if heat == 1:
        return ("此刻两人有身体接触。写她被碰到时的那一下反应，一到两句，30 到 60 字，"
                "不替青梅动作。")
    return "请写一句尤夏此刻的动作描写。"
