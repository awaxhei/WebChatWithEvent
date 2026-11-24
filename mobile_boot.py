"""
内嵌后端（原生 App 用）

手机端的 App 不需要电脑：同一套 Flask 逻辑被 Python 解释器直接跑在手机里
（Chaquopy 打包 CPython + 这些 .py 文件）。App 启动时调用 start()：

1. 把 ROOT 指到应用私有目录，SQLite / 表情包 / 提示词都写在那儿
2. 建一个本机账号并签一个 token —— 单机模式不需要登录界面
3. 在 127.0.0.1 上起 Flask（只有手机里的进程能访问）

电脑上跑 `python app.py` 的老方式完全不受影响。
"""
import hashlib
import os
import threading

_server = {"started": False, "port": 0}


def prepare(root, prompt_text="", memes_text=""):
    """准备工作目录：环境变量必须在 import config 之前设好"""
    root = os.path.abspath(root)
    os.environ["YUXIA_ROOT"] = root
    os.environ["YUXIA_MOBILE"] = "1"
    os.environ["YUXIA_DB_PATH"] = os.path.join(root, "data", "chat.db")
    for sub in ("data",
                os.path.join("static", "stickers"),
                os.path.join("static", "stickers", "upload")):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    # 人设提示词由 App 从 assets 里带过来，第一次启动时落盘
    prompt_path = os.path.join(root, "yuxia_prompt.txt")
    if prompt_text and not os.path.exists(prompt_path):
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt_text)
    # 梗库同理：App 从 assets 带过来，落盘后由 config 读取
    memes_path = os.path.join(root, "yuxia_memes.txt")
    if memes_text and not os.path.exists(memes_path):
        with open(memes_path, "w", encoding="utf-8") as f:
            f.write(memes_text)
    return root


def start(root, port=54123, prompt_text="", memes_text=""):
    """起内嵌服务并返回本机 token（重复调用只起一次）"""
    root = prepare(root, prompt_text, memes_text)
    import app as webapp                     # 必须在环境变量就绪后导入
    token = _local_token(webapp.db)
    _serve_once(webapp.app, root, int(port))
    return token


def _serve_once(flask_app, root, port):
    if _server["started"]:
        return _server["port"]
    from werkzeug.serving import make_server
    flask_app.static_folder = os.path.join(root, "static")
    flask_app.static_url_path = "/static"
    srv = make_server("127.0.0.1", port, flask_app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _server["started"] = True
    _server["port"] = port
    return port


def _local_token(db):
    """单机模式的本机账号：App 自己用，不需要用户名密码"""
    from core.auth import generate_token
    user = db.get_user("me")
    if not user:
        db.sync_accounts({"me": hashlib.sha256(b"yuxia-mobile-local").hexdigest()})
        user = db.get_user("me")
    return generate_token(user["id"], remember=True)


def api_key_path(root):
    return os.path.join(root, "deepseek_key.txt")
