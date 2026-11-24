"""地点里的实物：环境描写写到的道具要真的存在，而且能被改动

以前环境描写里出现的东西是一次性的文学修饰——写「椅背上搭着薄毯」，
下一轮谁提起它都查无此物，于是同一条毯子在旁白里被捡起来两次、又还在床上。
现在道具会随环境描写一起落库（按会话 + 地点），

- 环境描写重新生成时把现有道具喂回去，避免同一个地方凭空换一批东西；
- 对话提示词里带一份「此地的这些东西」，她提到杯子、毯子时是同一个杯子、同一条毯子；
- 旁白（character_writer）可以顺带改动它们的状态，改动会写回库里。
"""

MAX_PROPS = 12
MAX_NAME = 16
MAX_STATE = 40


def _clean(text, limit):
    return (text or "").strip()[:limit]


def remember(db, conv_id, location, items):
    """把环境描写给出的道具写进库（同名更新状态）"""
    if not location or not items:
        return []
    saved = []
    for item in items[:MAX_PROPS]:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("名字") or item.get("name"), MAX_NAME)
        state = _clean(item.get("状态") or item.get("state"), MAX_STATE)
        if not name:
            continue
        try:
            db.set_prop(conv_id, location, name, state)
            saved.append(name)
        except Exception:
            continue
    return saved


def items(db, conv_id, location, limit=MAX_PROPS):
    """库里此地的道具（原始 dict 列表）"""
    try:
        return db.get_props(conv_id, location, limit=limit)
    except Exception:
        return []


def block(db, conv_id, location, limit=MAX_PROPS):
    """给提示词用的一行：此地的这些东西"""
    if not location:
        return ""
    try:
        items = db.get_props(conv_id, location, limit=limit)
    except Exception:
        return ""
    if not items:
        return ""
    parts = []
    for it in items:
        state = (it.get("state") or "").strip()
        parts.append(f"{it['name']}（{state}）" if state else it["name"])
    return "【此地的这些东西】（可以拿、可以动，动完就变成新的状态）\n- " + "\n- ".join(parts)


def names(db, conv_id, location):
    try:
        return db.get_prop_names(conv_id, location)
    except Exception:
        return []
