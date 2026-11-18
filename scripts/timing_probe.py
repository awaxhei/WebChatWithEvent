"""首轮延迟分段测量（设置 YUXIA_TIMING=1，打印各阶段耗时）"""
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

os.environ["YUXIA_TIMING"] = "1"

import core.database as database    # noqa: E402

tmp = os.path.join(ROOT, ".testtmp", "timing_" + uuid.uuid4().hex[:6])
os.makedirs(tmp, exist_ok=True)
database.DB_PATH = os.path.join(tmp, "chat.db")

from app import app                 # noqa: E402
from core.auth import generate_token  # noqa: E402


def turn(c, conv, msg):
    t0 = time.time()
    r = c.post("/api/chat", json={"conversation_id": conv, "message": msg})
    chunks = 0
    first = None
    body = ""
    for raw in r.response:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        body += line
        if '"type": "chunk"' in line:
            chunks += 1
            if first is None:
                first = time.time() - t0
    total = time.time() - t0
    print(f"  >>> 客户端视角: 首字 {first:.2f}s / 完成 {total:.2f}s / chunk 数 {chunks}")
    return body


def main():
    app.config["TESTING"] = True
    c = app.test_client()
    try:
        c.set_cookie("auth_token", generate_token(1))
    except TypeError:
        c.set_cookie("localhost", "auth_token", generate_token(1))

    print("=== 第 1 轮（全新会话）===")
    body = turn(c, "", "我回来了。")
    import re
    m = re.search(r'"type": "conv_id", "id": "([^"]+)"', body)
    conv = m.group(1) if m else ""
    print("  会话:", conv)

    print("=== 第 2 轮（同会话）===")
    turn(c, conv, "今天好累。")

    print("=== 场景接口（冷启动，应返回 pending 且不阻塞）===")
    t0 = time.time()
    scene = c.get(f"/api/scene/{conv}").get_json() or {}
    print(f"  耗时 {time.time() - t0:.2f}s  pending={scene.get('pending')} "
          f"描写长度={len(scene.get('player_description') or '')}")

    print("=== 等后台生成后再取一次 ===")
    time.sleep(8)
    t0 = time.time()
    scene2 = c.get(f"/api/scene/{conv}").get_json() or {}
    print(f"  耗时 {time.time() - t0:.2f}s  pending={scene2.get('pending')} "
          f"描写={ (scene2.get('player_description') or '')[:40] }")

    print("临时库:", database.DB_PATH)


if __name__ == "__main__":
    main()
