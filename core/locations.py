"""
地点系统 —— 地点是真实存在的实体，而不是只活在上下文里的一句话

- 每个会话有一张自己的地点表（初始为 config.DEFAULT_LOCATIONS）
- AI 只能"选择已有地点"或"申请新建地点"，由服务端校验后落库
- 当前地点持久化在 conversations.current_location，跨轮次稳定
- 提供 prompt 片段，把可选地点注入给 AI B / AI A
"""
import re

from config import DEFAULT_LOCATIONS

MAX_LOCATIONS_PER_CONV = 80
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
    """首次访问该会话时写入默认地点，并把玩家放到尤夏身边"""
    names = db.get_location_names(conv_id)
    if not names:
        for name in DEFAULT_LOCATIONS:
            db.add_location(conv_id, name)
        names = db.get_location_names(conv_id)
    if not db.get_current_location(conv_id):
        db.set_current_location(conv_id, names[0] if names else DEFAULT_LOCATIONS[0])
    # 玩家默认和尤夏在同一个地方，否则新会话一开始就变成"异地"
    if not db.get_player_location(conv_id):
        db.set_player_location(conv_id, db.get_current_location(conv_id))
    return names


def get_current(db, conv_id):
    ensure_defaults(db, conv_id)
    return db.get_current_location(conv_id)


def list_names(db, conv_id):
    """会话已知地点列表"""
    return ensure_defaults(db, conv_id)


def resolve(db, conv_id, candidate):
    """把 AI 给出的地点名归一化到已有地点；不存在则新建。

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
    db.add_location(conv_id, name)
    return name, True


def switch(db, conv_id, candidate):
    """切换当前地点，返回最终地点名"""
    name, _created = resolve(db, conv_id, candidate)
    if name:
        db.set_current_location(conv_id, name)
    return name or get_current(db, conv_id)


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
