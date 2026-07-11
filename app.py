"""
AI 情感助手 - Flask 后端 (双 AI 架构)
对接 DeepSeek API (OpenAI 兼容)
"""

import json
import re
import sqlite3
import os
import uuid
import hashlib
from datetime import datetime, timedelta
from threading import Lock
from functools import wraps

import jwt
import requests
from flask import Flask, render_template, request, Response, jsonify
from config import (CHAT_PROMPT, ATMOSPHERE_PROMPT, CHARACTER_NAME,
                     DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
                     LOGIN_PASSWORD_HASH, JWT_SECRET_KEY, JWT_EXPIRE_DAYS)

app = Flask(__name__, static_folder="static", template_folder="templates")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chat.db")
DEEPSEEK_BASE = "https://api.deepseek.com/v1/chat/completions"

# 安全截断：防止模型续写用户发言
_TRUNCATE_RE = re.compile(r'(?:用户|User|Assistant)[：:]', re.IGNORECASE)

# 提取 AI B 输出的【地点】标签
_LOCATION_RE = re.compile(r'【地点】\s*(.+)')


# ============================================================
# 数据库管理类
# ============================================================
class ChatDB:
    """SQLite 数据库操作：会话、消息、环境叙述"""

    _lock = Lock()

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT PRIMARY KEY,
                    title       TEXT DEFAULT '新对话',
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    updated_at  TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content         TEXT NOT NULL,
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS atmosphere (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
            """)

    # ----- 会话操作 -----
    def create_conversation(self):
        conv_id = uuid.uuid4().hex[:12]
        with self._get_conn() as conn:
            conn.execute("INSERT INTO conversations(id) VALUES(?)", (conv_id,))
        return conv_id

    def get_conversations(self):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    def update_conversation_title(self, conv_id, title):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=datetime('now','localtime') WHERE id=?",
                (title, conv_id),
            )

    def touch_conversation(self, conv_id):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE conversations SET updated_at=datetime('now','localtime') WHERE id=?",
                (conv_id,),
            )

    # ----- 消息操作 -----
    def add_message(self, conv_id, role, content):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO messages(conversation_id, role, content) VALUES(?,?,?)",
                (conv_id, role, content),
            )

    def get_messages(self, conv_id):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (conv_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ----- 环境叙述操作 -----
    def add_atmosphere(self, conv_id, content):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO atmosphere(conversation_id, content) VALUES(?,?)",
                (conv_id, content),
            )

    def get_atmosphere(self, conv_id, limit=5):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT content FROM atmosphere WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (conv_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]


# ============================================================
# DeepSeek API 客户端类
# ============================================================
class DeepSeekClient:
    """封装 DeepSeek API 调用 (OpenAI 兼容)"""

    def __init__(self, base_url=DEEPSEEK_BASE, model=DEEPSEEK_MODEL, api_key=DEEPSEEK_API_KEY):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _build_messages(self, system_prompt, messages):
        """构建 OpenAI 兼容 messages 列表"""
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        for msg in messages:
            role = "user" if msg["role"] == "user" else "assistant"
            msgs.append({"role": role, "content": msg["content"]})
        return msgs

    def chat_stream(self, messages, system_prompt=""):
        """流式对话，即时逐块输出（非缓冲）"""
        msgs = self._build_messages(system_prompt, messages)
        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": True,
            "temperature": 0.7,
            "top_p": 0.9,
        }
        if not self.api_key:
            yield "请在 config.py 中设置 DEEPSEEK_API_KEY"
            return
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, stream=True, timeout=120)
            resp.raise_for_status()
            first_content = False  # 标记是否已收到首个有效内容
            prefix_buf = ""        # 缓冲区用于识别角色名前缀
            prefixes = (f"{CHARACTER_NAME}：", f"{CHARACTER_NAME}:", "青梅：")
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if not content:
                            continue
                        if not first_content:
                            # 首个有效内容：缓冲以识别并剥离角色名前缀
                            prefix_buf += content
                            stripped = prefix_buf
                            for p in prefixes:
                                if prefix_buf.startswith(p):
                                    stripped = prefix_buf[len(p):].strip()
                                    break
                            # 只有当缓冲区足够长（超过最长前缀）或无法匹配前缀时才输出
                            max_prefix_len = max(len(p) for p in prefixes)
                            if len(prefix_buf) >= max_prefix_len or stripped != prefix_buf:
                                first_content = True
                                if stripped:
                                    yield stripped
                            else:
                                # 缓冲内容尚不足以判断是否匹配前缀，继续累积
                                pass
                        else:
                            yield content
                    except json.JSONDecodeError:
                        continue
            # 如果始终未触发 first_content（整个响应仅匹配到了前缀，无实际内容），则输出前缀后的部分
            if not first_content and prefix_buf:
                for p in prefixes:
                    if prefix_buf.startswith(p):
                        prefix_buf = prefix_buf[len(p):].strip()
                        break
                if prefix_buf:
                    yield prefix_buf
        except requests.exceptions.ConnectionError:
            yield "无法连接到 DeepSeek API，请检查网络。"
        except requests.exceptions.HTTPError as e:
            try:
                detail = resp.json()
                msg = detail.get("error", {}).get("message", str(e))
            except Exception:
                msg = str(e)
            yield f"API 错误: {msg}"
        except Exception as e:
            yield f"错误: {str(e)}"

    def chat_sync(self, messages, system_prompt="", temperature=0.8):
        """同步生成完整回复"""
        msgs = self._build_messages(system_prompt, messages)
        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "temperature": temperature,
            "top_p": 0.9,
        }
        if not self.api_key:
            return "[请在 config.py 中设置 DEEPSEEK_API_KEY]"
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return text
        except Exception as e:
            return f"[环境叙述生成失败: {e}]"


# ============================================================
# 全局实例
# ============================================================
db = ChatDB()
deepseek = DeepSeekClient()


# ============================================================
# JWT 认证工具
# ============================================================
def generate_token(remember=False):
    """生成 JWT 令牌。remember=False 时过期时间为 24 小时，True 时为 JWT_EXPIRE_DAYS 天。"""
    expire_hours = 24 if not remember else JWT_EXPIRE_DAYS * 24
    payload = {
        "exp": datetime.utcnow() + timedelta(hours=expire_hours),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")


def verify_token(token):
    """验证 JWT 令牌，成功返回 True，失败返回 False。"""
    try:
        jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return True
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False


def require_auth(f):
    """装饰器：验证请求头中的 Authorization: Bearer <token>"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "未授权"}), 401
        token = auth_header[7:]
        if not verify_token(token):
            return jsonify({"error": "令牌无效或已过期"}), 401
        return f(*args, **kwargs)
    return decorated


# ============================================================
# 认证 API 路由
# ============================================================
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json()
    password = data.get("password", "")
    remember = data.get("remember", False)
    if not password:
        return jsonify({"success": False, "error": "请输入密码"}), 400
    if hashlib.sha256(password.encode()).hexdigest() != LOGIN_PASSWORD_HASH:
        return jsonify({"success": False, "error": "密码错误"}), 401
    token = generate_token(remember=remember)
    return jsonify({"success": True, "token": token})


@app.route("/api/auth/verify", methods=["GET"])
def auth_verify():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"valid": False}), 401
    token = auth_header[7:]
    if verify_token(token):
        return jsonify({"valid": True})
    return jsonify({"valid": False}), 401


# ============================================================
# Flask 路由（需要认证）
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.get_json()
    conv_id = data.get("conversation_id", "")
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"error": "消息不能为空"}), 400

    if not conv_id:
        conv_id = db.create_conversation()
    else:
        conversations = db.get_conversations()
        ids = [c["id"] for c in conversations]
        if conv_id not in ids:
            conv_id = db.create_conversation()

    db.add_message(conv_id, "user", user_message)
    db.touch_conversation(conv_id)

    messages = db.get_messages(conv_id)
    user_msgs = [m for m in messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        title = user_message[:20] + ("..." if len(user_message) > 20 else "")
        db.update_conversation_title(conv_id, title)

    recent_messages = messages[-20:]

    # 提取当前地点（用于注入 AI A 的 system prompt）
    current_location = "公寓客厅"
    latest_atmo = db.get_atmosphere(conv_id, limit=1)
    if latest_atmo:
        m = _LOCATION_RE.search(latest_atmo[0]["content"])
        if m:
            current_location = m.group(1).strip()

    # 拼接动态 system prompt（地点 + 完整角色设定）
    dynamic_chat_prompt = f"【当前地点】：{current_location}\n\n{CHAT_PROMPT}"

    def generate():
        full_response = ""
        yield f"data: {json.dumps({'type': 'conv_id', 'id': conv_id})}\n\n"

        # ---- AI A: 流式对话回复 ----
        for chunk in deepseek.chat_stream(recent_messages, dynamic_chat_prompt):
            full_response += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"

        if full_response:
            # 后处理：安全截断，防止模型续写用户发言（仅影响保存到 DB 的文本）
            clean_for_db = full_response.strip()
            m = _TRUNCATE_RE.search(clean_for_db)
            if m:
                clean_for_db = clean_for_db[:m.start()].strip()
            db.add_message(conv_id, "assistant", clean_for_db)
            db.touch_conversation(conv_id)

        # ---- AI B: 生成环境叙述 ----
        atmo_history = db.get_atmosphere(conv_id, limit=5)
        latest_msgs = messages[-2:]

        # 构建环境叙述 prompt 上下文
        atmo_context = "以下是之前的连续环境描述，请保持延续感：\n"
        if atmo_history:
            for ah in atmo_history:
                atmo_context += f"- {ah['content']}\n"
        else:
            atmo_context += "（这是对话的开始）\n"

        atmo_user = f"{atmo_context}\n最新的对话内容：\n"
        for msg in latest_msgs:
            label = "用户" if msg["role"] == "user" else CHARACTER_NAME
            atmo_user += f"{label}：{msg['content']}\n"
        atmo_user += "\n请输出当前时刻的环境描写（仅旁白文字，不超过60字）："

        atmo_msgs = [{"role": "user", "content": atmo_user}]
        atmo_text = deepseek.chat_sync(atmo_msgs, ATMOSPHERE_PROMPT, temperature=0.8)
        if atmo_text and not atmo_text.startswith("[环境"):
            # 解析地点
            loc_match = _LOCATION_RE.search(atmo_text)
            if loc_match:
                location = loc_match.group(1).strip()
                yield f"data: {json.dumps({'type': 'location', 'content': location})}\n\n"
                # 存储时保留完整文本（含地点标记）以支持历史回放
                db.add_atmosphere(conv_id, atmo_text)
                # 推送环境描写时去除地点行
                atmo_display = _LOCATION_RE.sub("", atmo_text).strip()
                if atmo_display:
                    yield f"data: {json.dumps({'type': 'atmosphere', 'content': atmo_display})}\n\n"
            else:
                db.add_atmosphere(conv_id, atmo_text)
                yield f"data: {json.dumps({'type': 'atmosphere', 'content': atmo_text})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/history", methods=["GET"])
@require_auth
def get_history():
    return jsonify(db.get_conversations())


@app.route("/api/history/<conv_id>", methods=["GET"])
@require_auth
def get_conversation(conv_id):
    messages = db.get_messages(conv_id)
    atmo = db.get_atmosphere(conv_id, limit=100)
    # 从最新环境叙述中提取地点
    location = "公寓客厅"
    atmo_list = [a["content"] for a in atmo]
    for a_text in reversed(atmo_list):
        m = _LOCATION_RE.search(a_text)
        if m:
            location = m.group(1).strip()
            break
    return jsonify({
        "conversation_id": conv_id,
        "messages": messages,
        "atmosphere": atmo_list,
        "location": location,
    })


@app.route("/api/history/<conv_id>", methods=["DELETE"])
@require_auth
def delete_conversation(conv_id):
    db.delete_conversation(conv_id)
    return jsonify({"success": True})


@app.route("/api/history/new", methods=["POST"])
@require_auth
def new_conversation():
    conv_id = db.create_conversation()
    return jsonify({"id": conv_id})


if __name__ == "__main__":
    # 检测本机 IPv4 地址
    import socket
    import subprocess
    ipv4 = ""
    ipv6 = ""
    try:
        hostname = socket.gethostname()
        addrs = socket.getaddrinfo(hostname, None)
        for addr in addrs:
            ip = addr[4][0]
            if "." in ip and not ip.startswith("127.") and not ipv4:
                ipv4 = ip
    except Exception:
        pass

    # 通过 PowerShell 获取永久（非临时）IPv6 全局单播地址
    # Windows 隐私扩展生成的临时地址 SuffixOrigin=Random，会被排除
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-NetIPAddress -AddressFamily IPv6 -AddressState Preferred "
                "-SuffixOrigin Dhcp,Manual,Link | "
                "Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.IPAddress -notlike 'fe80:*' } | "
                "Select-Object -ExpandProperty IPAddress"
            ],
            capture_output=True, text=True, timeout=10
        )
        ip_list = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        if ip_list:
            # 优先取全局公网 IPv6 地址（以 2 或 3 开头），其次取第一个可用地址
            global_ips = [ip for ip in ip_list if ip[0] in ('2', '3')]
            ipv6 = global_ips[0] if global_ips else ip_list[0]
    except Exception:
        pass

    print("=" * 50)
    print("  AI 情感助手 (双AI) 服务启动中...")
    print(f"  模型: {DEEPSEEK_MODEL}")
    print(f"  API: DeepSeek")
    print(f"  本地访问: http://127.0.0.1:5000")
    if ipv4:
        print(f"  IPv4 访问: http://{ipv4}:5000")
    if ipv6:
        print(f"  IPv6 访问: http://[{ipv6}]:5000")
    print("=" * 50)
    app.run(host="::", port=5000, debug=False, threaded=True)
