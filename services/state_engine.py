"""尤夏的状态：清醒 / 睡眠 / 迷糊 / 兴奋 / 饥饿 / 饱腹

为什么要有这个：
她的说话方式不该一成不变——刚睡醒、饿着、吃饱了、被说到高兴处，语气是不同的。
状态是**设定**，不是开关：它只改变"她此刻怎么说话"，不改变她是谁。

两条来源：
1. **规则**（不花钱、稳定）：夜里到点就是睡，天亮人会迷糊，饭点前后会饿。
   睡眠时她不会有反应——这是设定，不是 bug；叫醒她要单独问一次模型。
2. **她自己报**：她回复里可以带一个 `状态` 字段（兴奋 / 饥饿 / 饱腹 / 迷糊 / 清醒），
   服务端认它。这样不用额外加一次调用。

睡眠状态的门在 routes/chat_routes.py：睡着时不走正常回复，只留一句旁白。
"""
from config import DEEPSEEK_THINKING

STATES = ("清醒", "睡眠", "迷糊", "兴奋", "饥饿", "饱腹", "忙碌", "感冒", "发烧")

# 每个状态给模型的"怎么说话"（只在她醒着时注入提示词）
STYLE = {
    "清醒": "平常的样子：短、平、慢半拍。",
    "迷糊": "刚醒，脑子还没转起来：句子更短、更容易走神，反应比平时还慢半拍，"
            "偶尔会答岔了再改口。今天不要长篇大论。",
    "兴奋": "心情好，话比平时多一点、语速快一点，耳朵和尾巴的反应也更明显；"
            "但依旧不会喊、不会连用感叹号。",
    "饥饿": "饿着的时候注意力会被吃的勾走：先想到吃的，再回答问题，句子更短。"
            "「饿了」这件事可以说，但不要撒娇。",
    "饱腹": "刚吃饱，人懒下来：动作慢、话更少、更软，容易想躺着。",
    "睡眠": "睡着了（只有极小的反应，不会说话）。",
    "忙碌": "手上正忙着一件事：回答会更短、更急，注意力被手上的事占着，"
            "偶尔会说「等一下」「马上」。不会因为忙就不理人，但不铺开聊。",
    "感冒": "有点感冒：鼻子不通、声音闷，人懒懒的，句子更短；"
            "会提到自己不舒服，但不诉苦、不撒娇，也不要人照顾。",
    "发烧": "在发烧：脑子糊涂、反应更慢，说话有点断，会怕冷；"
            "被关心时会嘴硬（「没事」），但身体反应是瞒不住的。",
}

# 夜里到这个点她就睡了（分钟）
_SLEEP_FROM = 23 * 60
_SLEEP_TO = 6 * 60 + 30
# 刚醒的迷糊期
_DAZE_FROM = 6 * 60 + 30
_DAZE_TO = 8 * 60


def rules_state(now_minutes):
    """按故事时间给一个"该是什么状态"；返回空串表示时间上不做要求"""
    m = int(now_minutes) % (24 * 60)
    if m >= _SLEEP_FROM or m < _SLEEP_TO:
        return "睡眠"
    if _DAZE_FROM <= m < _DAZE_TO:
        return "迷糊"
    return ""


def normalize(state):
    state = (state or "").strip()
    return state if state in STATES else ""


def get(db, conv_id):
    """她现在的状态；没有就按时间推一个并落库"""
    state = normalize(db.get_yuxia_state(conv_id))
    return state or "清醒"


# 由事件造成的状态会自然过去：忙完就闲下来，感冒发烧睡一觉就好
# 单位是故事分钟；到点自动回到「清醒」
STALE_AFTER = {"忙碌": 90, "感冒": 360, "发烧": 480}


def set_state(db, conv_id, state, now_minutes=None):
    state = normalize(state)
    if not state:
        return ""
    try:
        db.set_yuxia_state(conv_id, state)
        if now_minutes is not None:
            db.set_yuxia_state_at(conv_id, int(now_minutes))
    except Exception:
        pass
    return state


def expire(db, conv_id, now_minutes):
    """事件带来的状态会过期：忙完、病好，自动回「清醒」"""
    state = get(db, conv_id)
    ttl = STALE_AFTER.get(state)
    if not ttl:
        return state
    try:
        at = db.get_yuxia_state_at(conv_id)
    except Exception:
        return state
    if at and now_minutes - at >= ttl:
        return set_state(db, conv_id, "清醒", now_minutes)
    return state


def apply_rules(db, conv_id, now_minutes):
    """时间规则：到点睡、天亮迷糊；其余状态保持不动"""
    state = get(db, conv_id)
    want = rules_state(now_minutes)
    # 只有夜里强行进睡眠（免得她半夜还精神抖擞）；天亮的迷糊只从睡眠里醒来时给
    if state in ("感冒", "发烧", "忙碌"):
        return state          # 生病/忙碌时不按时间强行改成睡着
    if want == "睡眠" and state != "睡眠":
        return set_state(db, conv_id, "睡眠")
    if state == "睡眠" and want != "睡眠":
        return set_state(db, conv_id, want or "清醒")
    if state == "睡眠" and want == "迷糊":
        return set_state(db, conv_id, "迷糊")
    return state


def is_asleep(db, conv_id):
    return get(db, conv_id) == "睡眠"


def apply_reply(db, conv_id, obj):
    """她自己报的状态（可选字段「状态」）"""
    if not isinstance(obj, dict):
        return ""
    return set_state(db, conv_id, obj.get("状态"))


def block(db, conv_id):
    """给对话提示词用的一小段：她此刻是什么状态、这个状态怎么说话"""
    state = get(db, conv_id)
    style = STYLE.get(state) or STYLE["清醒"]
    return (f"【你此刻的状态】{state}\n"
            f"【这个状态下的你】{style}\n"
            "（状态会变：饿了、吃饱了、刚睡醒、被说到高兴的地方，都可以在回复里"
            "带一个「状态」字段告诉系统，取值只有 清醒 / 迷糊 / 兴奋 / 饥饿 / 饱腹。）")


_WAKE_PROMPT = (
    "判断玩家这句话是不是在**试图叫醒**一个睡着的人。\n"
    "是叫醒（叫名字、让他起来、说该起床了、碰他）→ wake=true；\n"
    "只是自言自语、放东西、说话但不是冲着他叫 → wake=false。\n"
    '只输出 JSON：{"wake": true} 或 {"wake": false}'
)


def wake_attempt(deepseek, message, api_key=None):
    """只在她睡着时才调用：这句话能不能把她叫醒"""
    if not message:
        return False
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": message}],
            system_prompt=_WAKE_PROMPT,
            temperature=0.0,
            api_key_override=api_key,
            max_tokens=40,
            thinking=False,
        )
    except Exception as e:
        print(f"[状态] 叫醒判断失败: {e}")
        return False
    text = (raw or "").strip()
    if not text:
        return False
    try:
        import json
        start = text.find("{")
        if start < 0:
            return False
        data = json.loads(text[start:text.rfind("}") + 1])
        return bool(data.get("wake"))
    except Exception:
        return False
