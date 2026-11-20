"""给表情包打标的批处理脚本（本地跑，不动仓库里的数据）

    python scripts/tag_stickers.py            # 把所有没打标的补齐
    python scripts/tag_stickers.py --limit 50 # 只跑 50 张（试水）
    python scripts/tag_stickers.py --stats    # 只看进度

打标走视觉模型（deepseek-flash 多模态），关掉思考、限 400 token：
打标是"看图说三句话"，不需要推理，开思考只会慢三倍、贵三倍。
并发默认 4（--workers 调），失败会记 attempts，下次重试。
"""
import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database  # noqa: E402
import core.stickers as stickers  # noqa: E402
from config import STICKER_DIR, VISION_ENABLED  # noqa: E402


def stats(db):
    total = db.count_stickers()
    tagged = db.count_stickers(tagged_only=True)
    pending = len(db.get_untagged_stickers(limit=100000))
    return total, tagged, pending


# 文件名就是一串哈希的那些图：从文件名凑出来的标签一定是垃圾（"EF,F,D,A"），
# 清掉重新让视觉模型看一遍。
_HEXNAME_RE = re.compile(r"^[0-9A-Fa-f]{16,}$")


def junk_rows(db):
    out = []
    for r in db.get_all_stickers():
        if (r.get("tagged_by") or "") != "filename":
            continue
        base = os.path.splitext(os.path.basename(r["path"]))[0]
        if _HEXNAME_RE.match(base or ""):
            out.append(r)
    return out


def one(db, row, api_key=None):
    """打一张：人工旁注 > 视觉模型 > 文件名"""
    abs_path = os.path.join(STICKER_DIR, row["path"].replace("/", os.sep))
    note = stickers.sidecar_tags(abs_path)
    if note:
        db.set_sticker_tags(row["id"], note[:12], note[:120], note[:160], "manual")
        return "manual"
    if VISION_ENABLED:
        info = stickers.vision_tag(abs_path, api_key=api_key, fast=True)
        if info:
            db.set_sticker_tags(row["id"], info["emotion"], info["tags"],
                                info["caption"], "vision")
            return "vision"
    tokens = stickers.filename_tags(row["path"])
    if tokens:
        db.set_sticker_tags(row["id"], tokens[0][:12], ",".join(tokens)[:120],
                            " / ".join(tokens)[:160], "filename")
        return "filename"
    db.bump_sticker_attempts(row["id"])
    return "failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多打多少张（0 = 全部）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--retag-junk", action="store_true",
                    help="把从哈希文件名凑出来的垃圾标签清掉、重新用视觉模型打")
    args = ap.parse_args()

    db = database.ChatDB()
    stickers.refresh(db)
    if args.retag_junk:
        junk = junk_rows(db)
        for r in junk:
            db.reset_sticker_tags(r["id"])
        print(f"清理垃圾标签 {len(junk)} 条")
    total, tagged, pending = stats(db)
    print(f"表情包 {total} 张，已打标 {tagged}，待打标 {pending}")
    if args.stats or pending == 0:
        report_unmapped(db)
        return 0

    rows = db.get_untagged_stickers(limit=args.limit or pending)
    print(f"本次处理 {len(rows)} 张，并发 {args.workers}，视觉模型 {VISION_ENABLED}")
    started = time.time()
    counts = {"manual": 0, "vision": 0, "filename": 0, "failed": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(one, db, row): row for row in rows}
        for fut in as_completed(futures):
            try:
                src = fut.result()
            except Exception as e:
                src = "failed"
                print(f"  异常：{e}")
            counts[src] = counts.get(src, 0) + 1
            done += 1
            if done % 10 == 0 or done == len(rows):
                cost = time.time() - started
                speed = done / cost if cost else 0
                left = (len(rows) - done) / speed if speed else 0
                print(f"  {done}/{len(rows)}  视觉 {counts['vision']} 失败 {counts['failed']}  "
                      f"{speed:.2f} 张/秒  剩约 {left / 60:.1f} 分钟")

    total, tagged, pending = stats(db)
    print(f"\n完成：视觉 {counts['vision']}，人工旁注 {counts['manual']}，"
          f"文件名 {counts['filename']}，失败 {counts['failed']}")
    print(f"现在 {total} 张里已打标 {tagged}，还剩 {pending}")
    report_unmapped(db)
    return 0


def report_unmapped(db):
    """新导入的图会带新的情绪词：认不出来就说一声，好补进 EMOTION_BUCKETS

    认不出来的词也能被词面相似度兜住，只是配对会变差，所以这里只提醒、不报错。
    """
    unmapped = {}
    for row in db.get_all_stickers():
        emotion = (row.get("emotion") or "").strip()
        if emotion and not stickers.canonical_emotion(emotion):
            unmapped[emotion] = unmapped.get(emotion, 0) + 1
    if unmapped:
        items = sorted(unmapped.items(), key=lambda kv: -kv[1])
        print("这些情绪词还不在任何情绪桶里（建议补进 EMOTION_BUCKETS）：")
        print("  " + "、".join(f"{k}×{v}" for k, v in items))
    else:
        print(f"情绪词全部归进了 {len(stickers.EMOTION_BUCKETS)} 个桶")


if __name__ == "__main__":
    sys.exit(main())
