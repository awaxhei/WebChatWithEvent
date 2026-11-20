"""
AI 情感助手 - Flask 后端 (三 AI 架构 + 账号系统)
入口文件：创建应用、注册蓝图、启动服务
"""
import os, socket, subprocess, sys, threading
from flask import Flask, jsonify, render_template, request, send_file

from config import (
    PRESET_ACCOUNTS, DEEPSEEK_MODEL, CHARACTER_NAME, DEFAULT_LOCATION,
    DEFAULT_STORY_TIME, DEFAULT_STORY_SUMMARY, DEEPSEEK_API_KEY, IDLE_POLL_SECONDS,
    MOOD_FACES, MOOD_DEFAULT, AUTONOMY_SPEED_DEFAULT, AUTONOMY_SPEED_MIN, AUTONOMY_SPEED_MAX,
)
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient

# 创建 Flask 应用
app = Flask(__name__, static_folder="static", template_folder="templates")
# 改模板不用重启（本地个人项目，省得每次改 HTML 都要 kill 进程；
# 不开启时 Jinja2 会缓存已编译模板，导致页面停在旧版本）
app.jinja_env.auto_reload = True
app.config["TEMPLATES_AUTO_RELOAD"] = True

# 初始化全局依赖
db = ChatDB()
db.migrate()
db.sync_accounts(PRESET_ACCOUNTS)
deepseek = DeepSeekClient()

# 注册蓝图
from routes.auth_routes import auth_bp
from routes.chat_routes import chat_bp
from routes.event_routes import event_bp
from routes.history_routes import history_bp

# 注入模块级依赖（直接设置各路由模块的模块变量）
import routes.auth_routes as auth_mod
import routes.chat_routes as chat_mod
import routes.event_routes as event_mod
import routes.history_routes as history_mod

auth_mod.db = db
chat_mod.db = db
chat_mod.deepseek = deepseek
event_mod.db = db
event_mod.deepseek = deepseek
history_mod.db = db

app.register_blueprint(auth_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(event_bp)
app.register_blueprint(history_bp)


@app.route("/sw.js")
def service_worker():
    """把 Service Worker 放到根路径，才能以 scope=/ 控制整个站点（含首页）。

    /static/sw.js 的默认作用域只有 /static/，页面本身不会被控制，
    既是资源缓存失效的原因，也不满足 PWA 可安装条件。
    """
    resp = app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/app.apk")
def android_apk():
    """手机直接下载原生 App。

    APK 由 scripts/build_apk.ps1 构建并拷到 static/download/yuxia.apk；
    这里显式给 Android 的 MIME，浏览器才会直接进安装流程而不是当文件下载。
    """
    path = os.path.join(app.static_folder, "download", "yuxia.apk")
    if not os.path.exists(path):
        return jsonify({"error": "还没有构建 APK：先在电脑上运行 scripts\\build_apk.ps1"}), 404
    resp = send_file(
        path,
        mimetype="application/vnd.android.package-archive",
        as_attachment=True,
        download_name="yuxia.apk",
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/ping")
def ping():
    """健康检查：不碰数据库、不调模型，只证明"后端进程还活着"。

    前端用它区分两种情况：进程没了（请求直接失败）和 未登录（正常返回）。
    如果没有这一步，后端一退出，页面还能靠 Service Worker 缓存照常显示，
    点登录只会得到一句笼统的"网络错误"，看不出真正原因。
    """
    out = jsonify({"ok": True, "app": "yuxia", "host": request.host})
    out.headers["Cache-Control"] = "no-store"
    return out


@app.route("/api/config", methods=["GET"])
def get_client_config():
    return {
        "character_name": CHARACTER_NAME,
        "default_location": DEFAULT_LOCATION,
        "default_story_time": DEFAULT_STORY_TIME,
        "default_story_summary": DEFAULT_STORY_SUMMARY,
        # 仅暴露"是否存在服务端 Key"这一布尔值，绝不返回 Key 本身
        "has_server_key": bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY.strip()),
        "idle_poll_seconds": IDLE_POLL_SECONDS,
        # 头像用的心情表情表 / 行动速度范围（网页和 App 都按这个画）
        "mood_faces": MOOD_FACES,
        "mood_default": MOOD_DEFAULT,
        "speed_default": AUTONOMY_SPEED_DEFAULT,
        "speed_min": AUTONOMY_SPEED_MIN,
        "speed_max": AUTONOMY_SPEED_MAX,
    }


@app.route("/api/stickers")
def get_stickers():
    """表情包状态（索引来自数据库，导入即用，无需手工分类）"""
    from core import stickers as sticker_lib
    emotions = [
        {"name": name, "count": 0}
        for name in db.get_sticker_emotions()
    ]
    return {
        "emotions": emotions,
        "total": db.count_stickers(),
        "tagged": db.count_stickers(tagged_only=True),
    }


# 内嵌进 APK 时（Python 跑在手机里）标记一下：启动阶段不做会花钱的事
_MOBILE = bool(os.environ.get("YUXIA_MOBILE"))


def _prepare_stickers():
    """启动后异步：刷新索引 → 给新图片打标 → 补向量（不阻塞服务启动）"""
    try:
        from core import stickers as sticker_lib
        stats = sticker_lib.refresh(db)
        if stats["indexed"] or stats["removed"]:
            print(f"  [表情包] 索引更新: {stats}")
        # 手机上的图都是 App 传上来的，传的时候带着 Key 就打标了，启动时不必再扫
        if not _MOBILE:
            tagged = sticker_lib.auto_tag_all(db)
            if tagged:
                print(f"  [表情包] 本机新打标 {tagged} 张")
            sticker_lib.rebuild_embeddings(db)
    except Exception as e:
        print(f"  [表情包] 索引失败: {e}")


threading.Thread(target=_prepare_stickers, daemon=True).start()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台中文输出
    PORT = int(os.environ.get("PORT", "5000"))
    ipv4 = ipv6 = ""
    try:
        for addr in socket.getaddrinfo(socket.gethostname(), None):
            ip = addr[4][0]
            if "." in ip and not ip.startswith("127.") and not ipv4:
                ipv4 = ip
    except Exception:
        pass
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetIPAddress -AddressFamily IPv6 -AddressState Preferred -SuffixOrigin Dhcp,Manual,Link | "
                 "Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.IPAddress -notlike 'fe80:*' } | "
                 "Select-Object -ExpandProperty IPAddress"],
                capture_output=True, text=True, timeout=10)
            ip_list = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            if ip_list:
                global_ips = [ip for ip in ip_list if ip[0] in ('2', '3')]
                ipv6 = global_ips[0] if global_ips else ip_list[0]
        except Exception:
            pass
    print("=" * 50)
    print("  尤夏 (三AI - 账号系统) 服务启动中...")
    print(f"  模型: {DEEPSEEK_MODEL}")
    print(f"  本地访问: http://127.0.0.1:{PORT}")
    if ipv4:
        print(f"  IPv4 访问: http://{ipv4}:{PORT}")
    if ipv6:
        print(f"  IPv6 访问: http://[{ipv6}]:{PORT}")
    print("=" * 50)

    # 同时监听 IPv4 与 IPv6：Windows 默认 IPV6_V6ONLY=1，可同时绑定同一端口；
    # 若平台不允许双绑定（如 Linux），IPv6 监听失败不影响 IPv4 可用。
    from werkzeug.serving import make_server

    def _serve(host):
        try:
            srv = make_server(host, PORT, app, threaded=True)
            srv.serve_forever()
        except Exception as e:
            print(f"  [监听 {host}:{PORT} 失败] {e}")

    threads = []
    for _host in ("0.0.0.0", "::"):
        _t = threading.Thread(target=_serve, args=(_host,), daemon=True)
        _t.start()
        threads.append(_t)
    try:
        while any(t.is_alive() for t in threads):
            threads[0].join(timeout=1)
    except KeyboardInterrupt:
        print("\n服务已停止")