"""
表情包系统 —— 导入即用，无需手工分类

流程：
1. 扫描 static/stickers/ 下所有图片（递归、任意子目录、任意文件名）
2. 为新图片打标，优先级：
   手动旁注(.txt) > 视觉模型(deepseek-flash 多模态) > 文件名/目录名关键词
   打标结果写入 SQLite 缓存，同图只花一次 token
3. 运行时：AI 输出情绪词 → 按「情绪标签 > 标签词面 > 语义相似度」匹配图片，
   都不匹配时随机兜底（和 QQ 机器人的做法一致）
"""
import base64
import hashlib
import json
import os
import random
import re
import urllib.parse
from functools import lru_cache

import core.textvec as tv
from config import (
    STICKER_DIR, STICKER_EXTENSIONS, STICKER_UPLOAD_SUBDIR,
    STICKER_UPLOAD_MAX_FILES, STICKER_UPLOAD_MAX_MB,
    VISION_ENABLED, VISION_BASE_URL, VISION_MODEL, VISION_PROMPT, VISION_DETAIL,
    VISION_MAX_PER_RUN, DEEPSEEK_THINKING, DEEPSEEK_REASONING_EFFORT,
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_EMOTION_NOISE_RE = re.compile(r"[\s【】\[\]「」\"'“”‘’。，、！!？?~～]+")

MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

# ── 情绪归并 ──────────────────────────────────────────
# 视觉模型给每张图起的情绪词是自由的，864 张图里有 171 个不同的词
# （无语 120 张、委屈 46 张……还有 86 个只出现一次）。
# 直接拿自由词做精确匹配命中率很低，所以两边都先归到同一个「情绪桶」再比。
# 桶名也是前端筛选用的一排按钮，所以数量要少、要一眼看懂。
EMOTION_BUCKETS = {
    "无语": ("无语", "无奈", "呆滞", "懵逼", "懵圈", "懵", "发懵", "发愣", "发呆",
             "恍惚", "敷衍", "无所谓", "淡定", "高冷", "复杂", "茫然", "迷惑",
             "生无可恋", "安详", "摆烂", "心累", "颓废", "沧桑", "苦笑", "尴尬",
             "恶心", "升天", "自嘲", "无聊"),
    "开心": ("开心", "大笑", "傻乐", "愉悦", "满足", "美滋滋", "幸福", "兴奋",
             "元气", "享受", "安逸", "惬意", "轻松", "悠闲", "温馨", "治愈",
             "暖心", "喜极而泣", "激动", "高兴", "热情", "释怀"),
    "温柔": ("温柔", "关心", "安慰", "鼓励", "欣慰", "感动", "心疼", "感谢",
             "友善", "友好", "体贴", "温暖", "宠溺"),
    "害羞": ("害羞", "娇羞", "心动", "痴迷", "花痴", "脸红", "羞", "喜欢", "喜爱"),
    "撒娇": ("撒娇", "卖萌", "乖巧", "俏皮", "调皮", "亲昵", "挑逗", "渴望",
             "恳求", "迫不及待", "娇嗔", "佯怒", "傲娇", "可爱", "黏人",
             "要抱抱", "呆萌"),
    "得意": ("得意", "神气", "自信", "崇拜", "膜拜", "赞许", "赞赏", "得意洋洋",
             "炫耀", "窃喜", "偷笑", "美"),
    "嫌弃": ("嫌弃", "鄙视", "不屑", "嘲讽", "阴阳怪气", "吐槽", "嘲笑", "戏谑",
             "调侃", "腹黑", "狡黠", "坏笑", "核善", "阴险", "黑色幽默", "搞怪",
             "搞笑", "好笑", "否定", "挑刺", "厌世", "看戏", "吃瓜"),
    "惊讶": ("震惊", "惊讶", "惊叹", "惊喜", "惊恐", "惊吓", "破防", "惊",
             "不敢相信", "傻眼", "诡异"),
    "不爽": ("生气", "愤怒", "气鼓鼓", "不悦", "抓狂", "发疯", "战意", "不服气",
             "记仇", "暴躁", "怒", "不满", "火大"),
    "委屈": ("委屈", "卑微", "无辜", "失落", "沮丧", "悲伤", "伤感", "忧郁",
             "强颜欢笑", "大哭", "爆哭", "哭", "可怜", "难受", "伤心"),
    "疑惑": ("疑惑", "困惑", "好奇", "怀疑", "狐疑", "迷惑不解", "思考",
             "暗中观察", "问号", "费解"),
    "期待": ("期待", "馋", "嘴馋", "饥饿", "沉迷", "饿", "想吃", "盼望",
             "羡慕", "催促"),
    "认真": ("认真", "严肃", "专注", "坚定", "平静", "正经", "硬核", "酷"),
    "疲惫": ("疲惫", "困倦", "累", "无精打采", "犯困", "睡", "打哈欠", "慵懒"),
    "崩溃": ("崩溃", "癫狂", "晕眩", "绝望", "哀嚎", "崩", "歇斯底里"),
    "紧张": ("害怕", "胆怯", "紧张", "慌张", "警惕", "不安", "心惊", "畏惧"),
}

_ALIAS_TO_BUCKET = {}
for _bucket, _aliases in EMOTION_BUCKETS.items():
    _ALIAS_TO_BUCKET[_bucket] = _bucket
    for _alias in _aliases:
        _ALIAS_TO_BUCKET.setdefault(_alias, _bucket)
# 长词优先：先匹配「生无可恋」再匹配「无」
_SORTED_ALIASES = sorted(_ALIAS_TO_BUCKET, key=len, reverse=True)
_BUCKET_ORDER = list(EMOTION_BUCKETS)


@lru_cache(maxsize=4096)
def canonical_emotion(word):
    """把一个自由情绪词归到情绪桶；认不出来返回空串"""
    w = _EMOTION_NOISE_RE.sub("", str(word or ""))
    if not w:
        return ""
    if w in EMOTION_BUCKETS:
        return w
    for alias in _SORTED_ALIASES:
        if alias in w:
            return _ALIAS_TO_BUCKET[alias]
    return ""


# ── 索引 ──────────────────────────────────────────────

def iter_images():
    """递归遍历表情包目录，产出 (相对路径, 绝对路径)"""
    if not os.path.isdir(STICKER_DIR):
        return
    for root, _dirs, files in os.walk(STICKER_DIR):
        for fname in sorted(files):
            if os.path.splitext(fname)[1].lower() not in STICKER_EXTENSIONS:
                continue
            abs_path = os.path.join(root, fname)
            rel = os.path.relpath(abs_path, STICKER_DIR).replace("\\", "/")
            yield rel, abs_path


def sidecar_tags(abs_path):
    """同目录同名 .txt 或 .md 里的第一行，作为人工旁注（可选）"""
    base = os.path.splitext(abs_path)[0]
    for ext in (".txt", ".md"):
        if os.path.exists(base + ext):
            try:
                with open(base + ext, "r", encoding="utf-8") as f:
                    line = f.readline().strip()
                    if line:
                        return line
            except Exception:
                pass
    return ""


def filename_tags(rel_path):
    """文件名/目录名里的弱标签（很多表情包本身就叫"开心.png"）"""
    name = os.path.splitext(os.path.basename(rel_path))[0]
    folder = os.path.dirname(rel_path)
    tokens = []
    for part in [folder, name]:
        if not part:
            continue
        for piece in re.split(r"[\\/_\-\s0-9]+", part):
            piece = piece.strip()
            if piece and not piece.isascii() or (piece and len(piece) <= 6):
                tokens.append(piece)
    # 去重保序
    seen, out = set(), []
    for t in tokens:
        if t and t not in seen and len(t) <= 12:
            seen.add(t)
            out.append(t)
    return out


def refresh(db):
    """扫描目录并与数据库对齐，返回统计信息"""
    seen = set()
    added = 0
    for rel, abs_path in iter_images():
        seen.add(rel)
        try:
            mtime = os.path.getmtime(abs_path)
            size = os.path.getsize(abs_path)
        except OSError:
            continue
        existing = db.get_sticker(rel)
        if existing and existing.get("mtime") == mtime and existing.get("size") == size:
            continue
        db.upsert_sticker(rel, os.path.basename(rel), mtime, size)
        added += 1

    removed = db.delete_stickers_not_in(list(seen)) if len(seen) > 0 else 0
    return {
        "scanned": len(seen),
        "indexed": added,
        "removed": removed,
        "total": db.count_stickers(),
        "tagged": db.count_stickers(tagged_only=True),
    }


def rebuild_embeddings(db):
    """为还没有向量的表情包补向量（embedding 不可用时直接跳过）"""
    if not tv.available():
        return 0
    updated = 0
    for row in db.get_stickers_without_embedding(limit=200):
        text = _sticker_text(row)
        vec = tv.embed(text)
        if vec:
            db.set_sticker_embedding(row["id"], tv.to_blob(vec))
            updated += 1
    return updated


# ── 打标 ──────────────────────────────────────────────

def _sticker_text(row):
    parts = [row.get("emotion") or "", row.get("tags") or "", row.get("caption") or ""]
    return " ".join(p for p in parts if p).strip()


def _parse_tag_json(raw):
    if not raw:
        return None
    text = _FENCE_RE.sub("", raw.strip())
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def vision_tag(abs_path, api_key=None, fast=False):
    """调用多模态模型给单张图片打标，失败返回 None

    fast=True 关掉思考、只留 400 token：打标就是"看图写三句话"，
    不需要推理链，开着思考只会慢三倍、贵三倍（批量补齐时用这个）。
    """
    import requests      # 延迟导入：App 冷启动时用不到，等真要打标再加载
    ext = os.path.splitext(abs_path)[1].lower()
    mime = MIME.get(ext, "image/png")
    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    b64 = base64.b64encode(raw).decode("utf-8")
    payload = {
        "model": VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}", "detail": VISION_DETAIL}},
            ],
        }],
        # 思考模式的思维链也会占用 max_tokens，所以额度要留够，否则 content 会是空串
        "max_tokens": 400 if fast else 2048,
        "thinking": {"type": "disabled" if fast else ("enabled" if DEEPSEEK_THINKING else "disabled")},
    }
    if fast:
        payload["temperature"] = 0.2
    elif DEEPSEEK_THINKING:
        payload["reasoning_effort"] = DEEPSEEK_REASONING_EFFORT
    else:
        payload["temperature"] = 0.2
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        from config import DEEPSEEK_API_KEY
        headers["Authorization"] = f"Bearer {DEEPSEEK_API_KEY}"

    try:
        resp = requests.post(VISION_BASE_URL, json=payload, headers=headers, timeout=90)
        if resp.status_code != 200:
            print(f"[表情包打标] HTTP {resp.status_code}: {resp.text[:160]}")
            return None
        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        print(f"[表情包打标] 失败: {e}")
        return None

    data = _parse_tag_json(content)
    if not data:
        return None
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,，、\s]+", tags) if t]
    return {
        "emotion": _EMOTION_NOISE_RE.sub("", str(data.get("emotion") or ""))[:12],
        "tags": ",".join(str(t) for t in tags if t)[:120],
        "caption": str(data.get("caption") or "")[:160],
    }


def auto_tag_all(db, api_key=None, limit=None):
    """为未打标的图片批量打标（每张只运行一次）

    打标走快速模式（关思考）：一次导入二十张图，开思考要等一分钟，
    关了之后十来秒就完事，标注质量没有可感差别。
    """
    limit = min(limit or VISION_MAX_PER_RUN, VISION_MAX_PER_RUN)
    pending = db.get_untagged_stickers(limit=limit)
    done = 0
    for row in pending:
        abs_path = os.path.join(STICKER_DIR, row["path"].replace("/", os.sep))
        note = sidecar_tags(abs_path)
        info = None
        if note:
            info = {"emotion": note[:12], "tags": note[:120], "caption": note[:160]}
            source = "manual"
        elif VISION_ENABLED:
            info = vision_tag(abs_path, api_key=api_key, fast=True)
            source = "vision"
        if info:
            db.set_sticker_tags(row["id"], info["emotion"], info["tags"], info["caption"], source)
            done += 1
        else:
            tokens = filename_tags(row["path"])
            if tokens:
                db.set_sticker_tags(row["id"], tokens[0][:12], ",".join(tokens)[:120],
                                    " / ".join(tokens)[:160], "filename")
                done += 1
            else:
                db.bump_sticker_attempts(row["id"])
    return done


# ── 匹配 ──────────────────────────────────────────────

def match(db, intent, top_k=3):
    """根据 AI 给出的情绪/场景词挑一张表情包"""
    rows = db.get_all_stickers()
    if not rows:
        return []

    intent = _EMOTION_NOISE_RE.sub("", str(intent or ""))
    if not intent:
        return _random_pick(rows, top_k)

    intent_bucket = canonical_emotion(intent)
    intent_vec = tv.embed(intent)
    scored = []
    for row in rows:
        emotion = (row.get("emotion") or "").strip()
        tags = [t for t in re.split(r"[,，、\s]+", (row.get("tags") or "")) if t]
        caption = row.get("caption") or ""

        score = 0.0
        if emotion and emotion == intent:
            score = 1.0
        elif intent_bucket and canonical_emotion(emotion) == intent_bucket:
            # 「委屈」和「失落」是同一个情绪桶：自由词对不上，桶一定能对上
            score = 0.9
        elif intent in tags or emotion in intent or intent in emotion:
            score = 0.85
        else:
            lexical = max(
                tv.lexical_similarity(intent, " ".join(tags)),
                tv.lexical_similarity(intent, caption),
            )
            score = lexical * 0.7
            if intent_vec and row.get("embedding"):
                score = max(score, tv.cosine(intent_vec, tv.from_blob(row["embedding"])) * 0.95)

        if score > 0:
            scored.append((score, row))

    if not scored:
        return _random_pick(rows, top_k)

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "path": r["path"],
        "url": url_for(r["path"]),
        "emotion": r.get("emotion") or "",
        "bucket": canonical_emotion(r.get("emotion")),
        "tags": r.get("tags") or "",
        "caption": r.get("caption") or "",
        "score": round(s, 3),
    } for s, r in scored[:top_k]]


def _random_pick(rows, top_k):
    picked = random.sample(rows, min(top_k, len(rows)))
    return [{
        "path": r["path"],
        "url": url_for(r["path"]),
        "emotion": r.get("emotion") or "",
        "bucket": canonical_emotion(r.get("emotion")),
        "tags": r.get("tags") or "",
        "caption": r.get("caption") or "",
        "score": 0.0,
    } for r in picked]


def url_for(rel_path):
    return "/static/stickers/" + urllib.parse.quote(rel_path)


def list_emotions(db):
    """给前端/提示词用的情绪清单：按「情绪桶」给，按张数从多到少

    以前这里吐的是 171 个自由情绪词，前端一排筛选按钮根本排不下，
    也没法用来筛（点「疲惫」只出 8 张，点「无语」出 120 张）。
    """
    counts = {}
    for row in db.get_all_stickers():
        bucket = canonical_emotion(row.get("emotion") or "")
        if bucket:
            counts[bucket] = counts.get(bucket, 0) + 1
    return sorted(counts, key=lambda b: (-counts[b], _BUCKET_ORDER.index(b)))


# ── 手机相册多选导入 ──────────────────────────────────

def _detect_image_ext(data):
    """按文件头判断真实格式（不信客户端给的扩展名）"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_uploads(db, batch, api_key=None, max_files=None, max_mb=None):
    """把一批上传的图片落盘、入库、顺手打标。

    batch: [(filename, bytes)]
    文件名用内容 md5，同图重复上传自然去重；
    """
    folder = os.path.join(STICKER_DIR, STICKER_UPLOAD_SUBDIR)
    os.makedirs(folder, exist_ok=True)
    limit_bytes = int((max_mb or STICKER_UPLOAD_MAX_MB) * 1024 * 1024)
    max_files = max_files or STICKER_UPLOAD_MAX_FILES

    uploaded, duplicates, rejected = [], 0, []
    for name, data in batch[:max_files]:
        ext = _detect_image_ext(data or b"")
        if not ext:
            rejected.append({"name": name, "reason": "不是图片"})
            continue
        if len(data) > limit_bytes:
            rejected.append({"name": name, "reason": "文件太大"})
            continue
        rel = f"{STICKER_UPLOAD_SUBDIR}/{hashlib.md5(data).hexdigest()}{ext}"
        abs_path = os.path.join(STICKER_DIR, rel.replace("/", os.sep))
        if os.path.exists(abs_path):
            duplicates += 1
            continue
        try:
            with open(abs_path, "wb") as f:
                f.write(data)
        except OSError as e:
            rejected.append({"name": name, "reason": f"写入失败: {e}"})
            continue
        uploaded.append(rel)

    if batch[max_files:]:
        rejected.extend({"name": n, "reason": "超出单次上限"} for n, _ in batch[max_files:])

    refresh(db)
    tagged = auto_tag_all(db, api_key=api_key, limit=max(len(uploaded), 1)) if uploaded else 0
    return {
        "uploaded": uploaded,
        "uploaded_count": len(uploaded),
        "duplicates": duplicates,
        "rejected": rejected,
        "tagged": tagged,
        "pending": len(db.get_untagged_stickers(limit=1000)),
        "total": db.count_stickers(),
    }


def list_for_picker(db, limit=300, offset=0, emotion=None):
    """选择面板用的列表：新的在前，带可直接放进 <img> 的 url

    emotion 可以是某个自由情绪词，也可以是情绪桶名（前端按钮就是桶名）。
    """
    rows = db.get_all_stickers()
    if emotion:
        bucket = canonical_emotion(emotion)
        if bucket:
            rows = [r for r in rows if canonical_emotion(r.get("emotion") or "") == bucket]
        else:
            rows = [r for r in rows if (r.get("emotion") or "").strip() == emotion]
    rows = sorted(rows, key=lambda r: r.get("id") or 0, reverse=True)
    items = [{
        "path": r["path"],
        "url": url_for(r["path"]),
        "emotion": r.get("emotion") or "",
        "bucket": canonical_emotion(r.get("emotion")),
        "tags": r.get("tags") or "",
        "caption": r.get("caption") or "",
    } for r in rows[offset:offset + limit]]
    return {"total": len(rows), "items": items, "emotions": list_emotions(db)}


def describe(db, rel_path):
    """一句话说明这张表情包是什么（喂给她的上下文用）"""
    row = db.get_sticker(rel_path) if rel_path else None
    if not row:
        return ""
    for key in ("caption", "emotion", "tags"):
        value = (row.get(key) or "").strip()
        if value:
            return value
    return "一张表情包"
