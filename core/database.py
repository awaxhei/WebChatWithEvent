"""
ChatDB: 数据库初始化、迁移、CRUD 操作封装
"""
import os, sqlite3, time, uuid, hashlib, json
from threading import Lock


# 数据库位置：电脑上默认项目目录下的 data/chat.db；
# 内嵌进 APK 时由 mobile_boot 用环境变量指到应用私有存储。
DB_PATH = os.environ.get("YUXIA_DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "chat.db")


class ChatDB:
    _lock = Lock()

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        # 某些受限环境（沙箱/容器）里 SQLite 无法自己创建新文件，
        # 先用 Python 建一个空文件，SQLite 即可正常初始化。
        if not os.path.exists(DB_PATH):
            with open(DB_PATH, "ab"):
                pass
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
                    channel         TEXT DEFAULT 'chat',
                    scene           TEXT DEFAULT '',
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
                    tier            TEXT DEFAULT '',
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    summary         TEXT NOT NULL,
                    start_msg_id    INTEGER NOT NULL,
                    end_msg_id      INTEGER NOT NULL,
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                -- v3：地点实体（每个会话一份地点表）
                CREATE TABLE IF NOT EXISTS locations (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    name            TEXT NOT NULL,
                    description     TEXT DEFAULT '',
                    variants        TEXT DEFAULT '{}',
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(conversation_id, name),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                -- v3：AI 小镇式记忆流（观察 / 反思 / 计划）
                CREATE TABLE IF NOT EXISTS memories (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    kind            TEXT NOT NULL DEFAULT 'observation'
                                    CHECK(kind IN ('observation','reflection','plan')),
                    text            TEXT NOT NULL,
                    importance      INTEGER DEFAULT 5,
                    story_minutes   INTEGER DEFAULT 0,
                    embedding       BLOB,
                    meta            TEXT DEFAULT '{}',
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memories_conv ON memories(conversation_id, id);
                CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(conversation_id, kind);

                -- v8：动态 NPC（煎饼摊老板这类只应付简单问答的角色）
                CREATE TABLE IF NOT EXISTS npcs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    name            TEXT NOT NULL,
                    role            TEXT DEFAULT '',
                    location        TEXT DEFAULT '',
                    persona         TEXT DEFAULT '',
                    status          TEXT DEFAULT 'active'
                                    CHECK(status IN ('active','gone')),
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    last_spoke_at   REAL DEFAULT 0,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_npcs_conv ON npcs(conversation_id, status);

                -- v11：地点里的实物（环境描写写到的道具要真的存在，并且可以被改动）
                CREATE TABLE IF NOT EXISTS props (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    location        TEXT NOT NULL,
                    name            TEXT NOT NULL,
                    state           TEXT DEFAULT '',
                    updated_at      TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(conversation_id, location, name),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_props_conv
                    ON props(conversation_id, location);

                -- v3：表情包索引（导入即用，缓存自动打标结果）
                CREATE TABLE IF NOT EXISTS stickers (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    path            TEXT UNIQUE NOT NULL,
                    filename        TEXT NOT NULL,
                    mtime           REAL DEFAULT 0,
                    size            INTEGER DEFAULT 0,
                    emotion         TEXT DEFAULT '',
                    tags            TEXT DEFAULT '',
                    caption         TEXT DEFAULT '',
                    tagged_by       TEXT DEFAULT '',
                    tag_attempts    INTEGER DEFAULT 0,
                    embedding       BLOB,
                    updated_at      TEXT DEFAULT (datetime('now','localtime'))
                );
            """)

    def migrate(self):
        with self._get_conn() as conn:
            # 老库补列
            for col, ddl in [
                ("story_time", "TEXT DEFAULT ''"),
                ("events_initialized", "INTEGER DEFAULT 0"),
                ("user_id", "INTEGER DEFAULT 0"),
                # v3：确定性故事时钟 + 尤夏所在地点
                ("story_minutes", "INTEGER DEFAULT 0"),
                ("current_location", "TEXT DEFAULT ''"),
                # v4：玩家位置 / 自然情境缓存 / 手机已读位置 / 最后发言时间
                ("player_location", "TEXT DEFAULT ''"),
                ("context_brief", "TEXT DEFAULT ''"),
                ("context_brief_turn", "INTEGER DEFAULT 0"),
                ("phone_read_id", "INTEGER DEFAULT 0"),
                ("last_user_at", "REAL DEFAULT 0"),
                # v5：她此刻正在做的事（自主行动 act 写入，喂给情境/对话提示词）
                ("yuxia_activity", "TEXT DEFAULT ''"),
                ("yuxia_activity_at", "INTEGER DEFAULT 0"),
                # v5：自主行动流水（防重复、算连续次数），JSON 数组
                ("autonomy_tail", "TEXT DEFAULT '[]'"),
                # v7：暂停自动推进（她不再自主行动、世界引擎停转）
                ("paused", "INTEGER DEFAULT 0"),
                # v7：她最后一次说话的时刻与通道（决定她还该等玩家多久）
                ("last_yuxia_at", "REAL DEFAULT 0"),
                ("last_yuxia_channel", "TEXT DEFAULT ''"),
                # v7：玩家平均多久回一句（秒，指数滑动平均），用来自适应等待时长
                ("reply_latency", "REAL DEFAULT 0"),
                # v8：她此刻的心情（界面头像用的颜文字由它决定）
                ("yuxia_mood", "TEXT DEFAULT ''"),
                # v8：玩家可调的行动速度 1..5（乘在等待期上，上限 20 秒）
                ("speed", "INTEGER DEFAULT 3"),
                # v9：她此刻的状态（清醒 / 睡眠 / 迷糊 / 兴奋 / 饥饿 / 饱腹 / 忙碌 / 感冒 / 发烧）
                # 睡眠时她不会有反应；不同状态说话风格不同
                ("yuxia_state", "TEXT DEFAULT ''"),
                # 这个状态是第几分钟进入的：随机事件带来的状态（忙碌/感冒/发烧）按它算过期
                ("yuxia_state_at", "INTEGER DEFAULT 0"),
                # v10：故事检查点（把掉出窗口的旧对话压缩成一份档案）+ 已压缩到第几条消息
                ("context_checkpoint", "TEXT DEFAULT ''"),
                ("checkpoint_upto", "INTEGER DEFAULT 0"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {ddl}")
                except sqlite3.OperationalError:
                    pass

            for table, col, ddl in [
                ("events", "story_time", "TEXT DEFAULT ''"),
                ("events", "tier", "TEXT DEFAULT ''"),
                ("messages", "channel", "TEXT DEFAULT 'chat'"),
                ("messages", "scene", "TEXT DEFAULT ''"),
                # v12：动作/消息发生在故事的第几分钟（动作要有时间，不然只是嘴上说说）
                ("messages", "story_minutes", "INTEGER DEFAULT 0"),
                # v13：故事时钟锚定的真实时刻（unix 秒）：时间从此按真实世界流
                ("conversations", "story_at", "REAL DEFAULT 0"),
                # v14：玩家最后一次出现在前台的真实时刻（离线结算按它算离开了多久）
                ("conversations", "last_seen_at", "REAL DEFAULT 0"),
                ("locations", "description", "TEXT DEFAULT ''"),
                ("locations", "variants", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                except sqlite3.OperationalError:
                    pass

            # v7：早期自主行动事件没有 tier 标记，按"misc + completed"回填成 self，
            # 否则她自己的动作会被当成"世界发生的事"喂回给她（AI C 产出的 misc 都是 active）
            try:
                conn.execute("UPDATE events SET tier='self'"
                             " WHERE event_type='misc' AND status='completed'"
                             " AND (tier IS NULL OR tier='')")
            except Exception:
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
            rows = conn.execute("SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id ASC", (conv_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_messages_since(self, conv_id, since_id):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, role, content, created_at FROM messages WHERE conversation_id=? AND id > ? ORDER BY id ASC", (conv_id, since_id)).fetchall()
        return [dict(r) for r in rows]

    def get_last_message_id(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT MAX(id) as mid FROM messages WHERE conversation_id=?", (conv_id,)).fetchone()
        return row["mid"] if row and row["mid"] else 0

    def get_message_count(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM messages WHERE conversation_id=? AND role='user'", (conv_id,)).fetchone()
        return row["cnt"] if row else 0

    def get_total_message_count(self, conv_id):
        """所有消息（含她的旁白、自主行动）：用来判断"情境摘要"该不该重写

        只数玩家消息会误判：玩家可能很久才说一句，中间她已经自言自语好几轮，
        摘要却还停在开头那几句上（试玩里出现过"你刚才叫了尤夏的名字"这种陈年摘要）。
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE conversation_id=?",
                               (conv_id,)).fetchone()
        return int(row["c"]) if row else 0

    # ----- 环境叙述 -----
    def add_atmosphere(self, conv_id, content):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO atmosphere(conversation_id, content) VALUES(?,?)", (conv_id, content))

    def get_atmosphere(self, conv_id, limit=5):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT content FROM atmosphere WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conv_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ----- 事件 -----
    def add_event(self, conv_id, event_type, title, description, status="active", story_time="", tier=""):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO events(conversation_id, event_type, title, description, status, story_time, tier)"
                         " VALUES(?,?,?,?,?,?,?)",
                         (conv_id, event_type, title, description, status, story_time, tier))

    def get_events(self, conv_id, limit=30):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, event_type, title, description, status, story_time, tier, created_at"
                                " FROM events WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
                                (conv_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_recent_events(self, conv_id, limit=20):
        """最近 N 条事件（按时间正序返回）。

        get_events 是 ORDER BY id ASC LIMIT N，取的是**最早**的 N 条；
        事件超过 N 条之后，新发生的随机事件就再也进不了上下文，
        她会一直读不到眼前刚发生的事。
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, event_type, title, description, status, story_time, tier, created_at"
                                " FROM events WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                                (conv_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

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

    # ----- 对话摘要 -----
    def add_summary(self, conv_id, summary, start_msg_id, end_msg_id):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO conversation_summaries(conversation_id, summary, start_msg_id, end_msg_id) VALUES(?,?,?,?)",
                         (conv_id, summary, start_msg_id, end_msg_id))

    def get_latest_summaries(self, conv_id, limit=3):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT summary, end_msg_id FROM conversation_summaries WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conv_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_last_summary_end_id(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT MAX(end_msg_id) as eid FROM conversation_summaries WHERE conversation_id=?", (conv_id,)).fetchone()
        return row["eid"] if row and row["eid"] else 0

    def clear_summaries(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM conversation_summaries WHERE conversation_id=?", (conv_id,))

    # ----- v3：故事时钟 -----
    def get_story_minutes(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT story_minutes FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if not row:
            return None
        return row["story_minutes"] or 0

    def set_story_minutes(self, conv_id, minutes):
        """写故事时间的同时，记下对应的真实时刻（时间锚点）"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE conversations SET story_minutes=?, story_at=? WHERE id=?",
                (int(minutes), time.time(), conv_id))

    def touch_last_seen(self, conv_id, ts=None):
        """玩家在界面上出现了一次（说话、开界面、刷新场景都算）"""
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET last_seen_at=? WHERE id=?",
                         (float(ts if ts is not None else time.time()), conv_id))

    def get_last_seen_at(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT last_seen_at FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
        return float(row["last_seen_at"] or 0) if row else 0.0

    def get_story_anchor(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT story_minutes, story_at FROM conversations WHERE id=?",
                (conv_id,)).fetchone()
        if not row:
            return 0, 0.0
        return int(row["story_minutes"] or 0), float(row["story_at"] or 0)

    # ----- v3：地点 -----
    def add_location(self, conv_id, name, description=""):
        with self._get_conn() as conn:
            try:
                conn.execute("INSERT OR IGNORE INTO locations(conversation_id, name, description) VALUES(?,?,?)",
                             (conv_id, name, description))
            except sqlite3.Error:
                pass

    def get_location_names(self, conv_id):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT name FROM locations WHERE conversation_id=? ORDER BY id ASC", (conv_id,)).fetchall()
        return [r["name"] for r in rows]

    def get_current_location(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT current_location FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return (row["current_location"] or "") if row else ""

    def set_current_location(self, conv_id, name):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET current_location=? WHERE id=?", (name, conv_id))

    def clear_locations(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM locations WHERE conversation_id=?", (conv_id,))

    # ----- v3：记忆流 -----
    def add_memory(self, conv_id, kind, text, importance, story_minutes, embedding=None, meta="{}"):
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO memories(conversation_id, kind, text, importance, story_minutes, embedding, meta)"
                " VALUES(?,?,?,?,?,?,?)",
                (conv_id, kind, text, int(importance), int(story_minutes), embedding, meta))
            return cur.lastrowid

    def get_memories(self, conv_id, limit=600):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, kind, text, importance, story_minutes, embedding FROM memories"
                " WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conv_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_observations_since(self, conv_id, since_id, limit=20):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, text, importance, story_minutes FROM memories"
                " WHERE conversation_id=? AND kind='observation' AND id > ? ORDER BY id ASC LIMIT ?",
                (conv_id, since_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_reflection(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, text, importance, story_minutes, meta FROM memories"
                " WHERE conversation_id=? AND kind='reflection' ORDER BY id DESC LIMIT 1", (conv_id,)).fetchone()
        return dict(row) if row else None

    def get_observation_importance_sum(self, conv_id, since_id=0):
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(importance),0) AS s FROM memories"
                " WHERE conversation_id=? AND kind='observation' AND id > ?", (conv_id, since_id)).fetchone()
        return int(row["s"]) if row else 0

    def count_memories(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM memories WHERE conversation_id=?", (conv_id,)).fetchone()
        return int(row["c"]) if row else 0

    def delete_memories(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM memories WHERE conversation_id=?", (conv_id,))

    # ----- v3：表情包索引 -----
    def get_sticker(self, path):
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM stickers WHERE path=?", (path,)).fetchone()
        return dict(row) if row else None

    def upsert_sticker(self, path, filename, mtime, size):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO stickers(path, filename, mtime, size) VALUES(?,?,?,?)"
                " ON CONFLICT(path) DO UPDATE SET filename=excluded.filename,"
                " mtime=excluded.mtime, size=excluded.size,"
                " emotion='', tags='', caption='', tagged_by='', tag_attempts=0, embedding=NULL",
                (path, filename, mtime, size))

    def get_all_stickers(self):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM stickers ORDER BY path ASC").fetchall()
        return [dict(r) for r in rows]

    def get_untagged_stickers(self, limit=200):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stickers WHERE tagged_by='' AND tag_attempts < 3 ORDER BY id ASC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_sticker_tags(self, sticker_id, emotion, tags, caption, source):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE stickers SET emotion=?, tags=?, caption=?, tagged_by=?,"
                " tag_attempts=tag_attempts+1, updated_at=datetime('now','localtime') WHERE id=?",
                (emotion, tags, caption, source, sticker_id))

    def bump_sticker_attempts(self, sticker_id):
        with self._get_conn() as conn:
            conn.execute("UPDATE stickers SET tag_attempts=tag_attempts+1 WHERE id=?", (sticker_id,))

    def reset_sticker_tags(self, sticker_id):
        """清掉一条标签，让它重新排队（用于修掉早期从文件名凑出来的垃圾标签）"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE stickers SET emotion='', tags='', caption='', tagged_by='',"
                " embedding=NULL WHERE id=?", (sticker_id,))

    def get_stickers_without_embedding(self, limit=200):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stickers WHERE embedding IS NULL AND tagged_by != '' LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_sticker_embedding(self, sticker_id, blob):
        with self._get_conn() as conn:
            conn.execute("UPDATE stickers SET embedding=? WHERE id=?", (blob, sticker_id))

    def delete_stickers_not_in(self, paths):
        if not paths:
            return 0
        with self._get_conn() as conn:
            marks = ",".join("?" * len(paths))
            cur = conn.execute(f"DELETE FROM stickers WHERE path NOT IN ({marks})", tuple(paths))
            return cur.rowcount or 0

    def count_stickers(self, tagged_only=False):
        sql = "SELECT COUNT(*) AS c FROM stickers"
        if tagged_only:
            sql += " WHERE tagged_by != ''"
        with self._get_conn() as conn:
            row = conn.execute(sql).fetchone()
        return int(row["c"]) if row else 0

    def get_sticker_emotions(self):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT emotion FROM stickers WHERE emotion != '' ORDER BY emotion").fetchall()
        return [r["emotion"] for r in rows]

    # ----- v4：双方位置 -----
    def get_player_location(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT player_location FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return (row["player_location"] or "") if row else ""

    def set_player_location(self, conv_id, name):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET player_location=? WHERE id=?", (name, conv_id))

    # ----- v4：场景描写缓存（时段 → 描写） -----
    def get_location_variants(self, conv_id, name):
        with self._get_conn() as conn:
            row = conn.execute("SELECT variants FROM locations WHERE conversation_id=? AND name=?",
                               (conv_id, name)).fetchone()
        if not row or not row["variants"]:
            return {}
        try:
            data = json.loads(row["variants"])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def set_location_variant(self, conv_id, name, bucket, text):
        variants = self.get_location_variants(conv_id, name)
        variants[bucket] = text
        with self._get_conn() as conn:
            conn.execute("UPDATE locations SET variants=? WHERE conversation_id=? AND name=?",
                         (json.dumps(variants, ensure_ascii=False), conv_id, name))

    # ----- v4：自然情境缓存 -----
    def get_context_brief(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT context_brief, context_brief_turn FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
        return ((row["context_brief"] or ""), (row["context_brief_turn"] or 0)) if row else ("", 0)

    def set_context_brief(self, conv_id, brief, turn):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET context_brief=?, context_brief_turn=? WHERE id=?",
                         (brief, int(turn), conv_id))

    # ----- v11：地点里的实物 -----
    def get_props(self, conv_id, location, limit=12):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT name, state FROM props WHERE conversation_id=? AND location=?"
                " ORDER BY id ASC LIMIT ?",
                (conv_id, location, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def set_prop(self, conv_id, location, name, state):
        """记一样东西的状态（同名就更新）"""
        name = (name or "").strip()[:16]
        if not name or not location:
            return None
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO props(conversation_id, location, name, state) VALUES(?,?,?,?)"
                " ON CONFLICT(conversation_id, location, name)"
                " DO UPDATE SET state=excluded.state, updated_at=datetime('now','localtime')",
                (conv_id, location, name, (state or "").strip()[:40]))
        return name

    def get_prop_names(self, conv_id, location):
        return [p["name"] for p in self.get_props(conv_id, location)]

    # ----- v10：故事检查点（旧对话压缩） -----
    def get_checkpoint(self, conv_id):
        """返回 (检查点正文, 已覆盖到的消息 id)"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT context_checkpoint, checkpoint_upto FROM conversations WHERE id=?",
                (conv_id,)).fetchone()
        return ((row["context_checkpoint"] or ""), int(row["checkpoint_upto"] or 0)) if row else ("", 0)

    def set_checkpoint(self, conv_id, text, upto_id):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET context_checkpoint=?, checkpoint_upto=? WHERE id=?",
                         (text or "", int(upto_id or 0), conv_id))

    # ----- v4：消息通道（chat / phone） -----
    def add_message(self, conv_id, role, content, channel="chat", scene="", story_minutes=0):
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages(conversation_id, role, content, channel, scene,"
                " story_minutes) VALUES(?,?,?,?,?,?)",
                (conv_id, role, content, channel, scene, int(story_minutes or 0)))
            return cur.lastrowid

    def get_messages(self, conv_id, channel=None):
        with self._get_conn() as conn:
            if channel:
                rows = conn.execute(
                    "SELECT id, role, content, channel, scene, story_minutes, created_at"
                    " FROM messages"
                    " WHERE conversation_id=? AND channel=? ORDER BY id ASC",
                    (conv_id, channel)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, role, content, channel, scene, story_minutes, created_at"
                    " FROM messages WHERE conversation_id=? ORDER BY id ASC",
                    (conv_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_phone_messages(self, conv_id, since_id=0, limit=200):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, role, content, created_at FROM messages"
                " WHERE conversation_id=? AND channel='phone' AND id > ?"
                " ORDER BY id ASC LIMIT ?", (conv_id, since_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_phone_unread(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM messages"
                " WHERE conversation_id=? AND channel='phone' AND role='assistant'"
                " AND id > COALESCE((SELECT phone_read_id FROM conversations WHERE id=?),0)",
                (conv_id, conv_id)).fetchone()
        return int(row["c"]) if row else 0

    def mark_phone_read(self, conv_id):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE conversations SET phone_read_id = COALESCE("
                "(SELECT MAX(id) FROM messages WHERE conversation_id=?),0) WHERE id=?",
                (conv_id, conv_id))

    # ----- v4：玩家最后发言时间（用于"闲置"自主行动） -----
    def set_last_user_at(self, conv_id, ts):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET last_user_at=? WHERE id=?", (float(ts), conv_id))

    def get_last_user_at(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT last_user_at FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return float(row["last_user_at"] or 0) if row else 0.0

    # ----- v5：尤夏此刻的活动 + 自主行动流水 -----
    def set_yuxia_activity(self, conv_id, text, story_minutes=0):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET yuxia_activity=?, yuxia_activity_at=? WHERE id=?",
                         (text or "", int(story_minutes or 0), conv_id))

    def get_yuxia_activity(self, conv_id):
        """她此刻在做的事；太久以前的（超过一个时段）就当已经做完了"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT yuxia_activity, yuxia_activity_at FROM conversations WHERE id=?",
                (conv_id,)).fetchone()
        if not row:
            return ("", 0)
        return ((row["yuxia_activity"] or ""), int(row["yuxia_activity_at"] or 0))

    def clear_yuxia_activity(self, conv_id):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET yuxia_activity='', yuxia_activity_at=0 WHERE id=?",
                         (conv_id,))

    def get_autonomy_tail(self, conv_id, limit=8):
        with self._get_conn() as conn:
            row = conn.execute("SELECT autonomy_tail FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if not row or not row["autonomy_tail"]:
            return []
        try:
            data = json.loads(row["autonomy_tail"])
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def push_autonomy(self, conv_id, entry, keep=16):
        """记录一次自主行动（action/text/at），用于防重复与连续次数统计"""
        tail = self.get_autonomy_tail(conv_id, keep)
        tail.append(entry)
        tail = tail[-keep:]
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET autonomy_tail=? WHERE id=?",
                         (json.dumps(tail, ensure_ascii=False), conv_id))
        return tail

    # ----- v7：暂停自动推进 -----
    def set_paused(self, conv_id, paused):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET paused=? WHERE id=?",
                         (1 if paused else 0, conv_id))

    def is_paused(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT paused FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return bool(row and row["paused"])

    # ----- v8：她的心情（头像用）与行动速度 -----
    def set_yuxia_mood(self, conv_id, mood):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET yuxia_mood=? WHERE id=?",
                         (str(mood or "")[:8], conv_id))

    def get_yuxia_mood(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT yuxia_mood FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
        return (row["yuxia_mood"] or "") if row else ""

    def set_speed(self, conv_id, speed):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET speed=? WHERE id=?",
                         (int(speed), conv_id))

    def get_speed(self, conv_id, default=3):
        with self._get_conn() as conn:
            row = conn.execute("SELECT speed FROM conversations WHERE id=?", (conv_id,)).fetchone()
        value = row["speed"] if row else None
        return int(value or default)

    # ----- v7：她最后一次说话（用于"先等玩家，等不及了才行动"） -----
    # ----- v9：她的状态（清醒 / 睡眠 / 迷糊 / 兴奋 / 饥饿 / 饱腹）-----
    def set_yuxia_state(self, conv_id, state):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET yuxia_state=? WHERE id=?", (state, conv_id))

    def get_yuxia_state(self, conv_id, default=""):
        with self._get_conn() as conn:
            row = conn.execute("SELECT yuxia_state FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
        return ((row["yuxia_state"] or default) if row else default)

    def set_yuxia_state_at(self, conv_id, minutes):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET yuxia_state_at=? WHERE id=?",
                         (int(minutes), conv_id))

    def get_yuxia_state_at(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT yuxia_state_at FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
        return int(row["yuxia_state_at"] or 0) if row else 0

    def set_last_yuxia_at(self, conv_id, ts, channel="chat"):
        with self._get_conn() as conn:
            conn.execute("UPDATE conversations SET last_yuxia_at=?, last_yuxia_channel=? WHERE id=?",
                         (float(ts), channel or "chat", conv_id))

    def get_last_yuxia(self, conv_id):
        """返回 (最后一次说话的墙钟时间, 通道)"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_yuxia_at, last_yuxia_channel FROM conversations WHERE id=?",
                (conv_id,)).fetchone()
        if not row:
            return (0.0, "")
        return (float(row["last_yuxia_at"] or 0), row["last_yuxia_channel"] or "")

    # ----- v7：玩家的回复速度（自适应等待时长用）-----
    def update_reply_latency(self, conv_id, seconds, alpha=0.4):
        """把一次"她说完 → 玩家开回"的间隔并进滑动平均"""
        seconds = max(1.0, min(float(seconds), 3600.0))
        with self._get_conn() as conn:
            row = conn.execute("SELECT reply_latency FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
            old = float(row["reply_latency"] or 0) if row else 0.0
            value = seconds if old <= 0 else (old * (1 - alpha) + seconds * alpha)
            conn.execute("UPDATE conversations SET reply_latency=? WHERE id=?",
                         (round(value, 2), conv_id))
        return value

    def get_reply_latency(self, conv_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT reply_latency FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
        return float(row["reply_latency"] or 0) if row else 0.0

    # ----- v8：动态 NPC -----
    def add_npc(self, conv_id, name, role="", location="", persona=""):
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO npcs(conversation_id, name, role, location, persona)"
                " VALUES(?,?,?,?,?)", (conv_id, name, role, location, persona))
            return cur.lastrowid

    def get_npcs(self, conv_id, status="active", location=None):
        with self._get_conn() as conn:
            if location:
                rows = conn.execute(
                    "SELECT * FROM npcs WHERE conversation_id=? AND status=? AND location=?"
                    " ORDER BY id ASC", (conv_id, status, location)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM npcs WHERE conversation_id=? AND status=?"
                    " ORDER BY id ASC", (conv_id, status)).fetchall()
        return [dict(r) for r in rows]

    def get_npc(self, npc_id):
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM npcs WHERE id=?", (npc_id,)).fetchone()
        return dict(row) if row else None

    def find_npc(self, conv_id, name, location=None):
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM npcs WHERE conversation_id=? AND status='active' AND name=?",
                (conv_id, name)).fetchall()
        for r in rows:
            if location is None or r["location"] == location:
                return dict(r)
        return None

    def set_npc_status(self, npc_id, status):
        with self._get_conn() as conn:
            conn.execute("UPDATE npcs SET status=? WHERE id=?", (status, npc_id))

    def set_npc_location(self, npc_id, location):
        with self._get_conn() as conn:
            conn.execute("UPDATE npcs SET location=? WHERE id=?", (location, npc_id))

    def touch_npc(self, npc_id, ts):
        with self._get_conn() as conn:
            conn.execute("UPDATE npcs SET last_spoke_at=? WHERE id=?", (float(ts), npc_id))

    def count_npcs(self, conv_id, status="active"):
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM npcs WHERE conversation_id=? AND status=?",
                               (conv_id, status)).fetchone()
        return int(row["c"]) if row else 0
