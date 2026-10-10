"""Secure chat server (raw TCP sockets + one thread per connected client).

Responsibilities (Chapter 3 of the project report):
  * register / login / logout with bcrypt-verified passwords and session control
  * route ENCRYPTED messages between authenticated users in real time
  * queue messages for offline users and deliver them when they log in
  * friends (request / accept), one-to-one chat, and group chats
  * store everything in the SQLite database - only ciphertext, password hashes
    and public keys

The server never receives a passphrase or a private key, so it can only ever
see ciphertext.

Networking: ``TCPChatServer`` accepts connections; every client gets its own
thread running ``ChatServer.handle_connection``. Frames are length-prefixed JSON
(see shared/protocol.py).
"""
import logging
import os
import socket
import threading
import time
from collections import deque

from shared import protocol as P
from shared.crypto_utils import hash_password, verify_password
from server.database import UserExistsError

log = logging.getLogger("chat.server")

SEND_TIMEOUT = 10          # seconds to wait for a slow client before giving up on one frame
IDLE_TIMEOUT = 90          # seconds of silence before a client is dropped (clients ping every 15 s)
MAX_CONNECTIONS = 500


class Connection:
    """One client socket. ``send`` is safe to call from any thread."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self._send_lock = threading.Lock()
        self._closed = False
        sock.settimeout(SEND_TIMEOUT)
        for level, opt in ((socket.IPPROTO_TCP, socket.TCP_NODELAY), (socket.SOL_SOCKET, socket.SO_KEEPALIVE)):
            try:
                sock.setsockopt(level, opt, 1)
            except OSError:
                pass

    def send(self, obj) -> bool:
        try:
            data = P.encode_frame(obj)
        except P.FrameTooLarge:
            log.warning("refusing to send an oversized frame (%s)", obj.get("type"))
            return False
        try:
            with self._send_lock:
                self.sock.sendall(data)
            return True
        except OSError:                       # includes timeouts: a client that stopped reading
            self.close()
            return False

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """One authenticated connection."""

    def __init__(self, conn, username, fullname):
        self.conn = conn
        self.username = username
        self.fullname = fullname
        self.sent = deque()          # timestamps, for flood control


class ChatServer:
    def __init__(self, db, bcrypt_rounds=12, max_failed=5, lockout_seconds=60,
                 rate_limit=(60, 10), idle_timeout=IDLE_TIMEOUT):
        """rate_limit = (max messages, per seconds) or None to disable."""
        self.db = db
        self.bcrypt_rounds = bcrypt_rounds
        self.max_failed = max_failed
        self.lockout_seconds = lockout_seconds
        self.rate_limit = rate_limit
        self.idle_timeout = idle_timeout
        self.lock = threading.RLock()    # guards ``sessions`` and ``failed``
        self.sessions = {}               # username -> Session
        self.failed = {}                 # username -> [failure count, locked_until]
        # Used so that "unknown user" takes as long as "wrong password".
        self._dummy_hash = hash_password("not-a-real-password", bcrypt_rounds)
        self._dispatch = {
            "ping": self.on_ping,
            "friend_request": self.on_friend_request,
            "friend_respond": self.on_friend_respond,
            "friend_remove": self.on_friend_remove,
            "send_dm": self.on_send_dm,
            "send_group": self.on_send_group,
            "history": self.on_history,
            "user_search": self.on_user_search,
            "group_create": self.on_group_create,
            "group_add": self.on_group_add,
            "group_remove": self.on_group_remove,
            "group_leave": self.on_group_leave,
        }

    # ------------------------------------------------------------------ #
    # small helpers
    # ------------------------------------------------------------------ #
    def _send_to(self, username, obj):
        s = self.sessions.get(username)
        if s is None:
            return False
        return s.conn.send(obj)

    @staticmethod
    def _error(conn, message, code=None):
        conn.send({"type": "error", "message": message, "code": code})

    @staticmethod
    def _notice(conn, message):
        conn.send({"type": "notice", "message": message})

    @staticmethod
    def _text(msg, key, maxlen=200):
        v = msg.get(key)
        if not isinstance(v, str):
            return ""
        return v.strip()[:maxlen]

    def _allow(self, session):
        if not self.rate_limit:
            return True
        limit, window = self.rate_limit
        now = time.monotonic()
        while session.sent and now - session.sent[0] > window:
            session.sent.popleft()
        if len(session.sent) >= limit:
            return False
        session.sent.append(now)
        return True

    def online_flag(self, username):
        return username in self.sessions

    # ------------------------------------------------------------------ #
    # connection lifecycle (runs in the client's own thread)
    # ------------------------------------------------------------------ #
    def handle_connection(self, conn):
        reader = P.FrameReader(conn.sock)
        session = None
        try:
            while True:
                try:
                    raw = reader.read(idle=self.idle_timeout)
                except P.FrameTooLarge:
                    self._error(conn, "Message is too large.")
                    break
                except (P.IdleTimeout, P.ConnectionClosed, OSError):
                    break
                if raw is None:                       # client closed the connection
                    break
                try:
                    msg = P.decode(raw)
                except P.ProtocolError as exc:
                    self._error(conn, str(exc))
                    continue
                mtype = msg["type"]
                if session is None:
                    if mtype == "register":
                        self.on_register(conn, msg)
                    elif mtype == "login":
                        session = self.on_login(conn, msg)
                    elif mtype == "ping":
                        conn.send({"type": "pong"})
                    else:
                        self._error(conn, "Please log in first.", "auth_required")
                    continue
                if mtype == "logout":
                    self._drop_session(session)           # offline first, THEN confirm to the client
                    session = None
                    conn.send({"type": "logged_out"})
                    break
                fn = self._dispatch.get(mtype)
                if fn is None:
                    self._error(conn, "Unknown request.")
                    continue
                try:
                    fn(session, msg)
                except Exception:                      # never let one bad request kill the session
                    log.exception("Error handling %s from %s", mtype, session.username)
                    self._error(conn, "Something went wrong on the server.")
        finally:
            if session is not None:
                self._drop_session(session)
            conn.close()

    def _drop_session(self, session):
        with self.lock:
            current = self.sessions.get(session.username) is session
            if current:
                del self.sessions[session.username]
            remaining = len(self.sessions)
        if current:
            self._broadcast_presence(session.username, session.fullname, False)
            log.info("%s disconnected (%d online)", session.username, remaining)

    def _broadcast_presence(self, username, fullname, online):
        event = {"type": "presence", "username": username, "fullname": fullname, "online": online}
        with self.lock:
            targets = [s for s in self.sessions.values() if s.username != username]
        for s in targets:
            s.conn.send(event)

    def close_all(self):
        with self.lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            s.conn.close()

    # ------------------------------------------------------------------ #
    # registration / login
    # ------------------------------------------------------------------ #
    @staticmethod
    def on_ping(session, _msg):
        session.conn.send({"type": "pong"})

    def on_register(self, conn, msg):
        username = P.normalize_username(msg.get("username"))
        error = P.validate_registration(username, msg.get("password"), msg.get("confirm"),
                                        msg.get("fullname"))
        error = error or P.validate_public_key(msg.get("public_key"))
        if error:
            conn.send({"type": "register_result", "ok": False, "message": error})
            return
        pw_hash = hash_password(msg["password"], self.bcrypt_rounds)
        try:
            self.db.create_user(username, pw_hash, msg["fullname"].strip(), msg["public_key"])
        except UserExistsError:
            conn.send({"type": "register_result", "ok": False, "message": P.MSG_USER_EXISTS})
            return
        log.info("registered %s", username)
        conn.send({"type": "register_result", "ok": True, "message": P.MSG_REGISTERED,
                   "username": username})

    def _locked(self, username):
        with self.lock:
            rec = self.failed.get(username)
            return bool(rec and rec[1] > time.monotonic())

    def _record_failure(self, username):
        with self.lock:
            rec = self.failed.setdefault(username, [0, 0.0])
            rec[0] += 1
            if rec[0] >= self.max_failed:
                rec[0] = 0
                rec[1] = time.monotonic() + self.lockout_seconds

    def on_login(self, conn, msg):
        username = P.normalize_username(msg.get("username"))
        password = msg.get("password")

        def fail(text):
            conn.send({"type": "login_result", "ok": False, "message": text})
            return None

        if not username or not isinstance(password, str) or len(password) > 200:
            return fail(P.MSG_LOGIN_BAD)
        if self._locked(username):
            return fail("Too many failed attempts. Please try again in a minute.")
        user = self.db.get_user(username)
        stored = user["password"] if user else self._dummy_hash
        ok = verify_password(password, stored)
        if not user or not ok:
            self._record_failure(username)
            return fail(P.MSG_LOGIN_BAD)
        with self.lock:
            self.failed.pop(username, None)
        # The password is right; now check that the passphrase is this account's passphrase.
        if not user.get("public_key"):
            return fail(P.MSG_NO_KEY)
        if msg.get("public_key") != user["public_key"]:
            return fail(P.MSG_PASSPHRASE_BAD)

        session = Session(conn, username, user["fullname"])
        with self.lock:
            old = self.sessions.get(username)         # one active session per user
            self.sessions[username] = session
            online = len(self.sessions)
        if old is not None:
            old.conn.send({"type": "kicked",
                           "message": "You were logged out because your account logged in elsewhere."})
            old.conn.close()
        conn.send({"type": "login_result", "ok": True, "message": P.MSG_LOGIN_OK,
                   "user": {"username": username, "fullname": user["fullname"]}})
        self._send_state(session)
        self._flush_offline(session)
        self._broadcast_presence(username, user["fullname"], True)
        log.info("%s logged in (%d online)", username, online)
        return session

    def _send_state(self, session):
        me = session.username
        with self.lock:
            online = [{"username": s.username, "fullname": s.fullname}
                      for s in self.sessions.values() if s.username != me]
        session.conn.send({
            "type": "state",
            "friends": [dict(f, online=self.online_flag(f["username"]))
                        for f in self.db.friends_of(me)],
            "requests_in": self.db.incoming_requests(me),
            "requests_out": self.db.outgoing_requests(me),
            "groups": self.db.groups_for_user(me),
            "online": online,
        })

    # ------------------------------------------------------------------ #
    # offline delivery
    # ------------------------------------------------------------------ #
    @staticmethod
    def _dm_event(row, queued=False):
        return {"type": "message", "id": row["message_id"], "from": row["sender"],
                "to": row["receiver"], "payload": row["encrypted_message"],
                "timestamp": row["timestamp"], "queued": queued}

    @staticmethod
    def _group_event(row, queued=False):
        return {"type": "group_message", "id": row["message_id"], "group_id": row["group_id"],
                "from": row["sender"], "payload": row["encrypted_message"],
                "timestamp": row["timestamp"], "queued": queued}

    def _flush_offline(self, session):
        me = session.username
        delivered = []
        for row in self.db.undelivered_messages(me):
            if session.conn.send(self._dm_event(row, queued=True)):
                delivered.append(row["message_id"])
        self.db.mark_delivered(delivered)
        for g in self.db.groups_for_user(me):
            last = None
            for row in self.db.group_backlog(g["group_id"], me):
                if session.conn.send(self._group_event(row, queued=True)):
                    last = row["message_id"]
            if last is not None:
                self.db.set_group_cursor(g["group_id"], me, last)

    # ------------------------------------------------------------------ #
    # friends
    # ------------------------------------------------------------------ #
    def on_friend_request(self, session, msg):
        me, conn = session.username, session.conn
        target = P.normalize_username(msg.get("to"))
        if not target:
            return self._error(conn, "Enter a username.")
        if target == me:
            return self._error(conn, "You cannot add yourself.")
        user = self.db.get_user(target)
        if not user:
            return self._error(conn, "User not found.")
        if self.db.are_friends(me, target):
            return self._notice(conn, "You are already friends.")
        if self.db.has_request(me, target):
            return self._notice(conn, "Friend request already sent.")
        if self.db.has_request(target, me):                      # they already asked us
            self.db.make_friends(me, target)
            self._announce_friendship(self.db.get_user(me), user)
            conn.send({"type": "request_cleared", "username": target})
            return
        self.db.add_friend_request(me, target)
        conn.send({"type": "request_sent", "username": target})
        self._notice(conn, f"Friend request sent to {target}.")
        self._send_to(target, {"type": "friend_request_in", "username": me,
                               "fullname": session.fullname})

    def on_user_search(self, session, msg):
        """"Find people": everybody except me (optionally filtered), with their relationship to me."""
        me, conn = session.username, session.conn
        query = msg.get("query")
        query = query.strip()[:40] if isinstance(query, str) else ""
        rows = self.db.search_users(me, query, P.USER_SEARCH_LIMIT + 1)
        more = len(rows) > P.USER_SEARCH_LIMIT
        friends = {f["username"] for f in self.db.friends_of(me)}
        incoming = {r["username"] for r in self.db.incoming_requests(me)}
        outgoing = set(self.db.outgoing_requests(me))
        users = []
        for r in rows[:P.USER_SEARCH_LIMIT]:
            name = r["username"]
            status = ("friend" if name in friends else "request_received" if name in incoming
                      else "request_sent" if name in outgoing else "none")
            users.append({"username": name, "fullname": r["fullname"], "status": status,
                          "online": self.online_flag(name)})
        conn.send({"type": "user_list", "query": query, "users": users, "more": more})

    def _announce_friendship(self, a, b):
        """a and b are user rows (username, fullname, public_key)."""
        def card(u):
            return {"username": u["username"], "fullname": u["fullname"],
                    "public_key": u.get("public_key", ""), "online": self.online_flag(u["username"])}
        self._send_to(a["username"], {"type": "friend_added", "user": card(b)})
        self._send_to(b["username"], {"type": "friend_added", "user": card(a)})

    def on_friend_respond(self, session, msg):
        me, conn = session.username, session.conn
        sender = P.normalize_username(msg.get("from"))
        if not self.db.has_request(sender, me):
            return self._error(conn, "That friend request no longer exists.")
        conn.send({"type": "request_cleared", "username": sender})
        if msg.get("accept") is True:
            self.db.make_friends(me, sender)
            self._announce_friendship(self.db.get_user(me), self.db.get_user(sender))
        else:
            self.db.delete_request(sender, me)
            self._notice(conn, "Friend request declined.")

    def on_friend_remove(self, session, msg):
        me, conn = session.username, session.conn
        other = P.normalize_username(msg.get("user"))
        if not other or not self.db.are_friends(me, other):
            return self._error(conn, "That user is not in your friends list.")
        self.db.remove_friendship(me, other)
        conn.send({"type": "friend_removed", "username": other})
        self._send_to(other, {"type": "friend_removed", "username": me})

    # ------------------------------------------------------------------ #
    # direct messages
    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_payload(msg):
        payload = msg.get("payload")
        if not isinstance(payload, str) or not payload:
            return None, "Message is empty."
        if len(payload) > P.MAX_PAYLOAD:
            return None, "Message is too long."
        return payload, None

    def on_send_dm(self, session, msg):
        me, conn = session.username, session.conn
        if not self._allow(session):
            return self._error(conn, "You are sending messages too fast. Slow down.")
        target = P.normalize_username(msg.get("to"))
        payload, err = self._check_payload(msg)
        if err:
            return self._error(conn, err)
        if not target or not self.db.are_friends(me, target):
            return self._error(conn, "You can only message people in your friends list.")
        mid, ts = self.db.save_message(me, target, payload)
        event = self._dm_event({"message_id": mid, "sender": me, "receiver": target,
                                "encrypted_message": payload, "timestamp": ts})
        if self._send_to(target, event):
            self.db.mark_delivered([mid])
        conn.send(event)                                  # echo back to the sender

    # ------------------------------------------------------------------ #
    # groups
    # ------------------------------------------------------------------ #
    def _push_group_update(self, group_id):
        group = self.db.get_group(group_id)
        if not group:
            return
        event = {"type": "group_update", "group": group}
        for m in group["members"]:
            self._send_to(m["username"], event)

    @staticmethod
    def _gid(msg):
        gid = msg.get("group_id")
        return gid if isinstance(gid, int) and not isinstance(gid, bool) else None

    def _member_group(self, session, msg):
        """Return the group dict if the caller is a member, else send an error."""
        gid = self._gid(msg)
        group = self.db.get_group(gid) if gid is not None else None
        if not group or session.username not in [m["username"] for m in group["members"]]:
            self._error(session.conn, "You are not a member of that group.")
            return None
        return group

    def on_group_create(self, session, msg):
        me, conn = session.username, session.conn
        name = self._text(msg, "name", P.MAX_GROUP_NAME)
        if not name:
            return self._error(conn, "Group name is required.")
        raw = msg.get("members")
        if not isinstance(raw, list):
            return self._error(conn, "Pick at least one friend.")
        members = []
        for m in raw:
            u = P.normalize_username(m)
            if u and u != me and u not in members:
                members.append(u)
        if not members:
            return self._error(conn, "Pick at least one friend.")
        if len(members) + 1 > P.MAX_GROUP_MEMBERS:
            return self._error(conn, f"A group can have at most {P.MAX_GROUP_MEMBERS} members.")
        for u in members:
            if not self.db.are_friends(me, u):
                return self._error(conn, f"{u} is not in your friends list.")
        gid = self.db.create_group(name, me, members)
        self._push_group_update(gid)

    def on_group_add(self, session, msg):
        me, conn = session.username, session.conn
        group = self._member_group(session, msg)
        if not group:
            return
        if group["admin"] != me:
            return self._error(conn, "Only the group admin can add members.")
        target = P.normalize_username(msg.get("username"))
        if not target or not self.db.are_friends(me, target):
            return self._error(conn, "You can only add people from your friends list.")
        if target in [m["username"] for m in group["members"]]:
            return self._notice(conn, f"{target} is already in the group.")
        if len(group["members"]) >= P.MAX_GROUP_MEMBERS:
            return self._error(conn, "The group is full.")
        self.db.add_group_member(group["group_id"], target)
        self._push_group_update(group["group_id"])

    def on_group_remove(self, session, msg):
        me, conn = session.username, session.conn
        group = self._member_group(session, msg)
        if not group:
            return
        if group["admin"] != me:
            return self._error(conn, "Only the group admin can remove members.")
        target = P.normalize_username(msg.get("username"))
        if target == me:
            return self._error(conn, "Use 'Leave group' to leave your own group.")
        if target not in [m["username"] for m in group["members"]]:
            return self._error(conn, "That user is not in the group.")
        self.db.remove_group_member(group["group_id"], target)
        self._send_to(target, {"type": "group_removed", "group_id": group["group_id"],
                               "name": group["name"], "reason": "removed"})
        self._push_group_update(group["group_id"])

    def on_group_leave(self, session, msg):
        me = session.username
        group = self._member_group(session, msg)
        if not group:
            return
        gid = group["group_id"]
        self.db.remove_group_member(gid, me)
        session.conn.send({"type": "group_removed", "group_id": gid,
                           "name": group["name"], "reason": "left"})
        remaining = [m["username"] for m in group["members"] if m["username"] != me]
        if not remaining:
            self.db.delete_group(gid)
            return
        if group["admin"] == me:                          # hand the group to the longest member
            self.db.set_group_admin(gid, remaining[0])
        self._push_group_update(gid)

    def on_send_group(self, session, msg):
        me, conn = session.username, session.conn
        if not self._allow(session):
            return self._error(conn, "You are sending messages too fast. Slow down.")
        payload, err = self._check_payload(msg)
        if err:
            return self._error(conn, err)
        group = self._member_group(session, msg)
        if not group:
            return
        gid = group["group_id"]
        mid, ts = self.db.save_group_message(gid, me, payload)
        event = self._group_event({"message_id": mid, "group_id": gid, "sender": me,
                                   "encrypted_message": payload, "timestamp": ts})
        for m in group["members"]:
            if self._send_to(m["username"], event):
                self.db.set_group_cursor(gid, m["username"], mid)

    # ------------------------------------------------------------------ #
    # history
    # ------------------------------------------------------------------ #
    def on_history(self, session, msg):
        me, conn = session.username, session.conn
        kind = msg.get("kind")
        limit = msg.get("limit") if isinstance(msg.get("limit"), int) else 100
        limit = max(1, min(limit, P.HISTORY_LIMIT))
        if kind == "dm":
            other = P.normalize_username(msg.get("with"))
            if not other or not self.db.are_friends(me, other):
                return self._error(conn, "You can only view history with your friends.")
            rows = self.db.direct_history(me, other, limit)
            conn.send({"type": "history", "kind": "dm", "with": other, "messages": [
                {"id": r["message_id"], "from": r["sender"], "to": r["receiver"],
                 "payload": r["encrypted_message"], "timestamp": r["timestamp"]} for r in rows]})
            # Everything shown in the history counts as delivered.
            self.db.mark_delivered([r["message_id"] for r in rows if r["receiver"] == me])
        elif kind == "group":
            group = self._member_group(session, msg)
            if not group:
                return
            rows = self.db.group_history(group["group_id"], me, limit)
            conn.send({"type": "history", "kind": "group", "group_id": group["group_id"],
                       "messages": [{"id": r["message_id"], "from": r["sender"],
                                     "payload": r["encrypted_message"],
                                     "timestamp": r["timestamp"]} for r in rows]})
            if rows:
                self.db.set_group_cursor(group["group_id"], me, rows[-1]["message_id"])
        else:
            self._error(conn, "Unknown history request.")


# --------------------------------------------------------------------------- #
# TCP listener
# --------------------------------------------------------------------------- #
class TCPChatServer:
    """Listens on host:port and runs ``chat.handle_connection`` in a thread per client."""

    def __init__(self, chat, host, port):
        self.chat = chat
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.listener = socket.socket(family, socket.SOCK_STREAM)
        if os.name != "nt":                      # on Windows SO_REUSEADDR would allow port hijacking
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(128)
        self.listener.settimeout(0.5)
        self.host = host
        self.port = self.listener.getsockname()[1]
        self._connections = set()
        self._conn_lock = threading.Lock()
        self._stopping = False
        self._thread = threading.Thread(target=self._accept_loop, name="accept", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _accept_loop(self):
        while not self._stopping:
            try:
                sock, addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn = Connection(sock, addr)
            with self._conn_lock:
                if len(self._connections) >= MAX_CONNECTIONS:
                    conn.close()
                    continue
                self._connections.add(conn)
            threading.Thread(target=self._run, args=(conn,), name=f"client-{addr[1]}",
                             daemon=True).start()

    def _run(self, conn):
        try:
            self.chat.handle_connection(conn)
        except Exception:                                  # pragma: no cover - last-resort safety net
            log.exception("connection handler crashed")
        finally:
            conn.close()
            with self._conn_lock:
                self._connections.discard(conn)

    def stop(self):
        self._stopping = True
        try:
            self.listener.close()
        except OSError:
            pass
        with self._conn_lock:
            conns = list(self._connections)
        for c in conns:
            c.close()
        self._thread.join(2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self._conn_lock:
                if not self._connections:
                    break
            time.sleep(0.02)


def start_server(chat, host, port):
    """Start listening and return the running TCPChatServer (``.port`` is the real port)."""
    return TCPChatServer(chat, host, port).start()
