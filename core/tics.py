"""口头禅限流

「唔姆」是她的停顿，不是口头禅。人设里写的是「十句里至多一句」，但模型只看得见
自己前面的消息——上一条用了，下一条就跟着用，越聊越密，最后每条消息都从「唔姆」开头。

所以这里在服务端做一道限流：
- 一轮回复里最多留一个「唔姆」；
- 最近几条消息里已经出现过，这一轮一个都不留。
去掉的只是这一声和紧跟的标点，后面的话原样保留。
"""
import re

TIC = "唔姆"

# 这一声后面常见的标点与空白
_TRAIL = "，,、…．. \u3000"
# 打头的标点/空白：删掉「唔姆」后不能留下一个孤零零的逗号
_LEAD = "，,、…．. \u3000"
_LEAD_RE = re.compile(r"^[，,、…．. \u3000]+")
_DUP_RE = re.compile(r"[，,]{2,}")


def _tidy(text):
    out = _DUP_RE.sub("，", (text or "").strip())
    out = _LEAD_RE.sub("", out)
    out = out.replace("。，", "。").replace("，。", "。")
    if not out.strip("。…，,、．. "):
        return ""
    return out


class Budget:
    """一轮回复里允许出现几次；跨对象累减"""

    def __init__(self, allowed=1):
        self.left = max(0, int(allowed))

    def clean(self, text):
        """把一个字段（对白 / 消息 / 动作）洗一遍，超出额度的「唔姆」删掉"""
        if not text or TIC not in text:
            return text
        out, rest = "", text
        while TIC in rest:
            before, rest = rest.split(TIC, 1)
            out += before
            if self.left > 0:
                self.left -= 1
                out += TIC
            else:
                rest = rest.lstrip(_TRAIL)
        return _tidy(out + rest)


def recent_usage(db, conv_id, lookback=4):
    """最近几条她说的话里用过几次「唔姆」"""
    try:
        rows = [m for m in db.get_messages(conv_id) if m.get("role") == "assistant"]
    except Exception:
        return 0
    hits = 0
    for row in rows[-lookback:]:
        if TIC in (row.get("content") or ""):
            hits += 1
    return hits


def budget_for(db, conv_id):
    """这一轮留几个：最近用过就不留"""
    return 0 if recent_usage(db, conv_id) else 1
