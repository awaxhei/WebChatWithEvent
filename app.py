"""
AI 情感助手 - Flask 后端 (三 AI 架构 + 账号系统)
入口文件：创建应用、注册蓝图、启动服务
"""
import os, socket, subprocess
from flask import Flask, render_template

from config import PRESET_ACCOUNTS, DEEPSEEK_MODEL, CHARACTER_NAME, DEFAULT_LOCATION, DEFAULT_STORY_TIME, DEFAULT_STORY_SUMMARY
from core.database import ChatDB
from core.deepseek_client import DeepSeekClient

# 创建 Flask 应用
app = Flask(__name__, static_folder="static", template_folder="templates")

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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def get_client_config():
    return {
        "character_name": CHARACTER_NAME,
        "default_location": DEFAULT_LOCATION,
        "default_story_time": DEFAULT_STORY_TIME,
        "default_story_summary": DEFAULT_STORY_SUMMARY,
    }


@app.route("/api/stickers")
def get_stickers():
    """扫描 static/stickers/ 文件夹，返回可用情绪列表（动态，零硬编码）"""
    sticker_dir = os.path.join(app.static_folder, "stickers")
    folders = []
    if os.path.isdir(sticker_dir):
        for name in sorted(os.listdir(sticker_dir)):
            path = os.path.join(sticker_dir, name)
            if os.path.isdir(path):
                images = [f for f in os.listdir(path)
                          if f.lower().endswith(('.png', '.jpg', '.gif', '.webp'))]
                folders.append({"name": name, "count": len(images)})
    return {"emotions": folders}


if __name__ == "__main__":
    ipv4 = ipv6 = ""
    try:
        for addr in socket.getaddrinfo(socket.gethostname(), None):
            ip = addr[4][0]
            if "." in ip and not ip.startswith("127.") and not ipv4:
                ipv4 = ip
    except Exception:
        pass
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
    print("  AI 情感助手 (三AI - 账号系统) 服务启动中...")
    print(f"  模型: {DEEPSEEK_MODEL}")
    print(f"  API: DeepSeek")
    print(f"  本地访问: http://127.0.0.1:5000")
    if ipv4:
        print(f"  IPv4 访问: http://{ipv4}:5000")
    if ipv6:
        print(f"  IPv6 访问: http://[{ipv6}]:5000")
    print("=" * 50)
    app.run(host="::", port=5000, debug=False, threaded=True)