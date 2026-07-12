"""
AI 情感助手 - Flask 后端 (三 AI 架构 + 账号系统)
AI A: 尤夏对话 | AI B: 环境叙述 | AI C: 世界引擎
"""

import json, re, sqlite3, os, uuid, hashlib
from datetime import datetime, timedelta
from threading import Lock
from functools import wraps

import jwt, requests
from flask import Flask, render_template, request, Response, jsonify
from config import (CHAT_PROMPT, ATMOSPHERE_PROMPT, EVENT_PROMPT, CHARACTER_NAME,
                     DEEPSEEK_API_KEY, DEEPSEEK_MODEL, PRESET_ACCOUNTS,
                     JWT_SECRET_KEY, JWT_EXPIRE_DAYS, EVENT_AI_INTERVAL)

app = Flask(__name__, static_folder="static", template_folder="templates")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chat.db")
DEEPSEEK_BASE = "https://api.deepseek.com/v1/chat/completions"

_TRUNCATE_RE = re.compile(r'(?:用户|User|Assistant)[：:]', re.IGNORECASE)
_LOCATION_RE = re.compile(r'【地点】\s*(.+)')
_PUSH_CMD_RE = re.compile(r'^/推进\s*(.*)')


class ChatDB:
    _lock = Lock()

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()
        self._sync_accounts()
        self._migrate_db()

    def _get_conn(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    username   TEXT UNIQUE NOT NULL,
                    password   TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT PRIMARY KEY,
                    user_id     INTEGER NOT NULL DEFAULT 0,
                    title       TEXT DEFAULT '新对话',
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    updated_at  TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
                CREATE TABLE IF NOT EXISTS events (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    event_type      TEXT NOT NULL CHECK(event_type IN ('milestone','scene_change','emotional_shift','time_advance','random','misc')),
                    title           TEXT NOT NULL,
                    description     TEXT NOT NULL,
                    status          TEXT DEFAULT 'active' CHECK(status IN ('active','completed','pending')),
                    story_time      TEXT DEFAULT '',
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
            """)

    def _migrate_db(self):
        with self._get_conn() as conn:
            for col, tbl in [("story_time", "events"), ("events_initialized", "conversations"), ("user_id", "conversations")]:
                try: conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT DEFAULT ''" if col == "story_time" else f"ALTER TABLE {tbl} ADD COLUMN {col} INTEGER DEFAULT 0")
                except sqlite3.OperationalError: pass

            # 将旧对话 (user_id=0) 迁移到第一个用户
            try:
                first_user = conn.execute("SELECT MIN(id) as uid FROM users").fetchone()
                if first_user and first_user["uid"]:
                    conn.execute("UPDATE conversations SET user_id=? WHERE user_id=0", (first_user["uid"],))
            except Exception: pass

    def _sync_accounts(self):
        with self._get_conn() as conn:
            for username, pw_hash in PRESET_ACCOUNTS.items():
                existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                if not existing:
                    conn.execute("INSERT INTO users(username, password) VALUES(?,?)", (username, pw_hash))
                else:
                    conn.execute("UPDATE users SET password=? WHERE username=?", (pw_hash, username))

    def get_user(self, username):
        with self._get_conn() as conn:
            return conn.execute("SELECT id, username, password FROM users WHERE username=?", (username,)).fetchone()

    # ----- 会话 (user_id 隔离) -----
    def create_conversation(self, user_id):
        conv_id = uuid.uuid4().hex[:12]
        with self._get_conn() as conn:
            conn.execute("INSERT INTO conversations(id, user_id) VALUES(?,?)", (conv_id, user_id))
        return conv_id

    def get_conversations(self, user_id):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id):
        with self._get_conn() as conn: conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    def update_conversation_title(self, conv_id, title):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET title=?, updated_at=datetime('now','localtime') WHERE id=?", (title, conv_id))

    def touch_conversation(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET updated_at=datetime('now','localtime') WHERE id=?", (conv_id,))

    def is_events_initialized(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT events_initialized FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return row and row["events_initialized"] == 1

    def mark_events_initialized(self, conv_id):
        with self._get_conn() as conn: conn.execute("UPDATE conversations SET events_initialized=1 WHERE id=?", (conv_id,))

    def conversation_belongs_to(self, conv_id, user_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id)).fetchone()
        return row is not None

    # ----- 消息 -----
    def add_message(self, conv_id, role, content):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES(?,?,?)", (conv_id, role, content))

    def get_messages(self, conv_id):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id ASC", (conv_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_message_count(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM messages WHERE conversation_id=? AND role='user'", (conv_id,)).fetchone()
        return row["cnt"] if row else 0

    # ----- 环境叙述 -----
    def add_atmosphere(self, conv_id, content):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO atmosphere(conversation_id, content) VALUES(?,?)", (conv_id, content))

    def get_atmosphere(self, conv_id, limit=5):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT content FROM atmosphere WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conv_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ----- 事件 -----
    def add_event(self, conv_id, event_type, title, description, status="active", story_time=""):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO events(conversation_id, event_type, title, description, status, story_time) VALUES(?,?,?,?,?,?)",
                         (conv_id, event_type, title, description, status, story_time))

    def get_events(self, conv_id, limit=30):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, event_type, title, description, status, story_time, created_at FROM events WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
                                (conv_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def update_event_status(self, event_id, status):
        with self._get_conn() as conn: conn.execute("UPDATE events SET status=? WHERE id=?", (status, event_id))

    def clear_events(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM events WHERE conversation_id=?", (conv_id,))
            conn.execute("UPDATE conversations SET events_initialized=0 WHERE id=?", (conv_id,))

    def get_latest_story_time(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT story_time FROM events WHERE conversation_id=? AND story_time != '' ORDER BY id DESC LIMIT 1", (conv_id,)).fetchone()
        return row["story_time"] if row else ""


class DeepSeekClient:
    def __init__(self, base_url=DEEPSEEK_BASE, model=DEEPSEEK_MODEL, api_key=DEEPSEEK_API_KEY):
        self.base_url = base_url; self.model = model; self.api_key = api_key
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _build_messages(self, system_prompt, messages):
        msgs = [{"role": "system", "content": system_prompt}] if system_prompt else []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "assistant"
            msgs.append({"role": role, "content": msg["content"]})
        return msgs

    def chat_stream(self, messages, system_prompt=""):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": True, "temperature": 0.7, "top_p": 0.9}
        if not self.api_key: yield "请在 config.py 中设置 DEEPSEEK_API_KEY"; return
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, stream=True, timeout=120)
            resp.raise_for_status()
            fc = False; buf = ""
            prefixes = (f"{CHARACTER_NAME}：", f"{CHARACTER_NAME}:", "青梅：")
            for line in resp.iter_lines(decode_unicode=True):
                if not line: continue
                if line.startswith("data: "):
                    ds = line[6:]
                    if ds.strip() == "[DONE]": break
                    try:
                        content = json.loads(ds).get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if not content: continue
                        if not fc:
                            buf += content; s = buf
                            for p in prefixes:
                                if buf.startswith(p): s = buf[len(p):].strip(); break
                            if len(buf) >= max(len(p) for p in prefixes) or s != buf:
                                fc = True
                                if s: yield s
                        else: yield content
                    except json.JSONDecodeError: continue
            if not fc and buf:
                for p in prefixes:
                    if buf.startswith(p): buf = buf[len(p):].strip(); break
                if buf: yield buf
        except requests.exceptions.ConnectionError: yield "无法连接到 DeepSeek API，请检查网络。"
        except requests.exceptions.HTTPError as e:
            try: msg = resp.json().get("error", {}).get("message", str(e))
            except Exception: msg = str(e)
            yield f"API 错误: {msg}"
        except Exception as e: yield f"错误: {str(e)}"

    def chat_sync(self, messages, system_prompt="", temperature=0.8):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": False, "temperature": temperature, "top_p": 0.9}
        if not self.api_key: return "[请在 config.py 中设置 DEEPSEEK_API_KEY]"
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, timeout=120)
            resp.raise_for_status()
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception as e: return f"[生成失败: {e}]"


db = ChatDB()
deepseek = DeepSeekClient()


# ============================================================
# JWT 工具
# ============================================================
def generate_token(user_id, remember=False):
    expire_hours = 24 if not remember else JWT_EXPIRE_DAYS * 24
    payload = {"user_id": user_id, "exp": datetime.utcnow() + timedelta(hours=expire_hours), "iat": datetime.utcnow()}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")

def verify_token(token):
    try: return jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError): return None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get("auth_token") or ""
        if not token:
            # 兼容旧的 Bearer header
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token: return jsonify({"error": "未授权"}), 401
        payload = verify_token(token)
        if not payload: return jsonify({"error": "令牌无效或已过期"}), 401
        request.user_id = payload["user_id"]
        return f(*args, **kwargs)
    return decorated


# ============================================================
# AI C
# ============================================================
def _parse_event_json(raw_text):
    if not raw_text or raw_text.startswith("[生成失败"): return None
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        json_text = re.sub(r'^```(?:json)?\s*\n?', '', json_text)
        json_text = re.sub(r'\n?```\s*$', '', json_text)
    try: return json.loads(json_text)
    except json.JSONDecodeError:
        print(f"[AI C] JSON 解析失败: {json_text[:200]}"); return None

def _run_event_ai(conv_id, force_push=False, is_initializing=False):
    try:
        messages = db.get_messages(conv_id)
        existing_events = db.get_events(conv_id, limit=100)
        current_story_time = db.get_latest_story_time(conv_id) or "第一天早晨 8:00"
        msg_limit = len(messages) if is_initializing else 30
        history_text = "\n".join(
            f"{'青梅' if m['role'] == 'user' else '尤夏'}：{m['content']}" for m in messages[-msg_limit:]
        )
        events_text = "暂无"
        if existing_events:
            lines = []
            for e in existing_events:
                ts = f" @{e['story_time']}" if e.get('story_time') else ""
                sl = {"active": "进行中", "completed": "已完成", "pending": "待触发"}.get(e["status"], e["status"])
                lines.append(f"- [{sl}] [{e['event_type']}] {e['title']}: {e['description']}{ts}")
            events_text = "\n".join(lines)
        init_extra = push_extra = ""
        if is_initializing:
            init_extra = "\n【注意】这是历史事件初始化任务。请一次性分析全部对话历史，识别所有关键事件节点并生成完整的时间线。从「第一天早晨 8:00」开始。\n"
        if force_push:
            push_extra = "\n【注意】用户发送了 /推进 指令，请在本次分析中主动推进故事！如果有合适的关联线索，可以生成 random_event。\n"
        event_user_msg = (
            f"以下是对话历史：\n```\n{history_text}\n```\n\n"
            f"以下是已有的事件记录：\n{events_text}\n\n"
            f"对话轮数（用户发言次数）：{db.get_message_count(conv_id)}\n"
            f"当前故事时间：{current_story_time}\n"
            f"{init_extra}{push_extra}"
            "请分析当前故事进展，输出 JSON。"
        )
        temp = 0.7 if force_push else (0.5 if is_initializing else 0.4)
        event_result = deepseek.chat_sync(messages=[{"role": "user", "content": event_user_msg}], system_prompt=EVENT_PROMPT, temperature=temp)
        data = _parse_event_json(event_result)
        if not data: return None
        current_time = data.get("story_time", current_story_time)
        for event in data.get("events", []):
            etype = event.get("type", "misc")
            if etype not in ('milestone','scene_change','emotional_shift','time_advance','random','misc'): etype = 'misc'
            db.add_event(conv_id, etype, event.get("title", "未命名事件"), event.get("description", ""),
                         event.get("status", "active"), story_time=event.get("story_time", current_time))
        random_event = data.get("random_event")
        if random_event and isinstance(random_event, dict):
            db.add_event(conv_id, "random", random_event.get("title", "偶然事件"), random_event.get("description", ""),
                         "active", story_time=current_time)
        if is_initializing: db.mark_events_initialized(conv_id)
        return {
            "story_time": current_time, "story_summary": data.get("story_summary", ""),
            "events": data.get("events", []), "random_event": random_event,
            "should_push": data.get("should_push", False), "push_hint": data.get("push_hint", ""),
        }
    except Exception as e:
        print(f"[AI C] 运行异常: {e}"); return None

def _init_events_from_history(conv_id):
    db.clear_events(conv_id)
    return _run_event_ai(conv_id, force_push=False, is_initializing=True)

def _build_event_context(conv_id):
    events = db.get_events(conv_id, limit=20)
    lines = []
    story_time = db.get_latest_story_time(conv_id)
    if story_time: lines.append(f"【故事时间】{story_time}")
    if events:
        active = [e for e in events if e["status"] == "active"]
        completed = [e for e in events if e["status"] == "completed" and e["event_type"] != "random"]
        pending = [e for e in events if e["status"] == "pending"]
        lines.append("【故事进展】")
        if active:
            lines.append("当前进行中的事件：")
            for e in active[-5:]: lines.append(f"  → {e['title']}（{e['description']}）")
        if pending:
            lines.append("即将发生/待回应的事件：")
            for e in pending[-3:]: lines.append(f"  ⏳ {e['title']}（{e['description']}）")
        if completed:
            lines.append("近期已完成的事件：")
            for e in completed[-3:]: lines.append(f"  ✓ {e['title']}")
    return "\n".join(lines)


# ============================================================
# 路由
# ============================================================
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    remember = data.get("remember", False)
    if not username or not password: return jsonify({"success": False, "error": "请输入用户名和密码"}), 400
    user = db.get_user(username)
    if not user: return jsonify({"success": False, "error": "用户不存在"}), 401
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    if pw_hash != user["password"]: return jsonify({"success": False, "error": "密码错误"}), 401
    token = generate_token(user["id"], remember=remember)
    resp = jsonify({"success": True, "username": user["username"]})
    max_age = JWT_EXPIRE_DAYS * 86400 if remember else 86400  # remember=30天, 否则1天
    resp.set_cookie("auth_token", token, httponly=True, max_age=max_age, samesite="Lax")
    return resp

@app.route("/api/auth/verify", methods=["GET"])
def auth_verify():
    token = request.cookies.get("auth_token") or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token: return jsonify({"valid": False})
    payload = verify_token(token)
    return jsonify({"valid": bool(payload)})

@app.route("/")
def index(): return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.get_json()
    conv_id = data.get("conversation_id", "")
    user_message = data.get("message", "").strip()
    user_id = request.user_id
    if not user_message: return jsonify({"error": "消息不能为空"}), 400

    is_push_cmd = False
    push_cmd_match = _PUSH_CMD_RE.match(user_message)
    if push_cmd_match:
        is_push_cmd = True
        hint = push_cmd_match.group(1).strip()
        user_message = hint if hint else "（青梅轻轻戳了戳尤夏的肩膀，似乎在期待什么...）"

    if not conv_id: conv_id = db.create_conversation(user_id)
    elif not db.conversation_belongs_to(conv_id, user_id): conv_id = db.create_conversation(user_id)

    db.add_message(conv_id, "user", user_message)
    db.touch_conversation(conv_id)
    messages = db.get_messages(conv_id)
    user_msgs = [m for m in messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_conversation_title(conv_id, user_message[:20] + ("..." if len(user_message) > 20 else ""))

    recent_messages = messages[-20:]
    current_location = "公寓客厅"
    latest_atmo = db.get_atmosphere(conv_id, limit=1)
    if latest_atmo:
        m = _LOCATION_RE.search(latest_atmo[0]["content"])
        if m: current_location = m.group(1).strip()
    event_context = _build_event_context(conv_id)
    push_extra = ""
    if is_push_cmd: push_extra = "\n\n【系统提示】故事需要向前推进了。请自然地推动情节发展。"
    dynamic_chat_prompt = f"【当前地点】：{current_location}\n{event_context}{push_extra}\n\n{CHAT_PROMPT}"
    msg_count = db.get_message_count(conv_id)
    should_run_event_ai = is_push_cmd or (msg_count % EVENT_AI_INTERVAL == 0)

    def generate():
        full_response = ""
        yield f"data: {json.dumps({'type': 'conv_id', 'id': conv_id})}\n\n"
        for chunk in deepseek.chat_stream(recent_messages, dynamic_chat_prompt):
            full_response += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        if full_response:
            clean = full_response.strip()
            m = _TRUNCATE_RE.search(clean)
            if m: clean = clean[:m.start()].strip()
            db.add_message(conv_id, "assistant", clean)
            db.touch_conversation(conv_id)
        # AI B
        all_messages = db.get_messages(conv_id)
        atmo_history = db.get_atmosphere(conv_id, limit=5)
        latest_msgs = all_messages[-2:]
        ac = "以下是之前的连续环境描述，请保持延续感：\n"
        if atmo_history:
            for ah in atmo_history: ac += f"- {ah['content']}\n"
        else: ac += "（这是对话的开始）\n"
        au = f"{ac}\n最新的对话内容：\n"
        for msg in latest_msgs:
            label = "用户" if msg["role"] == "user" else CHARACTER_NAME
            au += f"{label}：{msg['content']}\n"
        au += "\n请输出当前时刻的环境描写（仅旁白文字，不超过60字）："
        atmo_text = deepseek.chat_sync([{"role": "user", "content": au}], ATMOSPHERE_PROMPT, temperature=0.8)
        if atmo_text and not atmo_text.startswith("[生成失败"):
            loc_match = _LOCATION_RE.search(atmo_text)
            if loc_match:
                loc = loc_match.group(1).strip()
                yield f"data: {json.dumps({'type': 'location', 'content': loc})}\n\n"
                db.add_atmosphere(conv_id, atmo_text)
                ad = _LOCATION_RE.sub("", atmo_text).strip()
                if ad: yield f"data: {json.dumps({'type': 'atmosphere', 'content': ad})}\n\n"
            else:
                db.add_atmosphere(conv_id, atmo_text)
                yield f"data: {json.dumps({'type': 'atmosphere', 'content': atmo_text})}\n\n"
        # AI C
        if should_run_event_ai:
            yield f"data: {json.dumps({'type': 'event_organizing'})}\n\n"
            ev = _run_event_ai(conv_id, force_push=is_push_cmd)
            if ev:
                re2 = ev.get("random_event")
                if re2 and isinstance(re2, dict):
                    yield f"data: {json.dumps({'type': 'world_narration', 'content': re2.get('description',''), 'intensity': re2.get('intensity','low')})}\n\n"
                if ev.get("story_time"): yield f"data: {json.dumps({'type': 'story_time', 'content': ev['story_time']})}\n\n"
                if ev.get("story_summary"): yield f"data: {json.dumps({'type': 'story_summary', 'content': ev['story_summary']})}\n\n"
                all_events = db.get_events(conv_id, limit=50)
                yield f"data: {json.dumps({'type': 'event_update', 'events': all_events, 'has_push': ev.get('should_push',False), 'push_hint': ev.get('push_hint','')})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/events/init/<conv_id>", methods=["POST"])
@require_auth
def init_events(conv_id):
    try:
        messages = db.get_messages(conv_id)
        if not messages: return jsonify({"success": False, "error": "该会话没有对话记录"}), 400
        if db.is_events_initialized(conv_id): return jsonify({"success": False, "error": "该会话已完成事件初始化"}), 400
        result = _init_events_from_history(conv_id)
        if not result: return jsonify({"success": False, "error": "AI C 分析失败"}), 500
        return jsonify({"success": True, "story_time": result.get("story_time",""), "story_summary": result.get("story_summary",""),
                        "events": db.get_events(conv_id, limit=50), "has_push": result.get("should_push",False),
                        "push_hint": result.get("push_hint",""), "random_event": result.get("random_event")})
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/events/<conv_id>", methods=["GET"])
@require_auth
def get_events(conv_id):
    return jsonify({"events": db.get_events(conv_id, limit=50), "story_time": db.get_latest_story_time(conv_id),
                    "initialized": db.is_events_initialized(conv_id)})

@app.route("/api/history", methods=["GET"])
@require_auth
def get_history():
    return jsonify(db.get_conversations(request.user_id))

@app.route("/api/history/<conv_id>", methods=["GET"])
@require_auth
def get_conversation(conv_id):
    messages = db.get_messages(conv_id)
    atmo = db.get_atmosphere(conv_id, limit=100)
    events = db.get_events(conv_id, limit=50)
    story_time = db.get_latest_story_time(conv_id)
    story_summary = events[-1]["description"] if events else ""
    initialized = db.is_events_initialized(conv_id)
    location = "公寓客厅"
    atmo_list = [a["content"] for a in atmo]
    for a_text in reversed(atmo_list):
        m = _LOCATION_RE.search(a_text)
        if m: location = m.group(1).strip(); break
    return jsonify({"conversation_id": conv_id, "messages": messages, "atmosphere": atmo_list,
                    "location": location, "events": events, "story_time": story_time,
                    "story_summary": story_summary, "events_initialized": initialized})

@app.route("/api/history/<conv_id>", methods=["DELETE"])
@require_auth
def delete_conversation(conv_id):
    db.delete_conversation(conv_id); return jsonify({"success": True})

@app.route("/api/history/new", methods=["POST"])
@require_auth
def new_conversation():
    return jsonify({"id": db.create_conversation(request.user_id)})

if __name__ == "__main__":
    import socket, subprocess
    ipv4 = ipv6 = ""
    try:
        for addr in socket.getaddrinfo(socket.gethostname(), None):
            ip = addr[4][0]
            if "." in ip and not ip.startswith("127.") and not ipv4: ipv4 = ip
    except Exception: pass
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command",
            "Get-NetIPAddress -AddressFamily IPv6 -AddressState Preferred -SuffixOrigin Dhcp,Manual,Link | "
            "Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.IPAddress -notlike 'fe80:*' } | "
            "Select-Object -ExpandProperty IPAddress"], capture_output=True, text=True, timeout=10)
        ip_list = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        if ip_list:
            global_ips = [ip for ip in ip_list if ip[0] in ('2', '3')]
            ipv6 = global_ips[0] if global_ips else ip_list[0]
    except Exception: pass
    print("=" * 50)
    print("  AI 情感助手 (三AI - 账号系统) 服务启动中...")
    print(f"  模型: {DEEPSEEK_MODEL}")
    print(f"  API: DeepSeek")
    print(f"  本地访问: http://127.0.0.1:5000")
    if ipv4: print(f"  IPv4 访问: http://{ipv4}:5000")
    if ipv6: print(f"  IPv6 访问: http://[{ipv6}]:5000")
    print("=" * 50)
    app.run(host="::", port=5000, debug=False, threaded=True)