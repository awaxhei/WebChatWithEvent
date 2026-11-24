"""尤夏与青梅的日程表

时间锚定真实世界之后，日程就可以按「真实几点」来排：她几点上课、几点吃饭、
几点该睡——不再靠模型随口编。日程只做纯计算，不调模型、不碰数据库。

每个时段（block）：

    start/end   分钟（0..1440）
    activity    在做什么（给提示词看的一句话）
    location    在哪儿（地名，必须是地点表里可能出现的名字）
    reply       她回消息的状态：free（随时）/ busy（忙，回得短慢）/ asleep（睡着）
    enterable   这段时间玩家能不能进那个地方（上课中的教室不能进）
    tags        给离线结算用的标签（class / meal / rest / free / sleep）
"""
import datetime

# 上课的日子：周一到周五；周末换成另一份
WEEKDAY_YUXIA = [
    (0, 7 * 60, "睡着", "尤夏的房间", "asleep", False, "sleep"),
    (7 * 60, 7 * 60 + 40, "刚起床，洗漱穿衣服", "尤夏的房间", "busy", False, "morning"),
    (7 * 60 + 40, 8 * 60, "在厨房弄早饭", "厨房", "free", True, "meal"),
    (8 * 60, 11 * 60 + 40, "在上上午的课", "大学教室", "busy", False, "class"),
    (11 * 60 + 40, 12 * 60 + 40, "在食堂吃午饭", "食堂", "free", True, "meal"),
    (12 * 60 + 40, 14 * 60, "回宿舍午休", "尤夏的房间", "busy", False, "rest"),
    (14 * 60, 17 * 60 + 40, "在上下午的课", "大学教室", "busy", False, "class"),
    (17 * 60 + 40, 18 * 60 + 40, "在食堂吃晚饭", "食堂", "free", True, "meal"),
    (18 * 60 + 40, 21 * 60, "在图书馆写作业", "图书馆", "busy", False, "study"),
    (21 * 60, 23 * 60, "回公寓了，窝在沙发上看东西", "公寓客厅", "free", True, "free"),
    (23 * 60, 24 * 60, "已经睡了", "尤夏的房间", "asleep", False, "sleep"),
]

WEEKEND_YUXIA = [
    (0, 8 * 60, "睡着", "尤夏的房间", "asleep", False, "sleep"),
    (8 * 60, 9 * 60, "赖床，醒了又赖回去", "尤夏的房间", "busy", False, "morning"),
    (9 * 60, 10 * 60, "在厨房捣鼓早饭", "厨房", "free", True, "meal"),
    (10 * 60, 12 * 60, "在公寓里晃，打游戏", "公寓客厅", "free", True, "free"),
    (12 * 60, 13 * 60, "出门吃午饭", "食堂", "free", True, "meal"),
    (13 * 60, 15 * 60, "午睡", "尤夏的房间", "busy", False, "rest"),
    (15 * 60, 18 * 60, "出门逛，可能去咖啡店", "咖啡店", "free", True, "free"),
    (18 * 60, 19 * 60, "吃晚饭", "食堂", "free", True, "meal"),
    (19 * 60, 23 * 60, "窝在公寓", "公寓客厅", "free", True, "free"),
    (23 * 60, 24 * 60, "睡了", "尤夏的房间", "asleep", False, "sleep"),
]

# 青梅的日程：和尤夏错开一点，用来判断"她以为你在哪、该不该等你"
WEEKDAY_QINGMEI = [
    (0, 7 * 60 + 20, "睡觉", "青梅的房间", "asleep"),
    (7 * 60 + 20, 8 * 60, "洗漱、出门", "公寓玄关", "free"),
    (8 * 60, 11 * 60 + 40, "上课", "大学教室", "busy"),
    (11 * 60 + 40, 12 * 60 + 40, "午饭", "食堂", "free"),
    (12 * 60 + 40, 14 * 60, "午休", "青梅的房间", "busy"),
    (14 * 60, 17 * 60 + 40, "上课", "大学教室", "busy"),
    (17 * 60 + 40, 18 * 60 + 40, "晚饭", "食堂", "free"),
    (18 * 60 + 40, 22 * 60, "在图书馆/自习", "图书馆", "busy"),
    (22 * 60, 24 * 60, "回公寓", "公寓客厅", "free"),
]

WEEKEND_QINGMEI = [
    (0, 9 * 60, "睡觉", "青梅的房间", "asleep"),
    (9 * 60, 12 * 60, "在公寓", "公寓客厅", "free"),
    (12 * 60, 13 * 60, "午饭", "食堂", "free"),
    (13 * 60, 18 * 60, "出门", "咖啡店", "free"),
    (18 * 60, 23 * 60, "在公寓", "公寓客厅", "free"),
    (23 * 60, 24 * 60, "睡了", "青梅的房间", "asleep"),
]


def is_weekend():
    """故事时间锚定真实世界，所以「今天周几」直接看真实日期"""
    return datetime.datetime.now().weekday() >= 5


def _blocks_for(who):
    weekend = is_weekend()
    if who == "yuxia":
        return WEEKEND_YUXIA if weekend else WEEKDAY_YUXIA
    return WEEKEND_QINGMEI if weekend else WEEKDAY_QINGMEI


def block_at(minutes, who="yuxia"):
    """这一刻她/他在做什么"""
    m = int(minutes) % (24 * 60)
    for block in _blocks_for(who):
        if who == "yuxia":
            start, end, activity, location, reply, enterable, tag = block
        else:
            start, end, activity, location, reply = block
            enterable, tag = True, "qingmei"
        if start <= m < end:
            return {
                "activity": activity,
                "location": location,
                "reply": reply,
                "enterable": enterable,
                "tag": tag,
                "start": start,
                "end": end,
            }
    return {"activity": "空着", "location": "", "reply": "free",
            "enterable": True, "tag": "free", "start": 0, "end": 0}


def blocks_between(from_minutes, to_minutes, who="yuxia", limit=6):
    """这段时间里已经结束的日程段（离线结算用）

    从 from 到 to 之间，按天切开，收集每一段的结束时刻落在这个区间里的做事段。
    """
    out = []
    if to_minutes <= from_minutes:
        return out
    day = (int(from_minutes) // 1440) * 1440
    while day < int(to_minutes):
        for block in _blocks_for(who):
            s = day + block[0]
            e = day + block[1]
            if e <= from_minutes or s >= to_minutes:
                continue
            if e > to_minutes:
                continue          # 还没结束的段不结算，只说明「她还在做」
            out.append({
                "start": s, "end": e,
                "activity": block[2], "location": block[3],
                "reply": block[4], "tag": block[6],
            })
        day += 1440
    return out[-limit:]


def describe(minutes, who="yuxia"):
    """给提示词的一句话：她此刻在做什么"""
    b = block_at(minutes, who)
    if not b["activity"]:
        return ""
    return (f"【此刻的日程】{b['activity']}"
            + (f"，在{b['location']}" if b["location"] else "")
            + {"asleep": "（睡着了）", "busy": "（忙，回消息会短、会慢）",
               "free": ""}[b["reply"]])


def player_may_enter(location, minutes):
    """玩家能不能进这个地方：上课中的教室进不去"""
    b = block_at(minutes, "yuxia")
    if b["tag"] == "class" and b["location"] == location:
        return False, f"{b['activity']}，这时候进不去"
    return True, ""
