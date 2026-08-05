"""
Android 启动器 — Chaquopy 入口
MainActivity 通过 com.chaquo.python 调用 start_flask()
"""
import os
import sys
import threading


def start_flask(data_dir):
    """由 Android MainActivity 调用的入口函数"""
    # 设置数据目录（SQLite / ChromaDB 存储路径）
    os.environ["YUXIA_DATA_DIR"] = os.path.join(data_dir, "data")
    os.makedirs(os.environ["YUXIA_DATA_DIR"], exist_ok=True)

    # 设置静态文件目录
    static_dir = os.path.join(data_dir, "static")
    template_dir = os.path.join(data_dir, "templates")

    # 将项目根目录加到 sys.path（让 import config 等生效）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    # 在后台线程启动 Flask（避免阻塞 Android UI）
    def run():
        from app import app
        app.static_folder = os.path.exists(static_dir) and static_dir or app.static_folder
        app.template_folder = os.path.exists(template_dir) and template_dir or app.template_folder
        app.run(host="127.0.0.1", port=5000, debug=False, threaded=True, use_reloader=False)

    t = threading.Thread(target=run, daemon=True)
    t.start()