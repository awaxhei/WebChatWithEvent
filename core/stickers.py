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
import json
import os
import random
import re
import urllib.parse

import requests

import core.textvec as tv
from config import (
    STICKER_DIR, STICKER_EXTENSIONS,
    VISION_ENABLED, VISION_BASE_URL, VISION_MODEL, VISION_PROMPT, VISION_DETAIL,
    VISION_MAX_PER_RUN, DEEPSEEK_THINKING, DEEPSEEK_REASONING_EFFORT,
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_EMOTION_NOISE_RE = re.compile(r"[\s【】\[\]「」\"'“”‘’。，、！!？?~～]+")

MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


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


def vision_tag(abs_path, api_key=None):
    """调用多模态模型给单张图片打标，失败返回 None"""
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
        "max_tokens": 2048,
        "thinking": {"type": "enabled" if DEEPSEEK_THINKING else "disabled"},
    }
    if DEEPSEEK_THINKING:
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
    """为未打标的图片批量打标（每张只运行一次）"""
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
            info = vision_tag(abs_path, api_key=api_key)
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

    intent_vec = tv.embed(intent)
    scored = []
    for row in rows:
        emotion = (row.get("emotion") or "").strip()
        tags = [t for t in re.split(r"[,，、\s]+", (row.get("tags") or "")) if t]
        caption = row.get("caption") or ""

        score = 0.0
        if emotion and emotion == intent:
            score = 1.0
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
        "tags": r.get("tags") or "",
        "caption": r.get("caption") or "",
        "score": 0.0,
    } for r in picked]


def url_for(rel_path):
    return "/static/stickers/" + urllib.parse.quote(rel_path)


def list_emotions(db):
    """给前端/prompt 用的情绪清单"""
    names = db.get_sticker_emotions()
    return [n for n in names if n]
