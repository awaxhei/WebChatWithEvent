"""系统自己写的那些话

聊天里有一部分文字不是她说的、也不是玩家说的，而是系统在交代状态：
「青梅走到了厨房」「尤夏睡着了」。这些句子要么进提示词、要么直接显示在界面上，
所以统一在这里写：

- 叙述用第三人称、书面语，句末加句号
- 不用语气词（呀、啦、哦、嘛、呢、吧、哈、嘿、诶、嘛），不用「啦」这种口语收尾
- 用「（……）」包起来的都是旁白，不是台词
"""


def note(text):
    """旁白式系统提示：统一加括号和句号"""
    body = (text or "").strip().rstrip("。")
    if not body:
        return ""
    return f"（{body}。）"


# 换地方
MOVE_PLAYER = "青梅走到了{place}"
MOVE_TOGETHER = "青梅拉着尤夏一起去了{place}"
MOVE_BLOCKED = "青梅想让尤夏一起去{place}，但两人并不在一起"

# 状态
FALL_ASLEEP = "尤夏睡着了"
WAKE_UP = "尤夏迷迷糊糊地睁开眼睛"
STICKER_RECEIVED = "青梅发来一个表情包"
STICKER_RECEIVED_WITH_FEEL = "青梅发来一个表情包，情绪是「{feel}」"

# 玩家做的动作（「你的动作」模式）：按旁白记录下来，而不是当成一句台词
PLAYER_ACTION = "青梅{action}"

# 给模型看的系统提示（不显示给玩家）
RETRY_HINT = ("【系统】上一轮没有产生任何新内容（可能重复了你刚说过的话）。"
              "请换一种说法，至少输出一句话，或一个动作。")


def move_player(place):
    return note(MOVE_PLAYER.format(place=place))


def move_together(place):
    return note(MOVE_TOGETHER.format(place=place))


def move_blocked(place):
    return note(MOVE_BLOCKED.format(place=place))


def sticker_received(desc, feel=""):
    if feel:
        body = f"{STICKER_RECEIVED_WITH_FEEL.format(feel=feel)}，画面是：{desc}"
    else:
        body = f"{STICKER_RECEIVED}，画面是：{desc}"
    return note(body)


def player_action(action):
    """玩家做的动作：写成第三人称旁白，直接放进时间线。

    她读到的是「（青梅把拖鞋放到她脚边。）」这种一行旁白，就像世界里刚发生的事，
    不需要额外的"这一轮是动作"提示。
    """
    body = (action or "").strip()
    if not body:
        body = "轻轻碰了碰她"
    if body.startswith("我"):
        body = "青梅" + body[1:].lstrip()
    elif body.startswith(("青梅", "尤夏")):
        pass
    else:
        body = "青梅" + body
    return note(body)


def wake_up():
    return note(WAKE_UP)


# 供测试检查：所有对外可见的系统句子
PUBLIC_TEMPLATES = (
    MOVE_PLAYER, MOVE_TOGETHER, MOVE_BLOCKED,
    FALL_ASLEEP, WAKE_UP, STICKER_RECEIVED, STICKER_RECEIVED_WITH_FEEL,
    PLAYER_ACTION, RETRY_HINT,
)
