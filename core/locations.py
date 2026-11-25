"""
地点系统 —— 地点是真实存在的实体，而不是只活在上下文里的一句话

- 每个会话有一张自己的地点表（初始为 config.DEFAULT_LOCATIONS）
- AI 只能"选择已有地点"或"申请新建地点"，由服务端校验后落库
- 当前地点持久化在 conversations.current_location，跨轮次稳定
- 提供 prompt 片段，把可选地点注入给 AI B / AI A
"""
import re

from config import (
    DEFAULT_LOCATIONS, DEFAULT_PRIVATE_LOCATIONS, LOCATION_CAP,
    DEFAULT_YUXIA_LOCATION, DEFAULT_PLAYER_LOCATION,
)

MAX_LOCATIONS_PER_CONV = LOCATION_CAP
MAX_NAME_LEN = 20
MIN_NAME_LEN = 2

_CLEAN_RE = re.compile(r"^[\s【】\[\]「」\"'“”‘’·,，.。:：;；!！?？]+|[\s【】\[\]「」\"'“”‘’·,，.。:：;；!！?？]+$")


def normalize(candidate):
    """清洗 AI 给出的地点名，返回可入库的规范名或 None"""
    if not candidate:
        return None
    name = _CLEAN_RE.sub("", str(candidate).strip())
    name = re.sub(r"\s+", "", name)
    if not (MIN_NAME_LEN <= len(name) <= MAX_NAME_LEN):
        return None
    return name


def ensure_defaults(db, conv_id):
    """首次访问该会话时写入默认地点，把两人放进各自的房间"""
    names = db.get_location_names(conv_id)
    if not names:
        for name in DEFAULT_LOCATIONS:
            db.add_location(conv_id, name,
                            private=name in DEFAULT_PRIVATE_LOCATIONS)
        names = db.get_location_names(conv_id)
    if not db.get_current_location(conv_id):
        start = DEFAULT_YUXIA_LOCATION if DEFAULT_YUXIA_LOCATION in names else (
            names[0] if names else DEFAULT_LOCATIONS[0])
        db.set_current_location(conv_id, start)
    # 开局各在各的房间：一个是"还没照面"的冷水式开场，也顺便让手机通道先用起来
    if not db.get_player_location(conv_id):
        start = DEFAULT_PLAYER_LOCATION if DEFAULT_PLAYER_LOCATION in names else \
            db.get_current_location(conv_id)
        db.set_player_location(conv_id, start)
    return names


def get_current(db, conv_id):
    ensure_defaults(db, conv_id)
    return db.get_current_location(conv_id)


def list_names(db, conv_id):
    """会话已知地点列表"""
    return ensure_defaults(db, conv_id)


def resolve(db, conv_id, candidate, private=False):
    """把 AI 给出的地点名归一化到已有地点；不存在则新建。

    `private`：新地点是不是私人空间。默认公共；默认地点里的私人房间建表时已标好，
    剧情新建的地点由场景观察者声明（私人空间不让陌生人自动出现）。

    返回 (最终地点名, 是否新建)。
    """
    name = normalize(candidate)
    if not name:
        return None, False

    names = ensure_defaults(db, conv_id)

    # 1) 完全一致
    if name in names:
        return name, False

    # 2) 包含关系（"教室" ↔ "大学教室"）
    for existing in names:
        if name in existing or existing in name:
            return existing, False

    # 3) 申请新建（有数量上限，防止刷表）
    if len(names) >= MAX_LOCATIONS_PER_CONV:
        return names[0], False
    db.add_location(conv_id, name, private=private)
    return name, True


def set_private(db, conv_id, name, private):
    """显式声明一个地点是私人还是公共（剧情/玩家/测试都可以用）"""
    db.set_location_private(conv_id, name, private)
    return name


def is_private(db, conv_id, name):
    """地点是不是私人空间（读 locations.private，不再按名字里的词猜）"""
    return db.get_location_private(conv_id, name)


def switch(db, conv_id, candidate):
    """切换当前地点，返回最终地点名"""
    name, _created = resolve(db, conv_id, candidate)
    if name:
        db.set_current_location(conv_id, name)
    return name or get_current(db, conv_id)


def switch_both(db, conv_id, candidate):
    """两人一起过去：尤夏和青梅都挪到同一个地点。

    返回 (最终地点名, 是否新建)；地点名不合法时返回 (None, False)。
    """
    name, created = resolve(db, conv_id, candidate)
    if not name:
        return None, False
    db.set_current_location(conv_id, name)
    db.set_player_location(conv_id, name)
    return name, created


_MOVE_TITLES = {
    "player": "青梅去了{to}",
    "yuxia": "尤夏去了{to}",
    "both": "两人一起去了{to}",
}
_MOVE_DESCS = {
    "player": "青梅从{fr}走开了，现在在{to}。",
    "yuxia": "她从{fr}过去，现在在{to}。",
    "both": "两个人一起从{fr}走到了{to}。",
}


def log_move(db, conv_id, who, from_location, to_location, story_time="", created=False,
             tier=""):
    """把"换地方"记成一条事件。

    玩家在界面上点一下地点就走了，一个字都不会说。没有这条事件，
    她下一轮还以为人在客厅——或者不知道自己已经被挪到了别处。

    返回是否真的记了一条（没换地方就不记）。
    """
    if not to_location or to_location == from_location:
        return False
    who = who if who in _MOVE_TITLES else "both"
    title = _MOVE_TITLES[who].format(to=to_location) + ("（新场景）" if created else "")
    if from_location:
        desc = _MOVE_DESCS[who].format(fr=from_location, to=to_location)
    else:
        subject = {"player": "青梅", "yuxia": "她", "both": "两个人"}[who]
        desc = f"{subject}现在在{to_location}。"
    db.add_event(conv_id, "scene_change", title, desc, "active",
                 story_time=story_time or "", tier=tier)
    return True


def prompt_block(db, conv_id, current=None):
    """给 AI 的地点上下文片段"""
    names = ensure_defaults(db, conv_id)
    current = current or db.get_current_location(conv_id)
    listing = "、".join(names[-40:])
    return (
        f"【当前地点】{current}\n"
        f"【已知地点（只能从这里选，或申请一个新地点）】{listing}\n"
        "如果场景确实转移到新地方，请直接输出新的【地点】名称，系统会自动登记。"
    )
