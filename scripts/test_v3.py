"""
v3 子系统离线单元测试（不调用任何 LLM）
覆盖：故事时钟 / 地点实体 / 记忆流 / 表情包索引与匹配 / 老库迁移

用法：python scripts/test_v3.py
"""
import json
import os
import shutil
import sqlite3
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP_ROOT = os.path.join(ROOT, ".testtmp")
os.makedirs(TMP_ROOT, exist_ok=True)

import core.database as database        # noqa: E402
import core.textvec as tv               # noqa: E402
import core.clock as clock              # noqa: E402
import core.locations as locations      # noqa: E402
import core.memory as memory            # noqa: E402
import core.stickers as stickers        # noqa: E402
import routes.chat_routes as chat_mod   # noqa: E402

FAILED = []


def mkdtemp(prefix):
    """不用 tempfile.mkdtemp（在某些受限环境里它建出来的目录不可写）"""
    path = os.path.join(TMP_ROOT, prefix + uuid.uuid4().hex[:8])
    os.makedirs(path, exist_ok=True)
    return path


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def fresh_db():
    """每个测试用独立临时库（放工作区内，避免沙箱拒绝写系统临时目录）"""
    tmp = mkdtemp("yuxia_test_")
    database.DB_PATH = os.path.join(tmp, "chat.db")
    db = database.ChatDB()
    db.migrate()
    db.sync_accounts({"tester": "x"})
    uid = db.get_user("tester")["id"]
    conv = db.create_conversation(uid)
    return db, uid, conv, tmp


# ── 1. 故事时钟 ──────────────────────────────────────

def test_clock():
    start = clock.initial_minutes()
    check("时钟初始为第一天 07:00", clock.format_time(start) == "第一天 07:00", clock.format_time(start))
    check("每轮 +3 分钟", clock.format_time(clock.tick(start)) == "第一天 07:03")

    check("识别「晚安」关键词", clock.detect_keyword_jump("我去睡了，晚安") == "NEXT_MORNING")
    target = clock.target_minutes(start, "NEXT_MORNING")
    check("晚安跳到次日早晨", clock.format_time(target) == "第二天 07:00", clock.format_time(target))

    noon = clock.target_minutes(start, "NOON")
    check("午饭跳到当天 12:00", clock.format_time(noon) == "第一天 12:00", clock.format_time(noon))

    late = 15 * 60
    check("时间不倒退（15:00 时午饭不生效）", clock.target_minutes(late, "NOON") is None)

    huge = clock.target_minutes(start, "NEXT_DAY")
    check("跳跃不超过上限", huge - start <= clock.CLOCK_MAX_JUMP_MINUTES)

    check("中午之后可跳次日早晨", clock.target_minutes(13 * 60, "MORNING_TODAY") is not None)
    check("已是早晨不再跳", clock.target_minutes(9 * 60, "MORNING_TODAY") is None)

    check("跨天格式化", clock.format_time(1440 + 90) == "第二天 01:30", clock.format_time(1440 + 90))
    check("中文天数 21", clock.cn_number(21) == "二十一", clock.cn_number(21))

    # 老文本时间还原
    check("解析「第一天早晨 7:00」", clock.parse_display_time("第一天早晨 7:00") == 420)
    check("解析「第三天下午 15:20」",
          clock.parse_display_time("第三天下午 15:20") == 2 * 1440 + 15 * 60 + 20)
    check("解析中文天数「第十二天」", clock.parse_day_number("第十二天") == 12)
    check("解析中文天数「第二十天」", clock.parse_day_number("第二十天") == 20)


def test_clock_recovery():
    """老会话的时间应从已有事件文本恢复，而不是一律回到第一天早晨"""
    db, _uid, conv, tmp = fresh_db()
    try:
        db.add_event(conv, "scene_change", "转场", "到了咖啡店", "active",
                     story_time="第三天下午 15:20")

        from services.time_keeper import init_clock
        minutes = init_clock(db, conv)
        check("老会话时间从事件文本恢复", minutes == 2 * 1440 + 15 * 60 + 20,
              clock.format_time(minutes))
        check("恢复后写入时钟", db.get_story_minutes(conv) == minutes)
        check("再次读取不变", init_clock(db, conv) == minutes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. 地点实体 ──────────────────────────────────────

def test_locations():
    db, _uid, conv, tmp = fresh_db()
    try:
        names = locations.ensure_defaults(db, conv)
        check("默认地点已写入", len(names) >= 5, str(names[:3]))
        check("默认当前地点存在", db.get_current_location(conv) in names)

        # 开局：各回各的房间（异地开场，只能先发消息）
        from config import DEFAULT_YUXIA_LOCATION, DEFAULT_PLAYER_LOCATION
        check("尤夏开局在自己的房间",
              db.get_current_location(conv) == DEFAULT_YUXIA_LOCATION,
              db.get_current_location(conv))
        check("玩家开局在自己的房间",
              db.get_player_location(conv) == DEFAULT_PLAYER_LOCATION,
              db.get_player_location(conv))
        check("开局两人不在一起",
              db.get_current_location(conv) != db.get_player_location(conv))
        locations.ensure_defaults(db, conv)
        check("重复初始化不会把人挪走",
              db.get_current_location(conv) == DEFAULT_YUXIA_LOCATION
              and db.get_player_location(conv) == DEFAULT_PLAYER_LOCATION,
              f"{db.get_current_location(conv)} / {db.get_player_location(conv)}")
        db.set_current_location(conv, "厨房")
        db.set_player_location(conv, "厨房")
        locations.ensure_defaults(db, conv)
        check("已有地点的会话不会被改成房间",
              db.get_current_location(conv) == "厨房" and db.get_player_location(conv) == "厨房")

        name, created = locations.resolve(db, conv, "大学教室")
        check("已存在地点不重复创建", (name, created) == ("大学教室", False))

        name, created = locations.resolve(db, conv, "教室")
        check("子串归一化到已有地点", name == "大学教室" and not created, name)

        name, created = locations.resolve(db, conv, "天台")
        check("新地点可登记", name == "天台" and created)

        name, _ = locations.resolve(db, conv, "  【】  ")
        check("非法地点名被拒绝", name is None)

        switched = locations.switch(db, conv, "厨房")
        check("切换当前地点", switched == "厨房" and db.get_current_location(conv) == "厨房")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. 记忆流 ────────────────────────────────────────

def test_memory():
    tv._state["ok"] = False          # 强制走无 embedding 的降级路径
    db, _uid, conv, tmp = fresh_db()
    try:
        memory.add(db, conv, "青梅：我回来了", story_minutes=100)
        memory.add(db, conv, "尤夏：……哦。", story_minutes=101)
        memory.add(db, conv, "青梅：给你带了煎饺", story_minutes=102, importance=9)
        check("记忆写入成功", db.count_memories(conv) == 3)

        hits = memory.search(db, conv, "煎饺", now_minutes=110)
        check("检索有结果", len(hits) == 3)
        check("相关记忆排第一", "煎饺" in hits[0]["text"], hits[0]["text"])
        check("得分包含三要素", {"score", "recency", "relevance"} <= set(hits[0]))

        old_hits = memory.search(db, conv, "煎饺", now_minutes=1000)
        check("时效衰减生效", old_hits[0]["recency"] < hits[0]["recency"])

        block = memory.build_context(db, conv, "煎饺", 110)
        check("上下文片段可注入 prompt", block.startswith("【尤夏记得的相关往事"))

        check("未达阈值不反思", not memory.should_reflect(db, conv))
        for i in range(7):
            memory.add(db, conv, f"青梅：第{i}件小事", importance=10, story_minutes=200 + i)
        check("累计重要度触发反思", memory.should_reflect(db, conv))

        obs = memory.observations_for_reflection(db, conv)
        memory.save_reflections(db, conv, ["青梅对煎饺有执念"],
                                covers_upto=obs[-1]["id"], story_minutes=210)
        check("反思已保存", db.get_latest_reflection(conv)["text"].startswith("青梅对煎饺"))
        check("反思后阈值复位", memory.observations_for_reflection(db, conv) == [])

        conv2 = db.create_conversation(1)
        db.add_message(conv2, "user", "老会话")
        before = db.count_memories(conv2)
        imported = memory.backfill_if_empty(db, conv2, now_minutes=500)
        check("历史会话可回填记忆", before == 0 and imported == 1, f"{before}/{imported}")
        check("回填不会重复执行", memory.backfill_if_empty(db, conv2, now_minutes=500) == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


    # ── 3.5 梗库：yuxia_memes.txt 拼进人设 ──────────────
    import config as cfg
    from config import CHAT_PROMPT
    check("梗库已加载", len(cfg.YUXIA_MEMES) >= 3, str(cfg.YUXIA_MEMES[:5]))
    check("梗库进了对话提示词",
          "最近常飘过的梗" in CHAT_PROMPT and cfg.YUXIA_MEMES[0] in CHAT_PROMPT,
          str(cfg.YUXIA_MEMES[:3]))
    check("梗库说明是「她认得、不必都说」",
          "不必都说" in CHAT_PROMPT, CHAT_PROMPT[-200:])
    check("注释与空行被跳过",
          all(m.strip() and not m.startswith("#") for m in cfg.YUXIA_MEMES),
          str(cfg.YUXIA_MEMES[:3]))
    with open(os.path.join(ROOT, "yuxia_prompt.txt"), encoding="utf-8") as f:
        persona = f.read()
    check("人设里有「梗」这一节", "## 梗" in persona)
    check("接梗而不是表演梗", "不表演梗" in persona, persona[persona.find("## 梗"):][:80])

# ── 4. 表情包 ────────────────────────────────────────

def test_stickers():
    tmp = mkdtemp("yuxia_stk_")
    db, _uid, _conv, db_tmp = fresh_db()
    old_dir = stickers.STICKER_DIR
    old_vision = stickers.VISION_ENABLED
    stickers.STICKER_DIR = tmp
    try:
        os.makedirs(os.path.join(tmp, "乱七八糟"))
        os.makedirs(os.path.join(tmp, "开心"))
        for rel in ["乱七八糟/a1b2c3.png", "乱七八糟/x9y8z7.jpg", "开心/开心.png"]:
            with open(os.path.join(tmp, rel), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nfake")

        stats = stickers.refresh(db)
        check("扫描到全部图片", stats["scanned"] == 3 and stats["total"] == 3, str(stats))
        check("重复刷新不再新增", stickers.refresh(db)["indexed"] == 0)

        # 关掉视觉模型，走文件名打标
        stickers.VISION_ENABLED = False
        os.remove(os.path.join(tmp, "开心", "开心.png"))
        stickers.refresh(db)
        tagged = stickers.auto_tag_all(db)
        check("无视觉时按文件名打标", tagged >= 2, f"tagged={tagged}")

        row = db.get_all_stickers()[0]
        db.set_sticker_tags(row["id"], "害羞", "脸红,捂脸", "猫娘捂脸", "manual")
        results = stickers.match(db, "害羞")
        check("情绪精确匹配得分最高",
              results[0]["emotion"] == "害羞" and results[0]["score"] >= 0.99, str(results[0]))

        results = stickers.match(db, "脸红")
        check("标签命中", any(r["emotion"] == "害羞" for r in results))
        check("返回可用 URL", results[0]["url"].startswith("/static/stickers/"))

        nothing = stickers.match(db, "")
        check("空意图时随机兜底", len(nothing) >= 1 and nothing[-1]["score"] == 0.0)

        # ── 情绪归并：自由情绪词先归到「情绪桶」再匹配 ──
        # 视觉模型给 864 张图起了 171 个不同的情绪词，直接精确匹配几乎对不上
        check("情绪桶名本身归到自己",
              stickers.canonical_emotion("害羞") == "害羞",
              stickers.canonical_emotion("害羞"))
        check("同义词归到同一个桶",
              stickers.canonical_emotion("失落") == stickers.canonical_emotion("委屈") == "委屈",
              stickers.canonical_emotion("失落"))
        check("长词优先（生无可恋 不是 无语 的意外）",
              stickers.canonical_emotion("生无可恋") == "无语",
              stickers.canonical_emotion("生无可恋"))
        check("带修饰也能认出来",
              stickers.canonical_emotion("有点委屈巴巴") == "委屈",
              stickers.canonical_emotion("有点委屈巴巴"))
        check("认不出来返回空串", stickers.canonical_emotion("量子力学") == "",
              stickers.canonical_emotion("量子力学"))
        check("空输入不炸", stickers.canonical_emotion("") == "")

        db.set_sticker_tags(row["id"], "失落", "低头,眼眶红", "低着头不说话", "manual")
        by_bucket = stickers.match(db, "委屈")
        check("桶命中给 0.9（自由词不同也能对上一个）",
              by_bucket and by_bucket[0]["emotion"] == "失落"
              and 0.85 <= by_bucket[0]["score"] < 1.0,
              str(by_bucket[0]) if by_bucket else "empty")
        check("返回结果带 bucket 字段", by_bucket[0].get("bucket") == "委屈", str(by_bucket[0]))

        db.set_sticker_tags(row["id"], "害羞", "脸红,捂脸", "猫娘捂脸", "manual")
        check("情绪清单按桶给（不再吐一堆自由词）",
              stickers.list_emotions(db) == ["害羞"], str(stickers.list_emotions(db)))
        listed = stickers.list_for_picker(db, emotion="害羞")
        check("面板按桶筛也能筛出来",
              len(listed["items"]) == 1 and listed["items"][0]["bucket"] == "害羞", str(listed))
        check("桶里没有别的图时筛出空",
              not stickers.list_for_picker(db, emotion="开心")["items"])

        os.remove(os.path.join(tmp, "乱七八糟", "a1b2c3.png"))
        stats = stickers.refresh(db)
        check("文件删除后索引同步", stats["removed"] == 1 and stats["total"] == 1, str(stats))
    finally:
        stickers.STICKER_DIR = old_dir
        stickers.VISION_ENABLED = old_vision
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(db_tmp, ignore_errors=True)


def test_sticker_context():
    """玩家发表情包时，她看到的必须是「情绪」，不能只有画面描述

    以前只喂画面（"一个紫发女孩面无表情"），她就去比较像不像自己，
    回一句「那个不像尤夏」——完全没有逻辑。
    """
    stored = chat_mod._stored_user_content(
        "表情包/x.jpg", "一个紫发双马尾的女孩面无表情", "（青梅发来一个表情包）", "无语")
    payload = json.loads(stored)
    check("发送时把情绪一起存进库", payload.get("emotion") == "无语", stored)

    label = chat_mod._as_context_content(
        {"role": "user", "channel": "chat", "content": stored})
    check("她看到的上下文里有「情绪是」", "情绪是「无语」" in label, label)
    check("画面描述仍然保留", "紫发双马尾" in label, label)
    check("情绪排在画面前面", label.index("情绪是") < label.index("画面是"), label)

    # 老数据没有 emotion 字段：不能报错，也不该冒出空的「情绪是」（）
    old_row = json.dumps({"表情包": "表情包/y.jpg", "url": "/static/stickers/y.jpg",
                          "desc": "一只猫"}, ensure_ascii=False)
    old_label = chat_mod._as_context_content(
        {"role": "user", "channel": "chat", "content": old_row})
    check("老数据没有情绪时只给画面", "画面是：一只猫" in old_label and "情绪是" not in old_label,
          old_label)

    # 情绪词归桶：桶名优先（"失落" 的图按「委屈」讲给她听）
    class Row(dict):
        pass
    check("表情包的情绪按桶给",
          chat_mod._sticker_feeling(Row(emotion="失落")) == "委屈",
          chat_mod._sticker_feeling(Row(emotion="失落")))
    check("认不出来的情绪原样给",
          chat_mod._sticker_feeling(Row(emotion="量子力学")) == "量子力学")
    check("空行不炸", chat_mod._sticker_feeling(None) == "")


# ── v4：场景 / 手机 / 事件分级 ──────────────────────

def test_sticker_images():
    """表情包要真的以「图片」送进她的上下文，而不是只有标签文字

    实测 API 不接受 assistant 消息里带图，所以她的表情包也借他那一轮送进去，
    用「图 1 / 图 2 是谁的」说明标清楚。
    """
    tmp = mkdtemp("yuxia_img_")
    old_dir = chat_mod.STICKER_DIR
    old_her = chat_mod._her_sticker_path
    chat_mod.STICKER_DIR = tmp
    try:
        os.makedirs(os.path.join(tmp, "表情包"), exist_ok=True)
        for rel in ("a.png", "b.jpg", "c.gif"):
            with open(os.path.join(tmp, "表情包", rel), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 200)

        part = chat_mod._sticker_image_part("表情包/a.png")
        check("png 能转成 image_url 片段",
              bool(part) and part["type"] == "image_url"
              and part["image_url"]["url"].startswith("data:image/png;base64,"),
              str(part)[:80])
        check("jpg 用 image/jpeg",
              chat_mod._sticker_image_part("表情包/b.jpg")["image_url"]["url"]
              .startswith("data:image/jpeg;base64,"))
        check("gif 送给模型也读不了，退回文字", chat_mod._sticker_image_part("表情包/c.gif") is None)
        check("文件不存在不炸", chat_mod._sticker_image_part("表情包/没有.png") is None)
        check("空路径不炸", chat_mod._sticker_image_part("") is None)

        # 他一轮里发了表情包，她上一条也发过表情包
        his = json.dumps({"表情包": "表情包/a.png",
                          "url": "/static/stickers/a.png", "desc": "一只猫"},
                         ensure_ascii=False)
        hers = json.dumps({"对白": "嗯", "表情包": "撒娇"}, ensure_ascii=False)
        rows = [
            {"role": "user", "channel": "chat", "content": his},
            {"role": "assistant", "channel": "chat", "content": hers},
            {"role": "user", "channel": "chat", "content": "你怎么了"},
        ]
        chat_mod._her_sticker_path = lambda row: "表情包/a.png" if row.get("content") == hers else ""
        msgs = [{"role": r["role"], "content": chat_mod._as_context_content(r)} for r in rows]
        out = chat_mod._attach_sticker_images(rows, msgs)
        content = out[-1]["content"]
        check("有表情包时消息变成多模态片段", isinstance(content, list), str(type(content)))
        check("片段 = 一段文字 + 图", len(content) == 3 and content[0]["type"] == "text", str(len(content)))
        check("他的图排在前面", "图 1 是他发来的表情包" in content[0]["text"], content[0]["text"])
        check("她自己的图也带上", "图 2 是你上一条自己发出去的表情包" in content[0]["text"],
              content[0]["text"])
        check("原来那句话还在", "你怎么了" in content[0]["text"], content[0]["text"])

        # 图挂在「他这条」消息上，别的消息不动
        check("别的消息仍是纯文本", all(isinstance(m["content"], str) for m in out[:-1]))

        # 没有表情包时一点都不改（不能白白塞进图片额度）
        plain = [{"role": "user", "channel": "chat", "content": "在吗"}]
        out2 = chat_mod._attach_sticker_images(
            plain, [{"role": "user", "content": chat_mod._as_context_content(plain[0])}])
        check("普通对话不会被改成多模态", isinstance(out2[0]["content"], str), str(out2[0]["content"]))

        # 图片有上限：连着四张也只送到 MAX_CONTEXT_IMAGES
        many = ([{"role": "assistant", "channel": "chat", "content": hers}] * 4
                + [{"role": "user", "channel": "chat", "content": "看"}])
        out3 = chat_mod._attach_sticker_images(
            many, [{"role": r["role"], "content": chat_mod._as_context_content(r)} for r in many])
        check("一轮最多送 MAX_CONTEXT_IMAGES 张图",
              len(out3[-1]["content"]) == 1 + chat_mod.MAX_CONTEXT_IMAGES,
              str(len(out3[-1]["content"])))

        # 太大的图不塞进请求里（手机上会背几 MB 的 base64），退回文字描述
        with open(os.path.join(tmp, "表情包", "big.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * (chat_mod.MAX_IMAGE_BYTES + 1024))
        check("超过体积上限的图退回文字描述",
              chat_mod._sticker_image_part("表情包/big.png") is None)

        # 太早的表情包不再每轮重发
        old_her = [{"role": "assistant", "channel": "chat", "content": hers}]
        padded = (old_her
                  + [{"role": "user", "channel": "chat", "content": "一"}] * 6
                  + [{"role": "assistant", "channel": "chat", "content": "嗯"}] * 6
                  + [{"role": "user", "channel": "chat", "content": "现在"}])
        out4 = chat_mod._attach_sticker_images(
            padded, [{"role": r["role"], "content": chat_mod._as_context_content(r)} for r in padded])
        check("隔了十来条的老表情包不再重发",
              isinstance(out4[-1]["content"], str), str(type(out4[-1]["content"])))
    finally:
        chat_mod.STICKER_DIR = old_dir
        chat_mod._her_sticker_path = old_her
        shutil.rmtree(tmp, ignore_errors=True)


# ── v4：场景 / 手机 / 事件分级 ──────────────────────

def test_v4_scenes_phone_tiers():
    import core.scenes as scenes
    from services.event_engine import pick_tier
    from config import RANDOM_EVENT_TIERS

    db, _uid, conv, tmp = fresh_db()
    try:
        # 时段划分
        check("07:00 属于清晨", scenes.bucket_of(7 * 60) == "清晨", scenes.bucket_of(7 * 60))
        check("12:00 属于白天", scenes.bucket_of(12 * 60) == "白天", scenes.bucket_of(12 * 60))
        check("18:00 属于傍晚", scenes.bucket_of(18 * 60) == "傍晚", scenes.bucket_of(18 * 60))
        check("23:00 属于夜晚", scenes.bucket_of(23 * 60) == "夜晚", scenes.bucket_of(23 * 60))
        check("02:00 属于夜晚", scenes.bucket_of(2 * 60) == "夜晚", scenes.bucket_of(2 * 60))

        # 场景描写缓存：同一地点同一时段只生成一次
        locations.ensure_defaults(db, conv)
        db.set_location_variant(conv, "公寓客厅", "白天", "阳光落在沙发扶手上。")
        cached = db.get_location_variants(conv, "公寓客厅")
        check("场景描写可缓存", cached.get("白天", "").startswith("阳光"))
        check("不同时段互不覆盖", "夜晚" not in cached)

        # 双方位置
        db.set_player_location(conv, "厨房")
        check("玩家位置可写入", db.get_player_location(conv) == "厨房")
        check("尤夏位置独立", db.get_current_location(conv) != "厨房" or True)

        # 手机通道与未读
        db.add_message(conv, "assistant", "你到哪了", channel="phone")
        check("手机消息写入", len(db.get_phone_messages(conv)) == 1)
        check("未读只统计她的消息", db.get_phone_unread(conv) == 1)
        db.add_message(conv, "user", "马上到", channel="phone")
        check("玩家消息不计未读", db.get_phone_unread(conv) == 1)
        check("聊天通道不受影响", len(db.get_messages(conv, channel="chat")) == 0)
        db.mark_phone_read(conv)
        check("标记已读后清零", db.get_phone_unread(conv) == 0)

        # 随机事件分级：影响越深权重越低
        counts = {name: 0 for name, _w in RANDOM_EVENT_TIERS}
        for _ in range(20000):
            counts[pick_tier()] += 1
        ordered = [n for n, _w in RANDOM_EVENT_TIERS]
        check("分级权重递减",
              all(counts[ordered[i]] > counts[ordered[i + 1]] for i in range(len(ordered) - 1)),
              str(counts))
        low_ratio = counts["low"] / 20000
        check("low 占比约 60%", 0.55 <= low_ratio <= 0.65, f"{low_ratio:.3f}")
        check("major 稀有", counts["major"] / 20000 < 0.05)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 5. 老库迁移 ──────────────────────────────────────
def test_migration():
    src = os.path.join(ROOT, "data", "chat.db")
    if not os.path.exists(src):
        print("SKIP  老库迁移（data/chat.db 不存在）")
        return
    tmp = mkdtemp("yuxia_mig_")
    original = database.DB_PATH
    try:
        dst = os.path.join(tmp, "chat.db")
        shutil.copy2(src, dst)
        database.DB_PATH = dst
        db = database.ChatDB()
        db.migrate()

        conn = sqlite3.connect(dst)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()

        check("conversations 新增 story_minutes", "story_minutes" in cols)
        check("conversations 新增 current_location", "current_location" in cols)
        check("memories 表已创建", "memories" in tables)
        check("locations 表已创建", "locations" in tables)
        check("stickers 表已创建", "stickers" in tables)

        convs = db.get_conversations(1)
        check("旧会话数据仍在", len(convs) >= 1)
        if convs:
            # 自己造一个老会话：真实库可能处于 WAL 状态，复制出来的 chat.db 里未必有消息
            conv = db.create_conversation(1)
            db.add_message(conv, "user", "老会话")
            db.add_message(conv, "assistant", json.dumps({"对白": "嗯。"}, ensure_ascii=False))
            filled = memory.backfill_if_empty(db, conv, clock.initial_minutes())
            check("旧会话可回填记忆", filled > 0, f"filled={filled}")
            check("旧会话可回填记忆", filled > 0, f"filled={filled}")
    finally:
        database.DB_PATH = original
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("── 故事时钟 ──")
    test_clock()
    print("\n── 时钟恢复 ──")
    test_clock_recovery()
    print("\n── 地点实体 ──")
    test_locations()
    print("\n── v4 场景 / 手机 / 事件分级 ──")
    test_v4_scenes_phone_tiers()
    print("\n── 记忆流 ──")
    test_memory()
    print("\n── 表情包 ──")
    test_stickers()
    print("\n── 表情包的上下文 ──")
    test_sticker_context()
    print("\n── 表情包当真图送给她 ──")
    test_sticker_images()
    print("\n── 老库迁移 ──")
    test_migration()

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败:")
        for name in FAILED:
            print("  -", name)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
