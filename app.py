"""
AI 情感助手 - Flask 后端 (三 AI 架构)
对接 DeepSeek API (OpenAI 兼容)
AI A: 尤夏对话 | AI B: 环境叙述 | AI C: 世界引擎
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
from config import (CHAT_PROMPT, ATMOSPHERE_PROMPT, EVENT_PROMPT, CHARACTER_NAME,
                     DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
                     LOGIN_PASSWORD_HASH, JWT_SECRET_KEY, JWT_EXPIRE_DAYS, EVENT_AI_INTERVAL)

app = Flask(__name__, static_folder="static", template_folder="templates")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chat.db")
DEEPSEEK_BASE = "https://api.deepseek.com/v1/chat/completions"

_TRUNCATE_RE = re.compile(r'(?:用户|User|Assistant)[：:]', re.IGNORECASE)
_LOCATION_RE = re.compile(r'【地点】\s*(.+)')
_PUSH_CMD_RE = re.compile(r'^/推进\s*(.*)')

# 存储最近一次 random_event（每个会话），用于下一轮注入
_last_random_event = {}  # {conv_id: random_event_dict}


class ChatDB:
    _lock = Lock()

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()
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
            try:
                conn.execute("ALTER TABLE events ADD COLUMN story_time TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE conversations ADD COLUMN events_initialized INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

    def create_conversation(self):
        conv_id = uuid.uuid4().hex[:12]
        with self._get_conn() as conn:
            conn.execute("INSERT INTO conversations(id) VALUES(?)", (conv_id,))
        return conv_id

    def get_conversations(self):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

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
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET events_initialized=1 WHERE id=?", (conv_id,))

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

    def add_atmosphere(self, conv_id, content):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO atmosphere(conversation_id, content) VALUES(?,?)", (conv_id, content))

    def get_atmosphere(self, conv_id, limit=5):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT content FROM atmosphere WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conv_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

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
        with self._get_conn() as conn:
            conn.execute("UPDATE events SET status=? WHERE id=?", (status, event_id))

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
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _build_messages(self, system_prompt, messages):
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        for msg in messages:
            role = "user" if msg["role"] == "user" else "assistant"
            msgs.append({"role": role, "content": msg["content"]})
        return msgs

    def chat_stream(self, messages, system_prompt=""):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": True, "temperature": 0.7, "top_p": 0.9}
        if not self.api_key:
            yield "请在 config.py 中设置 DEEPSEEK_API_KEY"
            return
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, stream=True, timeout=120)
            resp.raise_for_status()
            first_content = False
            prefix_buf = ""
            prefixes = (f"{CHARACTER_NAME}：", f"{CHARACTER_NAME}:", "青梅：")
            for line in resp.iter_lines(decode_unicode=True):
                if not line: continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]": break
                    try:
                        delta = json.loads(data_str).get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if not content: continue
                        if not first_content:
                            prefix_buf += content
                            stripped = prefix_buf
                            for p in prefixes:
                                if prefix_buf.startswith(p):
                                    stripped = prefix_buf[len(p):].strip()
                                    break
                            max_prefix_len = max(len(p) for p in prefixes)
                            if len(prefix_buf) >= max_prefix_len or stripped != prefix_buf:
                                first_content = True
                                if stripped: yield stripped
                        else:
                            yield content
                    except json.JSONDecodeError: continue
            if not first_content and prefix_buf:
                for p in prefixes:
                    if prefix_buf.startswith(p):
                        prefix_buf = prefix_buf[len(p):].strip()
                        break
                if prefix_buf: yield prefix_buf
        except requests.exceptions.ConnectionError:
            yield "无法连接到 DeepSeek API，请检查网络。"
        except requests.exceptions.HTTPError as e:
            try:
                detail = resp.json()
                msg = detail.get("error", {}).get("message", str(e))
            except Exception: msg = str(e)
            yield f"API 错误: {msg}"
        except Exception as e:
            yield f"错误: {str(e)}"

    def chat_sync(self, messages, system_prompt="", temperature=0.8):
        msgs = self._build_messages(system_prompt, messages)
        payload = {"model": self.model, "messages": msgs, "stream": False, "temperature": temperature, "top_p": 0.9}
        if not self.api_key: return "[请在 config.py 中设置 DEEPSEEK_API_KEY]"
        try:
            resp = requests.post(self.base_url, json=payload, headers=self._headers, timeout=120)
            resp.raise_for_status()
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"[生成失败: {e}]"


db = ChatDB()
deepseek = DeepSeekClient()


def generate_token(remember=False):
    expire_hours = 24 if not remember else JWT_EXPIRE_DAYS * 24
    return jwt.encode({"exp": datetime.utcnow() + timedelta(hours=expire_hours), "iat": datetime.utcnow()}, JWT_SECRET_KEY, algorithm="HS256")

def verify_token(token):
    try:
        jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return True
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "): return jsonify({"error": "未授权"}), 401
        if not verify_token(auth_header[7:]): return jsonify({"error": "令牌无效或已过期"}), 401
        return f(*args, **kwargs)
    return decorated


# ============================================================
# AI C: 世界引擎核心逻辑
# ============================================================
def _parse_event_json(raw_text):
    if not raw_text or raw_text.startswith("[生成失败"):
        return None
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        json_text = re.sub(r'^```(?:json)?\s*\n?', '', json_text)
        json_text = re.sub(r'\n?```\s*$', '', json_text)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        print(f"[AI C] JSON 解析失败: {json_text[:200]}")
        return None


def _run_event_ai(conv_id, force_push=False, is_initializing=False):
    try:
        messages = db.get_messages(conv_id)
        existing_events = db.get_events(conv_id, limit=100)
        current_story_time = db.get_latest_story_time(conv_id) or "第一天早晨 8:00"

        msg_limit = len(messages) if is_initializing else 30
        history_text_parts = []
        for msg in messages[-msg_limit:]:
            label = "青梅" if msg["role"] == "user" else "尤夏"
            history_text_parts.append(f"{label}：{msg['content']}")
        history_text = "\n".join(history_text_parts)

        events_text = "暂无"
        if existing_events:
            event_lines = []
            for e in existing_events:
                time_str = f" @{e['story_time']}" if e.get('story_time') else ""
                status_label = {"active": "进行中", "completed": "已完成", "pending": "待触发"}.get(e["status"], e["status"])
                event_lines.append(f"- [{status_label}] [{e['event_type']}] {e['title']}: {e['description']}{time_str}")
            events_text = "\n".join(event_lines)

        init_extra = ""
        push_extra = ""
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

        temp = 0.4
        if force_push: temp = 0.7
        elif is_initializing: temp = 0.5

        event_result = deepseek.chat_sync(
            messages=[{"role": "user", "content": event_user_msg}],
            system_prompt=EVENT_PROMPT, temperature=temp)

        data = _parse_event_json(event_result)
        if not data: return None

        current_time = data.get("story_time", current_story_time)

        # 写入必然事件
        new_events = data.get("events", [])
        if new_events:
            for event in new_events:
                etype = event.get("type", "misc")
                if etype not in ('milestone', 'scene_change', 'emotional_shift', 'time_advance', 'random', 'misc'):
                    etype = 'misc'
                db.add_event(conv_id, etype,
                    event.get("title", "未命名事件"),
                    event.get("description", ""),
                    event.get("status", "active"),
                    story_time=event.get("story_time", current_time))

        # 处理随机事件
        random_event = data.get("random_event")
        if random_event and isinstance(random_event, dict):
            # 将 random_event 也作为事件存入数据库（类型为 random）
            db.add_event(conv_id, "random",
                random_event.get("title", "偶然事件"),
                random_event.get("description", ""),
                "active",
                story_time=current_time)
            # 存储到全局缓存，供下一轮 _build_event_context 使用
            _last_random_event[conv_id] = {
                "description": random_event.get("description", ""),
                "context_reason": random_event.get("context_reason", ""),
                "intensity": random_event.get("intensity", "low"),
            }
        elif conv_id in _last_random_event:
            # 上一轮的 random_event 已经被消耗了，清除
            del _last_random_event[conv_id]

        if is_initializing:
            db.mark_events_initialized(conv_id)

        return {
            "story_time": current_time,
            "story_summary": data.get("story_summary", ""),
            "events": new_events,
            "random_event": random_event,
            "should_push": data.get("should_push", False),
            "push_hint": data.get("push_hint", ""),
        }
    except Exception as e:
        print(f"[AI C] 运行异常: {e}")
        return None


def _init_events_from_history(conv_id):
    db.clear_events(conv_id)
    return _run_event_ai(conv_id, force_push=False, is_initializing=True)


def _build_event_context(conv_id):
    """构建事件上下文（随机事件已改为 SSE 推送，不再注入 system prompt）"""
    events = db.get_events(conv_id, limit=20)
    lines = []

    # 故事时间
    story_time = db.get_latest_story_time(conv_id)
    if story_time:
        lines.append(f"【故事时间】{story_time}")

    # 故事进展
    if events:
        active_events = [e for e in events if e["status"] == "active"]
        completed_events = [e for e in events if e["status"] == "completed" and e["event_type"] != "random"]
        pending_events = [e for e in events if e["status"] == "pending"]

        lines.append("【故事进展】")
        if active_events:
            lines.append("当前进行中的事件：")
            for e in active_events[-5:]:
                lines.append(f"  → {e['title']}（{e['description']}）")
        if pending_events:
            lines.append("即将发生/待回应的事件：")
            for e in pending_events[-3:]:
                lines.append(f"  ⏳ {e['title']}（{e['description']}）")
        if completed_events:
            lines.append("近期已完成的事件：")
            for e in completed_events[-3:]:
                lines.append(f"  ✓ {e['title']}")

    return "\n".join(lines)


# ============================================================
# 认证 API
# ============================================================
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json()
    password = data.get("password", "")
    remember = data.get("remember", False)
    if not password: return jsonify({"success": False, "error": "请输入密码"}), 400
    if hashlib.sha256(password.encode()).hexdigest() != LOGIN_PASSWORD_HASH:
        return jsonify({"success": False, "error": "密码错误"}), 401
    return jsonify({"success": True, "token": generate_token(remember=remember)})

@app.route("/api/auth/verify", methods=["GET"])
def auth_verify():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "): return jsonify({"valid": False}), 401
    return jsonify({"valid": verify_token(auth_header[7:])})


# ============================================================
# Flask 路由
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.get_json()
    conv_id = data.get("conversation_id", "")
    user_message = data.get("message", "").strip()

    if not user_message: return jsonify({"error": "消息不能为空"}), 400

    is_push_cmd = False
    push_hint_text = ""
    push_cmd_match = _PUSH_CMD_RE.match(user_message)
    if push_cmd_match:
        is_push_cmd = True
        push_hint_text = push_cmd_match.group(1).strip()
        if push_hint_text:
            user_message = push_hint_text
        else:
            user_message = "（青梅轻轻戳了戳尤夏的肩膀，似乎在期待什么...）"

    if not conv_id: conv_id = db.create_conversation()
    else:
        conversations = db.get_conversations()
        if conv_id not in [c["id"] for c in conversations]:
            conv_id = db.create_conversation()

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
    if is_push_cmd:
        push_extra = "\n\n【系统提示】故事需要向前推进了。请自然地推动情节发展。"

    dynamic_chat_prompt = f"【当前地点】：{current_location}\n{event_context}{push_extra}\n\n{CHAT_PROMPT}"

    msg_count = db.get_message_count(conv_id)
    should_run_event_ai = is_push_cmd or (msg_count % EVENT_AI_INTERVAL == 0)

    def generate():
        full_response = ""
        yield f"data: {json.dumps({'type': 'conv_id', 'id': conv_id})}\n\n"

        # 推送上一轮缓存的随机事件（世界旁白）
        re_info = _last_random_event.pop(conv_id, None)
        if re_info:
            yield f"data: {json.dumps({'type': 'world_narration', 'content': re_info['description'], 'intensity': re_info.get('intensity', 'low')})}\n\n"

        # AI A
        for chunk in deepseek.chat_stream(recent_messages, dynamic_chat_prompt):
            full_response += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"

        if full_response:
            clean_for_db = full_response.strip()
            m = _TRUNCATE_RE.search(clean_for_db)
            if m: clean_for_db = clean_for_db[:m.start()].strip()
            db.add_message(conv_id, "assistant", clean_for_db)
            db.touch_conversation(conv_id)

        # AI B
        all_messages = db.get_messages(conv_id)
        atmo_history = db.get_atmosphere(conv_id, limit=5)
        latest_msgs = all_messages[-2:]
        atmo_context = "以下是之前的连续环境描述，请保持延续感：\n"
        if atmo_history:
            for ah in atmo_history: atmo_context += f"- {ah['content']}\n"
        else:
            atmo_context += "（这是对话的开始）\n"
        atmo_user = f"{atmo_context}\n最新的对话内容：\n"
        for msg in latest_msgs:
            label = "用户" if msg["role"] == "user" else CHARACTER_NAME
            atmo_user += f"{label}：{msg['content']}\n"
        atmo_user += "\n请输出当前时刻的环境描写（仅旁白文字，不超过60字）："
        atmo_text = deepseek.chat_sync([{"role": "user", "content": atmo_user}], ATMOSPHERE_PROMPT, temperature=0.8)
        if atmo_text and not atmo_text.startswith("[生成失败"):
            loc_match = _LOCATION_RE.search(atmo_text)
            if loc_match:
                location = loc_match.group(1).strip()
                yield f"data: {json.dumps({'type': 'location', 'content': location})}\n\n"
                db.add_atmosphere(conv_id, atmo_text)
                atmo_display = _LOCATION_RE.sub("", atmo_text).strip()
                if atmo_display: yield f"data: {json.dumps({'type': 'atmosphere', 'content': atmo_display})}\n\n"
            else:
                db.add_atmosphere(conv_id, atmo_text)
                yield f"data: {json.dumps({'type': 'atmosphere', 'content': atmo_text})}\n\n"

        # AI C
        if should_run_event_ai:
            event_result = _run_event_ai(conv_id, force_push=is_push_cmd)
            if event_result:
                if event_result.get("story_time"):
                    yield f"data: {json.dumps({'type': 'story_time', 'content': event_result['story_time']})}\n\n"
                if event_result.get("story_summary"):
                    yield f"data: {json.dumps({'type': 'story_summary', 'content': event_result['story_summary']})}\n\n"
                all_events = db.get_events(conv_id, limit=50)
                yield f"data: {json.dumps({'type': 'event_update', 'events': all_events, 'has_push': event_result.get('should_push', False), 'push_hint': event_result.get('push_hint', ''), 'random_event': event_result.get('random_event')})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/events/init/<conv_id>", methods=["POST"])
@require_auth
def init_events(conv_id):
    try:
        messages = db.get_messages(conv_id)
        if not messages: return jsonify({"success": False, "error": "该会话没有对话记录"}), 400
        if db.is_events_initialized(conv_id):
            return jsonify({"success": False, "error": "该会话已完成事件初始化"}), 400
        result = _init_events_from_history(conv_id)
        if not result: return jsonify({"success": False, "error": "AI C 分析失败"}), 500
        return jsonify({"success": True, "story_time": result.get("story_time", ""), "story_summary": result.get("story_summary", ""),
                        "events": db.get_events(conv_id, limit=50), "has_push": result.get("should_push", False),
                        "push_hint": result.get("push_hint", ""), "random_event": result.get("random_event")})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/events/<conv_id>", methods=["GET"])
@require_auth
def get_events(conv_id):
    return jsonify({"events": db.get_events(conv_id, limit=50), "story_time": db.get_latest_story_time(conv_id),
                    "initialized": db.is_events_initialized(conv_id)})


@app.route("/api/history", methods=["GET"])
@require_auth
def get_history():
    return jsonify(db.get_conversations())


@app.route("/api/history/<conv_id>", methods=["GET"])
@require_auth
def get_conversation(conv_id):
    messages = db.get_messages(conv_id)
    atmo = db.get_atmosphere(conv_id, limit=100)
    events = db.get_events(conv_id, limit=50)
    story_time = db.get_latest_story_time(conv_id)
    initialized = db.is_events_initialized(conv_id)
    location = "公寓客厅"
    atmo_list = [a["content"] for a in atmo]
    for a_text in reversed(atmo_list):
        m = _LOCATION_RE.search(a_text)
        if m: location = m.group(1).strip(); break
    return jsonify({"conversation_id": conv_id, "messages": messages, "atmosphere": atmo_list,
                    "location": location, "events": events, "story_time": story_time, "events_initialized": initialized})


@app.route("/api/history/<conv_id>", methods=["DELETE"])
@require_auth
def delete_conversation(conv_id):
    db.delete_conversation(conv_id)
    return jsonify({"success": True})


@app.route("/api/history/new", methods=["POST"])
@require_auth
def new_conversation():
    return jsonify({"id": db.create_conversation()})


if __name__ == "__main__":
    import socket, subprocess
    ipv4 = ""
    try:
        hostname = socket.gethostname()
        for addr in socket.getaddrinfo(hostname, None):
            ip = addr[4][0]
            if "." in ip and not ip.startswith("127.") and not ipv4: ipv4 = ip
    except Exception: pass
    ipv6 = ""
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
    print("  AI 情感助手 (三AI - 世界引擎) 服务启动中...")
    print(f"  模型: {DEEPSEEK_MODEL}")
    print(f"  API: DeepSeek")
    print(f"  本地访问: http://127.0.0.1:5000")
    if ipv4: print(f"  IPv4 访问: http://{ipv4}:5000")
    if ipv6: print(f"  IPv6 访问: http://[{ipv6}]:5000")
    print("=" * 50)
    app.run(host="::", port=5000, debug=False, threaded=True)