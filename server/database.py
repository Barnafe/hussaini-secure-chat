"""Database layer (SQLite).

One file on disk (``data/chat.db`` by default). All methods are synchronous and
thread-safe: the server runs one thread per client, and every database call is
serialised with a lock, which is plenty for a chat of this size.

Messages are only ever stored as ciphertext (``encrypted_message``), passwords
only as bcrypt hashes, and each user's *public* key (never a private key or
passphrase) is stored so friends can encrypt for each other.
"""
import hashlib
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


class UserExistsError(Exception):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def _ts(value):
    """Timestamps are stored as text; keep them as text."""
    return None if value is None else str(value)


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        fullname TEXT NOT NULL,
        public_key TEXT NOT NULL DEFAULT '',
        bio TEXT NOT NULL DEFAULT '',
        avatar TEXT NOT NULL DEFAULT '',
        avatar_ver TEXT NOT NULL DEFAULT '',
        date_created DATETIME NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT NOT NULL,
        receiver TEXT NOT NULL,
        encrypted_message TEXT NOT NULL,
        timestamp DATETIME NOT NULL,
        delivered INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages (receiver, delivered)",
    "CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages (sender, receiver)",
    """CREATE TABLE IF NOT EXISTS friend_requests (
        request_id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user TEXT NOT NULL,
        to_user TEXT NOT NULL,
        created DATETIME NOT NULL,
        UNIQUE (from_user, to_user))""",
    """CREATE TABLE IF NOT EXISTS friendships (
        user_a TEXT NOT NULL,
        user_b TEXT NOT NULL,
        created DATETIME NOT NULL,
        PRIMARY KEY (user_a, user_b))""",
    """CREATE TABLE IF NOT EXISTS chat_groups (
        group_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        admin TEXT NOT NULL,
        created DATETIME NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS group_members (
        group_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        first_visible_id INTEGER NOT NULL DEFAULT 0,
        last_delivered_id INTEGER NOT NULL DEFAULT 0,
        joined DATETIME NOT NULL,
        PRIMARY KEY (group_id, username))""",
    """CREATE TABLE IF NOT EXISTS group_messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        sender TEXT NOT NULL,
        encrypted_message TEXT NOT NULL,
        timestamp DATETIME NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_group_messages_group ON group_messages (group_id, message_id)",
    """CREATE TABLE IF NOT EXISTS call_history (
        call_id TEXT PRIMARY KEY,
        caller TEXT NOT NULL,
        callee TEXT NOT NULL,
        media TEXT NOT NULL,
        status TEXT NOT NULL,
        started TEXT NOT NULL,
        answered TEXT,
        ended TEXT,
        duration INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_call_history_users ON call_history (caller, callee, started)",
]


class Database:
    def __init__(self, sqlite_path: str = "data/chat.db"):
        self._path = sqlite_path
        self._lock = threading.RLock()
        folder = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(folder, exist_ok=True)
        self._conn = sqlite3.connect(self._path, timeout=15, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self.init_schema()

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    @property
    def backend(self) -> str:
        return "sqlite"

    @property
    def path(self) -> str:
        return self._path

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def _tx(self):
        """One locked transaction: commit on success, roll back on error."""
        with self._lock:
            conn = self._conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _q(conn, sql, params=()):
        return conn.execute(sql, params)

    def _all(self, conn, sql, params=()):
        return [dict(r) for r in self._q(conn, sql, params).fetchall()]

    def _one(self, conn, sql, params=()):
        row = self._q(conn, sql, params).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _insert(conn, sql, params, _id_col=None):
        return conn.execute(sql, params).lastrowid

    def init_schema(self):
        with self._tx() as conn:
            for stmt in SCHEMA:
                self._q(conn, stmt)
            columns = [r["name"] for r in self._q(conn, "PRAGMA table_info(users)").fetchall()]
            if "public_key" not in columns:              # database made by an older version
                self._q(conn, "ALTER TABLE users ADD COLUMN public_key TEXT NOT NULL DEFAULT ''")
            for name in ("bio", "avatar", "avatar_ver"):   # profile fields (added in v6)
                if name not in columns:
                    self._q(conn, f"ALTER TABLE users ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            call_columns = [r["name"] for r in self._q(conn, "PRAGMA table_info(call_history)").fetchall()]
            if "answered" not in call_columns:             # call log made by the first calling build
                self._q(conn, "ALTER TABLE call_history ADD COLUMN answered TEXT")

    # ------------------------------------------------------------------ #
    # users
    # ------------------------------------------------------------------ #
    def create_user(self, username, pw_hash, fullname, public_key=""):
        with self._tx() as conn:
            if self._one(conn, "SELECT user_id FROM users WHERE username = ?", (username,)):
                raise UserExistsError(username)
            try:
                uid = self._insert(
                    conn,
                    "INSERT INTO users (username, password, fullname, public_key, date_created) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (username, pw_hash, fullname, public_key, utcnow()), "user_id")
            except Exception as exc:                     # race: duplicate inserted meanwhile
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    raise UserExistsError(username) from exc
                raise
        return {"user_id": uid, "username": username, "fullname": fullname, "public_key": public_key}

    def get_user(self, username):
        with self._tx() as conn:
            row = self._one(conn, "SELECT * FROM users WHERE username = ?", (username,))
        if row:
            row["date_created"] = _ts(row["date_created"])
        return row

    # ---- profile (name, status line, picture) ---------------------------- #
    def update_profile(self, username, fullname, bio):
        with self._tx() as conn:
            self._q(conn, "UPDATE users SET fullname = ?, bio = ? WHERE username = ?",
                    (fullname, bio, username))

    def set_avatar(self, username, data):
        """Store a picture (base64 text) or clear it with ''. Returns the new version tag."""
        ver = hashlib.sha1(data.encode("ascii")).hexdigest()[:10] if data else ""
        with self._tx() as conn:
            self._q(conn, "UPDATE users SET avatar = ?, avatar_ver = ? WHERE username = ?",
                    (data, ver, username))
        return ver

    def get_avatar(self, username):
        """(version, base64 data) of a user's picture; ('', '') when there is none."""
        with self._tx() as conn:
            row = self._one(conn, "SELECT avatar, avatar_ver FROM users WHERE username = ?", (username,))
        return (row["avatar_ver"], row["avatar"]) if row else ("", "")

    def set_password(self, username, pw_hash):
        with self._tx() as conn:
            self._q(conn, "UPDATE users SET password = ? WHERE username = ?", (pw_hash, username))

    # ------------------------------------------------------------------ #
    # friends
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pair(a, b):
        return (a, b) if a < b else (b, a)

    def add_friend_request(self, from_user, to_user):
        with self._tx() as conn:
            self._q(conn, "INSERT INTO friend_requests (from_user, to_user, created) VALUES (?, ?, ?) "
                          "ON CONFLICT (from_user, to_user) DO NOTHING",
                    (from_user, to_user, utcnow()))

    def has_request(self, from_user, to_user):
        with self._tx() as conn:
            return self._one(conn, "SELECT request_id FROM friend_requests WHERE from_user = ? AND to_user = ?",
                             (from_user, to_user)) is not None

    def delete_request(self, from_user, to_user):
        with self._tx() as conn:
            self._q(conn, "DELETE FROM friend_requests WHERE from_user = ? AND to_user = ?",
                    (from_user, to_user))

    def incoming_requests(self, user):
        with self._tx() as conn:
            return self._all(conn,
                             "SELECT u.username, u.fullname, u.avatar_ver FROM friend_requests r "
                             "JOIN users u ON u.username = r.from_user "
                             "WHERE r.to_user = ? ORDER BY r.request_id", (user,))

    def outgoing_requests(self, user):
        with self._tx() as conn:
            rows = self._all(conn, "SELECT to_user FROM friend_requests WHERE from_user = ? ORDER BY request_id",
                             (user,))
        return [r["to_user"] for r in rows]

    def make_friends(self, a, b):
        x, y = self._pair(a, b)
        with self._tx() as conn:
            self._q(conn, "DELETE FROM friend_requests WHERE (from_user = ? AND to_user = ?) "
                          "OR (from_user = ? AND to_user = ?)", (a, b, b, a))
            self._q(conn, "INSERT INTO friendships (user_a, user_b, created) VALUES (?, ?, ?) "
                          "ON CONFLICT (user_a, user_b) DO NOTHING", (x, y, utcnow()))

    # ------------------------------------------------------------------ #
    # call log
    # ------------------------------------------------------------------ #
    def create_call(self, call_id, caller, callee, media, status="ringing"):
        """Log a call. Anything but "ringing" is already over (busy / offline): it is closed at once."""
        now = utcnow()
        with self._tx() as conn:
            self._q(conn, "INSERT INTO call_history(call_id,caller,callee,media,status,started,ended,duration) "
                          "VALUES(?,?,?,?,?,?,?,0)",
                    (call_id, caller, callee, media, status, now, None if status == "ringing" else now))

    def mark_call_answered(self, call_id):
        with self._tx() as conn:
            self._q(conn, "UPDATE call_history SET answered=? WHERE call_id=? AND ended IS NULL AND answered IS NULL",
                    (utcnow(), call_id))

    _DURATION_SQL = ("CASE WHEN answered IS NULL THEN 0 ELSE "
                     "MAX(0, CAST(strftime('%s', ?) AS INTEGER) - CAST(strftime('%s', answered) AS INTEGER)) END")

    def finish_call(self, call_id, status):
        """Close a call. The duration is the talk time (answer -> end); ringing time never counts."""
        with self._tx() as conn:
            now = utcnow()
            self._q(conn, f"UPDATE call_history SET status=?, ended=?, duration={self._DURATION_SQL} "
                          "WHERE call_id=? AND ended IS NULL", (status, now, now, call_id))

    def close_stale_calls(self):
        """After a crash or restart no call can still be going on: close every call left open."""
        with self._tx() as conn:
            now = utcnow()
            self._q(conn, f"UPDATE call_history SET ended=?, duration={self._DURATION_SQL}, "
                          "status=CASE WHEN answered IS NULL THEN 'no_answer' ELSE 'connection_lost' END "
                          "WHERE ended IS NULL", (now, now))

    def calls_for_user(self, username, limit=100):
        with self._tx() as conn:
            return self._all(conn, "SELECT call_id,caller,callee,media,status,started,answered,ended,duration "
                                   "FROM call_history WHERE caller=? OR callee=? "
                                   "ORDER BY started DESC, rowid DESC LIMIT ?",
                             (username, username, max(1, min(int(limit), 200))))

    def are_friends(self, a, b):
        x, y = self._pair(a, b)
        with self._tx() as conn:
            return self._one(conn, "SELECT user_a FROM friendships WHERE user_a = ? AND user_b = ?",
                             (x, y)) is not None

    def remove_friendship(self, a, b):
        x, y = self._pair(a, b)
        with self._tx() as conn:
            self._q(conn, "DELETE FROM friendships WHERE user_a = ? AND user_b = ?", (x, y))

    def update_password(self, username, pw_hash):
        with self._tx() as conn:
            self._q(conn, "UPDATE users SET password = ? WHERE username = ?", (pw_hash, username))

    def last_direct_messages(self, user):
        """{peer: newest message row between `user` and that peer} - used for chat-list previews."""
        with self._tx() as conn:
            rows = self._all(
                conn,
                "SELECT m.* FROM messages m JOIN ("
                "SELECT CASE WHEN sender = ? THEN receiver ELSE sender END AS peer, MAX(message_id) AS mid "
                "FROM messages WHERE sender = ? OR receiver = ? GROUP BY peer) x ON m.message_id = x.mid",
                (user, user, user))
        out = {}
        for r in rows:
            r["timestamp"] = _ts(r["timestamp"])
            out[r["receiver"] if r["sender"] == user else r["sender"]] = r
        return out

    def last_group_messages(self, user):
        """{group_id: newest message this member is allowed to see}."""
        with self._tx() as conn:
            rows = self._all(
                conn,
                "SELECT gm2.* FROM group_messages gm2 JOIN group_members m "
                "ON m.group_id = gm2.group_id AND m.username = ? "
                "WHERE gm2.message_id = (SELECT MAX(message_id) FROM group_messages g3 "
                "WHERE g3.group_id = gm2.group_id) AND gm2.message_id > m.first_visible_id", (user,))
        for r in rows:
            r["timestamp"] = _ts(r["timestamp"])
        return {r["group_id"]: r for r in rows}

    def rename_group(self, group_id, name):
        with self._tx() as conn:
            self._q(conn, "UPDATE chat_groups SET name = ? WHERE group_id = ?", (name, group_id))

    def user_summaries(self):
        """One row per account for the --list-users admin command."""
        with self._tx() as conn:
            return self._all(
                conn,
                "SELECT u.username, u.fullname, u.date_created, "
                "(SELECT COUNT(*) FROM friendships f WHERE f.user_a = u.username OR f.user_b = u.username) AS friends, "
                "(SELECT COUNT(*) FROM messages m WHERE m.sender = u.username) AS messages_sent "
                "FROM users u ORDER BY u.user_id")

    def backup_to(self, target_path):
        """Copy the whole database to `target_path` (safe while the server is running)."""
        folder = os.path.dirname(os.path.abspath(target_path))
        os.makedirs(folder, exist_ok=True)
        src = sqlite3.connect(self._path, timeout=15)
        dst = sqlite3.connect(target_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return target_path

    def search_users(self, exclude, query="", limit=100):
        """Users other than `exclude` whose username or full name contains `query` (case-insensitive).

        An empty query lists everybody. LIKE wildcards typed by the user are escaped, so a search
        for "50%" or "a_b" only matches those literal characters.
        """
        like = "%" + query.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._tx() as conn:
            return self._all(
                conn,
                "SELECT username, fullname, avatar_ver FROM users WHERE username != ? "
                "AND (LOWER(username) LIKE ? ESCAPE '\\' OR LOWER(fullname) LIKE ? ESCAPE '\\') "
                "ORDER BY LOWER(fullname), username LIMIT ?", (exclude, like, like, int(limit)))

    def friends_of(self, user):
        with self._tx() as conn:
            return self._all(conn,
                             "SELECT u.username, u.fullname, u.public_key, u.bio, u.avatar_ver FROM users u WHERE u.username IN ("
                             "SELECT user_b FROM friendships WHERE user_a = ? "
                             "UNION SELECT user_a FROM friendships WHERE user_b = ?) "
                             "ORDER BY u.fullname", (user, user))

    # ------------------------------------------------------------------ #
    # direct messages
    # ------------------------------------------------------------------ #
    def save_message(self, sender, receiver, encrypted):
        ts = utcnow()
        with self._tx() as conn:
            mid = self._insert(
                conn,
                "INSERT INTO messages (sender, receiver, encrypted_message, timestamp, delivered) "
                "VALUES (?, ?, ?, ?, 0)", (sender, receiver, encrypted, ts), "message_id")
        return mid, ts

    @staticmethod
    def _fmt_msg(row):
        row["timestamp"] = _ts(row["timestamp"])
        return row

    def undelivered_messages(self, receiver):
        with self._tx() as conn:
            rows = self._all(conn, "SELECT * FROM messages WHERE receiver = ? AND delivered = 0 "
                                   "ORDER BY message_id", (receiver,))
        return [self._fmt_msg(r) for r in rows]

    def mark_delivered(self, ids):
        if not ids:
            return
        with self._tx() as conn:
            for mid in ids:
                self._q(conn, "UPDATE messages SET delivered = 1 WHERE message_id = ?", (mid,))

    def direct_history(self, a, b, limit=100):
        with self._tx() as conn:
            rows = self._all(
                conn,
                "SELECT * FROM messages WHERE (sender = ? AND receiver = ?) OR (sender = ? AND receiver = ?) "
                "ORDER BY message_id DESC LIMIT ?", (a, b, b, a, int(limit)))
        rows.reverse()
        return [self._fmt_msg(r) for r in rows]

    # ------------------------------------------------------------------ #
    # groups
    # ------------------------------------------------------------------ #
    def _max_group_msg(self, conn):
        return self._one(conn, "SELECT COALESCE(MAX(message_id), 0) AS m FROM group_messages")["m"]

    def create_group(self, name, admin, members):
        """members: usernames to add besides the admin. Returns the group id."""
        with self._tx() as conn:
            gid = self._insert(conn, "INSERT INTO chat_groups (name, admin, created) VALUES (?, ?, ?)",
                               (name, admin, utcnow()), "group_id")
            m = self._max_group_msg(conn)
            for user in [admin] + [u for u in members if u != admin]:
                self._q(conn, "INSERT INTO group_members (group_id, username, first_visible_id, "
                              "last_delivered_id, joined) VALUES (?, ?, ?, ?, ?) "
                              "ON CONFLICT (group_id, username) DO NOTHING",
                        (gid, user, m, m, utcnow()))
        return gid

    def get_group(self, group_id):
        with self._tx() as conn:
            g = self._one(conn, "SELECT * FROM chat_groups WHERE group_id = ?", (group_id,))
            if not g:
                return None
            members = self._all(
                conn,
                "SELECT u.username, u.fullname, u.public_key, u.avatar_ver FROM group_members gm "
                "JOIN users u ON u.username = gm.username WHERE gm.group_id = ? ORDER BY gm.joined, u.username",
                (group_id,))
        return {"group_id": g["group_id"], "name": g["name"], "admin": g["admin"], "members": members}

    def groups_for_user(self, user):
        with self._tx() as conn:
            ids = [r["group_id"] for r in self._all(
                conn, "SELECT group_id FROM group_members WHERE username = ? ORDER BY group_id", (user,))]
        return [self.get_group(i) for i in ids]

    def group_member_names(self, group_id):
        with self._tx() as conn:
            rows = self._all(conn, "SELECT username FROM group_members WHERE group_id = ?", (group_id,))
        return [r["username"] for r in rows]

    def is_member(self, group_id, user):
        with self._tx() as conn:
            return self._one(conn, "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                             (group_id, user)) is not None

    def add_group_member(self, group_id, user):
        with self._tx() as conn:
            m = self._max_group_msg(conn)
            self._q(conn, "INSERT INTO group_members (group_id, username, first_visible_id, "
                          "last_delivered_id, joined) VALUES (?, ?, ?, ?, ?) "
                          "ON CONFLICT (group_id, username) DO NOTHING",
                    (group_id, user, m, m, utcnow()))

    def remove_group_member(self, group_id, user):
        with self._tx() as conn:
            self._q(conn, "DELETE FROM group_members WHERE group_id = ? AND username = ?", (group_id, user))

    def set_group_admin(self, group_id, user):
        with self._tx() as conn:
            self._q(conn, "UPDATE chat_groups SET admin = ? WHERE group_id = ?", (user, group_id))

    def delete_group(self, group_id):
        with self._tx() as conn:
            self._q(conn, "DELETE FROM group_messages WHERE group_id = ?", (group_id,))
            self._q(conn, "DELETE FROM group_members WHERE group_id = ?", (group_id,))
            self._q(conn, "DELETE FROM chat_groups WHERE group_id = ?", (group_id,))

    def save_group_message(self, group_id, sender, encrypted):
        ts = utcnow()
        with self._tx() as conn:
            mid = self._insert(
                conn,
                "INSERT INTO group_messages (group_id, sender, encrypted_message, timestamp) "
                "VALUES (?, ?, ?, ?)", (group_id, sender, encrypted, ts), "message_id")
        return mid, ts

    def group_backlog(self, group_id, user):
        """Messages this member has not been sent yet (and is allowed to see)."""
        with self._tx() as conn:
            rows = self._all(
                conn,
                "SELECT gm2.* FROM group_messages gm2 JOIN group_members m "
                "ON m.group_id = gm2.group_id AND m.username = ? "
                "WHERE gm2.group_id = ? AND gm2.message_id > m.last_delivered_id "
                "AND gm2.message_id > m.first_visible_id ORDER BY gm2.message_id", (user, group_id))
        return [self._fmt_msg(r) for r in rows]

    def set_group_cursor(self, group_id, user, message_id):
        with self._tx() as conn:
            self._q(conn, "UPDATE group_members SET last_delivered_id = ? "
                          "WHERE group_id = ? AND username = ? AND last_delivered_id < ?",
                    (message_id, group_id, user, message_id))

    def set_group_cursor_if_caught_up(self, group_id, user, message_id):
        """Live delivery: move the delivered marker to ``message_id`` ONLY if nothing older is still undelivered.

        The marker is a single high-water mark per member. If a live message simply overwrote it while older
        messages were still queued for that member (for example one arriving while they were logging in),
        those older messages would never be delivered. Left behind, they are sent at the next login (clients
        ignore a message id they already have).
        """
        with self._tx() as conn:
            self._q(conn,
                    "UPDATE group_members SET last_delivered_id = ? "
                    "WHERE group_id = ? AND username = ? AND last_delivered_id < ? "
                    "AND NOT EXISTS (SELECT 1 FROM group_messages g WHERE g.group_id = group_members.group_id "
                    "AND g.message_id > group_members.last_delivered_id "
                    "AND g.message_id > group_members.first_visible_id AND g.message_id < ?)",
                    (message_id, group_id, user, message_id, message_id))

    def group_history(self, group_id, user, limit=100):
        with self._tx() as conn:
            rows = self._all(
                conn,
                "SELECT gm2.* FROM group_messages gm2 JOIN group_members m "
                "ON m.group_id = gm2.group_id AND m.username = ? "
                "WHERE gm2.group_id = ? AND gm2.message_id > m.first_visible_id "
                "ORDER BY gm2.message_id DESC LIMIT ?", (user, group_id, int(limit)))
        rows.reverse()
        return [self._fmt_msg(r) for r in rows]

    # ------------------------------------------------------------------ #
    # test helpers
    # ------------------------------------------------------------------ #
    def raw_dump(self, table):
        """Return every row of a table (used by the security tests)."""
        if table not in ("users", "messages", "group_messages", "friend_requests",
                         "friendships", "chat_groups", "group_members"):
            raise ValueError("unknown table")
        with self._tx() as conn:
            return self._all(conn, f"SELECT * FROM {table}")
