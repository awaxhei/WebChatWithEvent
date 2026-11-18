"""
端到端验证用临时服务（不碰真实 data/chat.db）
- 使用 .testtmp/e2e/chat.db
- 端口 5050
- 预置账号 e2e / e2e123
用法：python scripts/_e2e_server.py
"""
import hashlib
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = os.path.join(ROOT, ".testtmp", "e2e")
os.makedirs(TMP, exist_ok=True)

import core.database as database    # noqa: E402

database.DB_PATH = os.path.join(TMP, "chat.db")

# 在 app 导入前先把测试账号写进临时库
boot = database.ChatDB()
boot.migrate()
boot.sync_accounts({"e2e": hashlib.sha256("e2e123".encode()).hexdigest()})

from app import app                 # noqa: E402

if __name__ == "__main__":
    from werkzeug.serving import make_server

    def serve(host):
        try:
            make_server(host, 5050, app, threaded=True).serve_forever()
        except Exception as e:
            print(f"listen {host} failed: {e}")

    for host in ("0.0.0.0", "::"):
        threading.Thread(target=serve, args=(host,), daemon=True).start()

    print("e2e server on http://127.0.0.1:5050 (db=%s)" % database.DB_PATH)
    threading.Event().wait()
