"""
故事时钟 —— 确定性时间系统

原则：时间由服务器按规则推进，LLM 永远不写时间。
- 每轮用户消息：+CLOCK_MINUTES_PER_TURN 分钟
- 命中关键词：按固定规则跳到对应时间点（只前进，不倒退）
- 时间观察者 LLM：只能提交一个"跳跃类型"枚举，具体分钟数由本模块换算

内部表示：story_minutes = 距"第 0 天 00:00"的绝对分钟数。
显示格式：第 N 天 HH:MM
"""
import re

from config import (
    CLOCK_START_MINUTES, CLOCK_MINUTES_PER_TURN, CLOCK_MAX_JUMP_MINUTES,
)

DAY_MINUTES = 1440

# 跳跃类型 → (目标小时, 目标分钟, 是否跳到次日)
JUMP_TARGETS = {
    "NEXT_MORNING": (7, 0, True),
    "NOON": (12, 0, False),
    "AFTERNOON": (16, 30, False),
    "EVENING": (18, 0, False),
    "LATE_NIGHT": (22, 30, False),
    "NEXT_DAY": (8, 0, True),
}

# 关键词规则：命中即确定性跳跃（不经过 LLM），按顺序匹配，先命中者生效
KEYWORD_RULES = [
    (re.compile(r"晚安|睡了|睡觉|困了|去睡|休息了|要睡了"), "NEXT_MORNING"),
    (re.compile(r"早上好|早安|起床|天亮了|一大早"), "MORNING_TODAY"),
    (re.compile(r"午饭|中午|吃午饭|午休"), "NOON"),
    (re.compile(r"放学|下课|下午了"), "AFTERNOON"),
    (re.compile(r"晚饭|晚餐|傍晚|天黑了|天黑"), "EVENING"),
    (re.compile(r"深夜|很晚了|凌晨|半夜"), "LATE_NIGHT"),
    (re.compile(r"第二天|明天|隔天|过几天|后一天"), "NEXT_DAY"),
]


def day_of(minutes):
    """返回从 1 开始的天数"""
    return minutes // DAY_MINUTES + 1


def time_of_day(minutes):
    """返回当天 (小时, 分钟)"""
    m = minutes % DAY_MINUTES
    return m // 60, m % 60


_CN_DIGITS = "零一二三四五六七八九"


def cn_number(n):
    """1 → 一，10 → 十，21 → 二十一（够用到 99 天）"""
    n = int(n)
    if n <= 0:
        return _CN_DIGITS[0]
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def format_time(minutes):
    """第 N 天 HH:MM（N 用中文数字，和前端事件面板的分组方式一致）"""
    h, m = time_of_day(minutes)
    return f"第{cn_number(day_of(minutes))}天 {h:02d}:{m:02d}"


def parse_hhmm(text):
    """从「第一天早晨 7:00」这类文本里解析出分钟数；失败返回 None"""
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text or "")
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return None
    return h * 60 + mm


def parse_day_number(text):
    """从「第一天」「第十二天」里解析天数，失败返回 None"""
    m = re.search(r"第\s*([一二三四五六七八九十百\d]+)\s*天", text or "")
    if not m:
        return None
    raw = m.group(1)
    if raw.isdigit():
        return int(raw)
    digits = {c: i for i, c in enumerate(_CN_DIGITS)}
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(raw) == 1 and raw in digits:
        return digits[raw]
    return None


def parse_display_time(text):
    """把「第一天早晨 7:00」「第三天下午 15:20」还原成绝对分钟数"""
    day = parse_day_number(text)
    minute_of_day = parse_hhmm(text)
    if day is None or minute_of_day is None:
        return None
    return (day - 1) * DAY_MINUTES + minute_of_day


def initial_minutes():
    return CLOCK_START_MINUTES


def tick(minutes, n=None):
    """基础推进：每轮 +N 分钟"""
    return minutes + (CLOCK_MINUTES_PER_TURN if n is None else n)


def detect_keyword_jump(text):
    """检测文本里的时间跳跃关键词，返回跳跃类型或 None"""
    if not text:
        return None
    for pattern, jump in KEYWORD_RULES:
        if pattern.search(text):
            return jump
    return None


def target_minutes(now, jump):
    """把跳跃类型换算成绝对分钟数。

    返回 None 表示"不该跳"（例如已经过了那个时间点，不允许倒退）。
    """
    if jump == "MORNING_TODAY":
        # 已经在中午之前就说明是当前早晨，不跳
        if time_of_day(now)[0] < 12:
            return None
        jump = "NEXT_MORNING"
    if jump not in JUMP_TARGETS:
        return None

    hour, minute, next_day = JUMP_TARGETS[jump]
    day_start = (now // DAY_MINUTES) * DAY_MINUTES
    target = day_start + hour * 60 + minute
    if next_day:
        target += DAY_MINUTES
    if target <= now:
        return None
    return min(target, now + CLOCK_MAX_JUMP_MINUTES)


def apply_jump(now, jump):
    """应用跳跃并返回 (新分钟数, 跳过的分钟数)；不合法时返回 (now, 0)"""
    target = target_minutes(now, jump)
    if target is None or target <= now:
        return now, 0
    return target, target - now


def elapsed_hours(newer, older):
    """两个时间点之间的"故事小时数"，用于记忆时效性衰减"""
    return max(0.0, (newer - older) / 60.0)
