"""
ChatDB: 数据库初始化、迁移、CRUD 操作封装
"""
import os, sqlite3, uuid, hashlib
from threading import Lock


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "chat.db")


class ChatDB:
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

    def migrate(self):
        with self._get_conn() as conn:
            for col, tbl in [("story_time", "events"), ("events_initialized", "conversations"), ("user_id", "conversations")]:
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT DEFAULT ''" if col == "story_time" else f"ALTER TABLE {tbl} ADD COLUMN {col} INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass

            # 将旧对话 (user_id=0) 迁移到第一个用户
            try:
                first_user = conn.execute("SELECT MIN(id) as uid FROM users").fetchone()
                if first_user and first_user["uid"]:
                    conn.execute("UPDATE conversations SET user_id=? WHERE user_id=0", (first_user["uid"],))
            except Exception:
                pass

    def sync_accounts(self, preset_accounts):
        with self._get_conn() as conn:
            for username, pw_hash in preset_accounts.items():
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