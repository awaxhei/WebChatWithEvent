"""
风格 / 情感评审（真模型 + 评委模型）

做两件事：
1. 结构规则（确定性）：条数、字数、颜文字（当面不该有 / 手机该有）、
   「喵」、感叹号、叠字、说教词、万能词敷衍、心情取值
2. 情感打分（评委模型）：亲密 / 傲娇 / 冷淡 / 萌 / 脑回路清奇 / 说教 / 答非所问 /
   情感方向（靠近 / 回避 / 平衡）

用法：python scripts/test_style_emotion.py
产出：docs/style_report.md
"""
import io
import json
import os
import re
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402

TMP = os.path.join(ROOT, ".testtmp", "style_" + uuid.uuid4().hex[:6])
os.makedirs(TMP, exist_ok=True)
database.DB_PATH = os.path.join(TMP, "chat.db")

from app import app, db as app_db            # noqa: E402
from core.auth import generate_token         # noqa: E402
import core.locations as locations           # noqa: E402
import routes.chat_routes as chat_mod        # noqa: E402

REPORT = os.path.join(ROOT, "docs", "style_report.md")

KAOMOJI_RE = re.compile(r"[（(][^）)]{0,12}(ᗜ|●|￣|ω|‸|・|﹏|o|_)[^）)]{0,12}[）)]")
# 说教/生活指导：出现就算违例
LECTURE = ["注意身体", "多穿", "小心点", "早点睡", "早点休息", "记得吃饭", "按时吃",
           "别熬夜", "多喝热水", "路上小心", "注意安全", "要好好", "应该多吃"]
# 万能词敷衍：整句只有这些
VAGUE = {"嗯", "哦", "噢", "随便", "都行", "好啊", "好", "行", "嗯嗯", "哦哦", "……"}
REPEAT = ["哈哈", "嘿嘿", "呜呜", "嗯嗯", "啊啊"]
CMD_START = ("先", "快", "别", "记得", "注意", "小心", "去", "把")
MOODS = {"平静", "开心", "认真", "害羞", "放松", "无语", "惊讶", "不爽"}
# 口癖：唔姆 是招牌（目标使用率 20%~40%），唔 / 嗯 是最短的两个版本
TIC_MAIN = "唔姆"
TIC_ALL = ("唔姆", "唔", "嗯")
# 口癖的错误用法：叠用、拉长
TIC_BAD_RE = re.compile(r"(唔姆|唔|嗯)\1|(唔姆|唔|嗯)\s*[～~—]+|唔姆[诶啊嗯呀哦]")

JUDGE_PROMPT = (
    "你在给一段角色扮演对话做质量评审。\n"
    "设定：尤夏 20 岁，猫娘，面无表情、话少、心口不一、有点孩子气；\n"
    "青梅是她同居的青梅竹马，两人关系很近但没有挑明。\n\n"
    "只评「尤夏的这一句回复」，输出一个 JSON：\n"
    '{"亲密":0-5,"傲娇":0-5,"冷淡":0-5,"萌":0-5,"脑回路清奇":true/false,'
    '"说教":true/false,"答非所问":true/false,"情感方向":"靠近|回避|平衡","理由":"不超过20字"}\n\n'
    "口径：\n"
    "- 亲密：有没有把关系往前推（在意、靠近、想被注意、独占）\n"
    "- 傲娇：心口不一的程度\n"
    "- 冷淡：语气平、句子短（三无的正常表现，不等于差）\n"
    "- 萌：孩子气、小动物感、让人心软的程度\n"
    "- 脑回路清奇：理由偏离常理但自洽\n"
    "- 说教：在叮嘱/教育对方怎么生活（应当 false）\n"
    "- 答非所问：对方问的是具体的事（吃没吃、几点、在哪、谁）却没回应才算 true；\n"
    "  对方问的是感情/私密问题（喜欢吗、想我吗）时，嘴硬回避、反问、岔开都不算答非所问\n"
    "- 情感方向：这句让人感觉她在靠近、在回避，还是平衡\n"
    "只输出 JSON。"
)

# (场景名, 青梅说的话, 通道)
CASES = [
    # 夸奖 / 被夸
    ("被夸好看", "你今天穿这件真的好看", "chat"),
    ("被夸可爱", "你刚才那个样子怪可爱的", "chat"),
    ("被当面夸", "我发现我越来越喜欢和你待在一起了", "chat"),
    # 吃醋 / 第三方
    ("吃醋-同事", "今天公司新来的女生请我喝奶茶", "chat"),
    ("吃醋-邻居", "楼下邻居好像对我有点意思", "chat"),
    ("吃醋-游戏", "我今晚要跟朋友开黑，你自己吃", "chat"),
    ("吃醋-手机", "我刚跟别人聊了两个小时", "chat"),
    # 拒绝 / 说不
    ("拒绝-抱抱", "过来抱一下", "chat"),
    ("拒绝-吃东西", "这个我不吃了，你吃吧", "chat"),
    ("拒绝-出门", "陪我去个地方吧", "chat"),
    # 撒娇 / 想要
    ("想要-毯子", "毯子给我一条", "chat"),
    ("想要-靠近", "你坐那么远干嘛", "chat"),
    ("想要-陪伴", "我今天想赖着你", "chat"),
    ("想要-摸头", "头伸过来", "chat"),
    # 被冷落 / 忽视
    ("被冷落", "我今天一句话都不想说", "chat"),
    ("被忽视", "（我一直在看手机，没抬头）", "chat"),
    ("被晾着", "嗯，随便吧", "chat"),
    # 坦白 / 试探
    ("试探-喜欢吗", "你到底喜不喜欢我", "chat"),
    ("试探-想我吗", "我出去三天，你会想我吗", "chat"),
    ("坦白", "我好像有点喜欢你", "chat"),
    ("试探-如果", "如果我说我要搬走呢", "chat"),
    # 被戳穿
    ("戳穿-偷看", "你刚才一直在偷看我吧", "chat"),
    ("戳穿-截图", "你是不是又截我图了", "chat"),
    ("戳穿-吃醋", "你是不是吃醋了", "chat"),
    # 疲惫 / 照顾
    ("疲惫", "我今天累得动不了了", "chat"),
    ("生病", "我好像有点发烧", "chat"),
    ("被照顾", "我给你煮了粥，喝一点", "chat"),
    # 道歉 / 和好
    ("道歉", "刚才是我不好，对不起", "chat"),
    ("和好", "别生气了，我错了", "chat"),
    # 睡前 / 早安
    ("睡前", "我先睡了，晚安", "chat"),
    ("凌晨", "你还没睡？", "chat"),
    ("早安", "早安，起床了", "chat"),
    ("赖床", "再睡五分钟嘛", "chat"),
    # 距离 / 身体
    ("靠近", "你头发上有东西，别动", "chat"),
    ("牵手", "手给我", "chat"),
    ("蹭过去", "（我往你这边挪了一点）", "chat"),
    ("被摸头", "（伸手摸了摸你的头）", "chat"),
    # 日常琐事
    ("吃什么", "晚饭吃什么", "chat"),
    ("买菜", "冰箱空了，去买点东西吧", "chat"),
    ("修东西", "那个灯是不是坏了", "chat"),
    ("赶时间", "我快迟到了，先走", "chat"),
    # 被比较
    ("被比较", "我朋友的女朋友特别会做饭", "chat"),
    ("被比较-2", "别人都说你好冷淡", "chat"),
    # 抽象 / 长话题
    ("聊未来", "你想过以后要做什么吗", "chat"),
    ("聊孤独", "有时候我觉得一个人挺没意思的", "chat"),
    ("聊死亡", "你说人为什么要活着", "chat"),
    # 手机通道（异地，应该活泼 + 颜文字）
    ("手机-在干嘛", "在干嘛呢", "phone"),
    ("手机-想你", "有点想你了", "phone"),
    ("手机-生气", "你半天不回我消息", "phone"),
    ("手机-晚安", "晚安，早点睡", "phone"),
]

stats = {"cases": 0, "violations": [], "scores": [], "samples": [],
         "tic_hits": 0, "tic_any": 0, "tic_only": 0, "tic_adjacent": 0}

def parse_sse(body):
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return out


def strip_prefix(text):
    return re.sub(r"^\s*(?:尤夏|青梅|她|我)\s*[:：]\s*", "", text or "")


def unwrap(content):
    """库里的一条内容 → 她实际说出来的话（JSON 拆 对白/消息，纯文本原样）"""
    raw = (content or "").strip()
    if not raw.startswith("{"):
        return strip_prefix(raw)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return strip_prefix(raw)
    if not isinstance(obj, dict):
        return strip_prefix(raw)
    return strip_prefix(str(obj.get("对白") or obj.get("消息") or ""))


def judge(deepseek, mine, hers):
    try:
        raw = deepseek.chat_sync(
            [{"role": "user", "content": f"青梅：{mine}\n尤夏：{hers}"}],
            system_prompt=JUDGE_PROMPT,
            temperature=0.0,
            max_tokens=300,
            thinking=False,
        )
    except Exception as e:
        return {"error": str(e)}
    text = (raw or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"error": "评委没给 JSON: " + text[:60]}


def main():
    app.config["TESTING"] = True
    user = app_db.get_user("awaxhei")["id"]
    c = app.test_client()
    c.set_cookie("auth_token", generate_token(user))
    deepseek = chat_mod.deepseek

    # 一个会话：当面戏用它；手机戏先把玩家挪到别处
    conv = (c.post("/api/history/new", json={}).get_json() or {})["id"]
    locations.ensure_defaults(app_db, conv)

    for idx, (name, mine, channel) in enumerate(CASES, 1):
        stats["cases"] += 1
        co = app_db.get_current_location(conv) == app_db.get_player_location(conv)
        if channel == "phone" and co:
            c.post(f"/api/move/{conv}", json={"who": "player", "location": "厨房"})
        elif channel == "chat" and not co:
            yloc = app_db.get_current_location(conv)
            c.post(f"/api/move/{conv}", json={"who": "player", "location": yloc})

        n_before = len([m for m in app_db.get_messages(conv) if m["role"] == "assistant"])
        r = c.post("/api/chat", json={"conversation_id": conv, "message": mine})
        evs = parse_sse(r.data.decode("utf-8"))
        if not any(e.get("type") == "done" for e in evs):
            stats["violations"].append(f"[{name}] 接口失败")
            continue
        done_channel = next((e.get("channel") for e in evs if e.get("type") == "done"), "chat")

        msgs = [m for m in app_db.get_messages(conv) if m["role"] == "assistant"]
        last = (msgs[n_before:] or msgs)[-1]
        # 以**实际落库的通道**为准：她在当面也可能发一条手机消息（落 phone 通道、
        # 前端显示在手机面板里），那种行本来就是纯文本，不能当成"当面不是 JSON"
        done_channel = last.get("channel") or done_channel
        content = last["content"] or ""
        objs = []
        if content.startswith("{"):
            try:
                objs = [json.loads(content)]
            except json.JSONDecodeError:
                objs = []
        if not objs:
            # 手机通道本来就是纯文本落的库，只要有颜文字就算合格
            if done_channel == "phone" and KAOMOJI_RE.search(content):
                objs = [{"消息": strip_prefix(content)}]
            else:
                stats["violations"].append(f"[{name}] 不是 JSON：{content[:30]}")
                continue

        obj = objs[0]
        speech = strip_prefix(obj.get("对白") or "")
        phone_text = obj.get("消息") or ""
        mood = obj.get("心情") or ""
        shown = phone_text if done_channel == "phone" else speech
        if not shown:
            stats["violations"].append(f"[{name}] 空回复")
            continue
        # 一轮可能落好几条（她把话拆成两三条短 JSON），口癖那条不一定是最后一条
        turn_text = " ".join(
            strip_prefix(unwrap(m["content"])) for m in msgs[n_before:]
        ) if msgs[n_before:] else shown

        # ── 结构规则 ──
        if done_channel == "chat":
            if len(speech) > 30:
                stats["violations"].append(f"[{name}] 对白过长({len(speech)}字)")
            if KAOMOJI_RE.search(speech):
                stats["violations"].append(f"[{name}] 当面出现了颜文字")
        else:
            if not KAOMOJI_RE.search(phone_text):
                stats["violations"].append(f"[{name}] 手机消息没带颜文字")
            if len(phone_text) > 60:
                stats["violations"].append(f"[{name}] 手机消息过长({len(phone_text)}字)")
        if "喵" in shown:
            stats["violations"].append(f"[{name}] 说了「喵」")
        if shown.count("！") + shown.count("!") > 1:
            stats["violations"].append(f"[{name}] 感叹号过多")
        for w in REPEAT:
            if w in shown:
                stats["violations"].append(f"[{name}] 叠字「{w}」")
        for w in LECTURE:
            if w in shown:
                stats["violations"].append(f"[{name}] 说教式关心「{w}」")
        if shown.strip("。…，, ") in VAGUE:
            stats["violations"].append(f"[{name}] 万能词敷衍：{shown}")
        if mood and mood not in MOODS:
            stats["violations"].append(f"[{name}] 心情取值非法：{mood}")
        # 口癖：叠用/拉长是硬伤；另有使用率统计（见报告）
        if TIC_BAD_RE.search(turn_text):
            stats["violations"].append(f"[{name}] 口癖叠用或拉长：{turn_text[:20]}")
        tic_hits = turn_text.count(TIC_MAIN)
        if tic_hits > 1:
            stats["violations"].append(f"[{name}] 一轮里「唔姆」出现 {tic_hits} 次")
        if tic_hits:
            stats["tic_hits"] += 1
        starts_tic = turn_text.lstrip().startswith(TIC_MAIN) or \
            turn_text.lstrip("。…，, ").startswith(TIC_MAIN)
        if starts_tic and stats.get("_prev_tic"):
            stats["tic_adjacent"] += 1
        stats["_prev_tic"] = starts_tic
        if any(t in turn_text for t in TIC_ALL):
            stats["tic_any"] += 1
        # 整条只有口癖：偶尔可以（心情糟），多了就是敷衍
        if turn_text.strip("。…，, ！!") in (TIC_MAIN, "唔", "嗯"):
            stats["tic_only"] += 1

        # ── 情感评分 ──
        score = judge(deepseek, mine, shown)
        if score.get("error"):
            stats["violations"].append(f"[{name}] 评委失败：{score['error'][:40]}")
        else:
            score["name"] = name
            score["shown"] = shown
            stats["scores"].append(score)
            if len(stats["samples"]) < 200:
                stats["samples"].append((name, mine, shown, score))
        print(f"  {idx}/{len(CASES)} {name} ok")

    if stats["tic_only"] > 2:
        stats["violations"].append(f"[全篇] 整条只有口癖的回复有 {stats['tic_only']} 条（最多 2 条）")
    write_report()
    print(f"\n完成 {stats['cases']} 个场景，违例 {len(stats['violations'])} 条")
    return 0


def write_report():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    sc = stats["scores"]
    n = max(1, len(sc))
    avg = {k: round(sum(s.get(k, 0) or 0 for s in sc) / n, 2)
           for k in ("亲密", "傲娇", "冷淡", "萌")}
    weird = sum(1 for s in sc if s.get("脑回路清奇"))
    lecture = sum(1 for s in sc if s.get("说教"))
    off = sum(1 for s in sc if s.get("答非所问"))
    near = sum(1 for s in sc if s.get("情感方向") == "靠近")
    away = sum(1 for s in sc if s.get("情感方向") == "回避")
    lines = ["# 风格 / 情感评审报告", ""]
    lines.append(f"- 场景数：**{len(sc)}**")
    lines.append(f"- 平均分：亲密 {avg['亲密']} / 傲娇 {avg['傲娇']} / 冷淡 {avg['冷淡']} / 萌 {avg['萌']}")
    lines.append(f"- 脑回路清奇：{weird} 条；说教：{lecture} 条；答非所问：{off} 条")
    lines.append(f"- 情感方向：靠近 {near} / 回避 {away} / 平衡 {len(sc) - near - away}")
    tic_rate = round(100.0 * stats["tic_hits"] / n)
    any_rate = round(100.0 * stats["tic_any"] / n)
    lines.append(f"- 口癖「唔姆」：{stats['tic_hits']} 条（{tic_rate}%）；"
                 f"含唔/嗯/唔姆的 {stats['tic_any']} 条（{any_rate}%）；"
                 f"整条只有口癖 {stats['tic_only']} 条；连着两轮都用 {stats['tic_adjacent']} 次")
    off_names = [s["name"] for s in sc if s.get("答非所问")]
    weird_names = [s["name"] for s in sc if s.get("脑回路清奇")]
    if off_names:
        lines.append(f"- 答非所问的场景：{'、'.join(off_names)}")
    if weird_names:
        lines.append(f"- 脑洞的场景：{'、'.join(weird_names)}")
    lines.append("")
    lines.append(f"## 规则违例（{len(stats['violations'])}）")
    for v in stats["violations"][:60]:
        lines.append(f"- {v}")
    lines.append("")
    lines.append("## 逐条（按萌分从低到高，先看最差的）")
    for s in sorted(sc, key=lambda x: (x.get("萌") or 0, x.get("亲密") or 0))[:20]:
        lines.append(f"- **{s['name']}** 萌{s.get('萌')} 亲密{s.get('亲密')} 傲娇{s.get('傲娇')} "
                     f"冷淡{s.get('冷淡')} 方向={s.get('情感方向')} 理由={s.get('理由')}")
        lines.append(f"  - 尤夏：{s['shown']}")
    lines.append("")
    lines.append("## 逐条（全部）")
    for s in sc:
        flags = []
        if s.get("脑回路清奇"):
            flags.append("脑洞")
        if s.get("说教"):
            flags.append("说教")
        if s.get("答非所问"):
            flags.append("答非所问")
        lines.append(f"- **{s['name']}**｜萌{s.get('萌')} 亲密{s.get('亲密')} 傲娇{s.get('傲娇')} "
                     f"冷淡{s.get('冷淡')} {'/'.join(flags) or '-'} "
                     f"方向={s.get('情感方向')}｜{s['shown']}")
    with io.open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
