"""
自主行动测试实例（端口 5051）
- 临时库，不碰 data/chat.db
- 把自主行动的概率门槛调到 100%，用来验证链路是否真的通
用法：python scripts/_auto_test_server.py
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

# 测试专用：把门槛调到必然触发
autonomy.AUTONOMY_ENABLED = True
autonomy.AUTONOMY_CHANCE = 1.0
autonomy.AUTONOMY_COOLDOWN_TURNS = 1
autonomy.IDLE_THRESHOLD_SECONDS = 0
autonomy.IDLE_CHANCE = 1.0

if __name__ == "__main__":
    from werkzeug.serving import make_server

    def serve(host):
        try:
            make_server(host, 5051, app, threaded=True).serve_forever()
        except Exception as e:
            print("listen failed:", e)

    for host in ("0.0.0.0", "::"):
        threading.Thread(target=serve, args=(host,), daemon=True).start()

    print("autonomy test server: http://127.0.0.1:5051  db =", database.DB_PATH)
    print("autonomy: chance=1.0 cooldown=1 idle_threshold=0 idle_chance=1.0")
    threading.Event().wait()
