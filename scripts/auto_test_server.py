"""
自主行动演示实例（端口 5051）
- 临时库，不碰 data/chat.db
- 把"闲置最小间隔"调小，方便观察她自己连续行动（是否行动仍由模型判断，无概率门槛）
用法：python scripts/auto_test_server.py
"""
import hashlib
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import core.database as database    # noqa: E402

TMP = os.path.join(ROOT, ".testtmp", "auto")
os.makedirs(TMP, exist_ok=True)
database.DB_PATH = os.path.join(TMP, "chat.db")

boot = database.ChatDB()
boot.migrate()
boot.sync_accounts({"e2e": hashlib.sha256("e2e123".encode()).hexdigest()})

from app import app                       # noqa: E402
import services.autonomy as autonomy      # noqa: E402

# 演示用：把最小间隔调小；是否行动由模型判断
autonomy.AUTONOMY_MIN_INTERVAL_SECONDS = 5

if __name__ == "__main__":
    from werkzeug.serving import make_server

    def serve(host):
        try:
            make_server(host, 5051, app, threaded=True).serve_forever()
        except Exception as e:
            print("listen failed:", e)

    for host in ("0.0.0.0", "::"):
        threading.Thread(target=serve, args=(host,), daemon=True).start()

    print("autonomy demo server: http://127.0.0.1:5051  db =", database.DB_PATH)
    print("autonomy: LLM 判断 + 最小间隔 5s（无概率门槛）")
    threading.Event().wait()
