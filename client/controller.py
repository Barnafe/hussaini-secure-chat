"""Client controller - all chat logic, with no GUI code in it.

The Tkinter window calls the action methods (start_auth, send_text, ...) and
reacts to ``on_update(topic, payload)`` callbacks. Keeping this separate makes
the whole client testable without a screen.

Encryption happens HERE: text is encrypted with AES-256-GCM before it is handed
to the network layer, and decrypted only after it arrives. The key comes from the
user's own passphrase (see shared/keys.py), so the server only ever sees ciphertext.
"""
import re
import time
from datetime import datetime, timezone

from client.network import NetworkClient, normalize_address
from client.prefs import Prefs
from shared import protocol as P
from shared.crypto_utils import PBKDF2_ITERATIONS, DecryptionError
from client.trust import TrustStore
from shared.keys import (Identity, PeerKeyError, generate_passphrase, normalize_passphrase,
                         open_dm, open_group, safety_number, seal_dm, seal_group)

DECRYPT_FAIL = "[Unable to decrypt this message]"
MAX_TEXT_BYTES = 12_000


def dm_key(username):
    return f"dm:{username}"


def group_key(group_id):
    return f"group:{int(group_id)}"


def local_dt(ts: str):
    """Server timestamps are UTC 'YYYY-MM-DD HH:MM:SS'; return a local datetime (or None)."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
    except (TypeError, ValueError):
        return None


def local_time(ts: str) -> str:
    dt = local_dt(ts)
    return dt.strftime("%d %b %H:%M") if dt else str(ts or "")


def clock(ts: str) -> str:
    """'3:58 PM' (no leading zero, same on every platform)."""
    dt = local_dt(ts)
    return dt.strftime("%I:%M %p").lstrip("0") if dt else ""


def day_heading(ts: str, today=None) -> str:
    """'Today, 10 Oct 2026' / 'Yesterday, 9 Oct 2026' / '3 Oct 2026'."""
    dt = local_dt(ts)
    if dt is None:
        return ""
    today = (today or datetime.now().astimezone()).date()
    text = f"{dt.day} {dt.strftime('%b %Y')}"
    days = (today - dt.date()).days
    return f"Today, {text}" if days == 0 else f"Yesterday, {text}" if days == 1 else text


def list_time(ts: str, today=None) -> str:
    """Time shown in the chat list: the clock today, 'Yesterday', else the short date."""
    dt = local_dt(ts)
    if dt is None:
        return ""
    today = (today or datetime.now().astimezone()).date()
    days = (today - dt.date()).days
    if days == 0:
        return clock(ts)
    return "Yesterday" if days == 1 else f"{dt.day} {dt.strftime('%b')}"


_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)


def find_links(text: str):
    """URLs inside a message (trailing punctuation removed)."""
    return [m.group(0).rstrip(".,;:!?)") for m in _URL_RE.finditer(text or "")]


TYPING_SECONDS = 5           # how long "Bob is typing..." stays after the last notice
TYPING_THROTTLE = 3          # send at most one typing notice per conversation every 3 seconds


def day_label(ts: str, today=None) -> str:
    """'Today' / 'Yesterday' / '12 Oct 2026' for a server timestamp, in local time."""
    try:
        day = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone().date()
    except (TypeError, ValueError):
        return ""
    today = today or datetime.now().astimezone().date()
    delta = (today - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return day.strftime("%d %b %Y").lstrip("0")


class ChatController:
    def __init__(self, network_factory=NetworkClient, kdf_iterations=PBKDF2_ITERATIONS, prefs=None,
                 trust_store=None):
        self._network_factory = network_factory
        self._kdf_iterations = kdf_iterations
        self.prefs = prefs if prefs is not None else Prefs()
        self.trust = trust_store or TrustStore()
        self.on_update = lambda topic, payload=None: None
        self.network = None
        self.connected = False
        self._reset_state()

    def _reset_state(self):
        self.identity = None         # my key pair (derived from my passphrase)
        self.me = None
        self.logged_in = False
        self.friends = {}            # username -> {"fullname", "online"}
        self.requests_in = []        # [{"username", "fullname"}]
        self.requests_out = set()
        self.groups = {}             # group_id -> {"group_id", "name", "admin", "members": [...]}
        self.online_users = {}       # username -> fullname (everyone online except me)
        self.peer_keys = {}          # username -> public key (from friends / group members)
        self.messages = {}           # conv key -> {message id -> message dict}
        self.loaded = set()          # conv keys whose history was fetched
        self.unread = {}             # conv key -> count
        self.current = None
        self.user_results = []       # last "find people" answer: [{"username", "fullname", ...}]
        self.user_more = False       # True if there were more matches than were sent
        self.user_query = None
        self.profiles = {}           # username -> {"fullname", "bio", "avatar_ver"} for everyone we know of
        self.avatars = {}            # username -> (version, base64 picture) - fetched on demand
        self._avatar_asked = set()   # (username, version) already requested
        self._preview_ids = set()    # (conv key, id) of messages known only as a chat-list preview
        self.typing = {}             # conv key -> {username: expiry (monotonic seconds)}
        self._typing_sent = {}       # conv key -> when I last told the server I am typing
        self.call_history = []
        self.active_call = None      # the one call being tracked: ringing in, ringing out, or running
        self._call_pending_until = 0.0   # a call request is in flight: refuse a second one meanwhile
        self._pending_new_password = None
        self._pending_auth = None
        self._pending_identity = None    # key pair of the login attempt in flight
        self._pending_register = None    # {"username", "passphrase"} of the registration in flight
        self._login_msg = None       # credentials of the last successful login (for auto-reconnect)
        self._last_login_msg = None  # credentials of the login attempt in flight

    def _notify(self, topic, payload=None):
        self.on_update(topic, payload)

    # ------------------------------------------------------------------ #
    # authentication
    # ------------------------------------------------------------------ #
    def start_auth(self, address, mode, passphrase, username, password, fullname="", confirm=""):
        """mode is 'login' or 'register'.

        Login needs the user's passphrase. Registration does not: a new passphrase is
        created automatically and handed back in the ``register_result`` update.
        """
        username = P.normalize_username(username)
        try:
            target = normalize_address(address)
        except ValueError:
            return self._notify("auth_error", P.format_address_error())
        if not target:
            return self._notify("auth_error", "Enter the server address.")
        if mode != "register" and not normalize_passphrase(passphrase):
            return self._notify("auth_error", "Enter your passphrase.")
        if not username or not password:
            return self._notify("auth_error", "Enter your username and password.")
        if mode == "register":
            error = P.validate_registration(username, password, confirm, fullname)
            if error:
                return self._notify("auth_error", error)
            new_passphrase = generate_passphrase()
            identity = Identity(username, new_passphrase, self._kdf_iterations)
            self._pending_register = {"username": username, "passphrase": new_passphrase}
            msg = {"type": "register", "username": username, "password": password,
                   "confirm": confirm, "fullname": fullname.strip(),
                   "public_key": identity.public_key_b64}
        else:
            identity = Identity(username, passphrase, self._kdf_iterations)
            self._pending_identity = identity
            msg = {"type": "login", "username": username, "password": password,
                   "public_key": identity.public_key_b64}
            self._last_login_msg = msg

        if self.network is not None and self.network.address != target:
            self.network.stop()
            self.network, self.connected = None, False
        if self.network is not None and self.connected:
            self.network.send(msg)
            return
        self._pending_auth = msg
        if self.network is None:
            self.network = self._network_factory(target)
            self.network.start()
            self._notify("status", "Connecting...")

    # ------------------------------------------------------------------ #
    # polling & event handling
    # ------------------------------------------------------------------ #
    def poll(self):
        self._expire_typing()
        if self.network is None:
            return 0
        events = self.network.drain()
        for ev in events:
            self.handle(ev)
        return len(events)

    def handle(self, ev):
        t = ev.get("type")
        fn = getattr(self, "_on_" + str(t).lstrip("_"), None)
        if fn:
            fn(ev)

    def _on_connected(self, ev):
        self.connected = True
        if self._pending_auth:
            msg, self._pending_auth = self._pending_auth, None
            self.network.send(msg)
        self._notify("connection", "Connected")

    def _on_disconnected(self, ev):
        self.connected = False
        self._drop_call_on_disconnect()
        if self._login_msg:
            self._pending_auth = self._login_msg           # log in again automatically
        self._notify("connection", "Connection lost - reconnecting...")

    def _on_connect_failed(self, ev):
        self.connected = False
        if self.network is not None:
            self.network.stop()
        self.network = None
        self._pending_auth = None
        self._notify("auth_error", ev.get("reason", P.MSG_CONN_FAILED))

    def _on_register_result(self, ev):
        pending, self._pending_register = self._pending_register, None
        if ev.get("ok") and pending:
            ev = dict(ev, passphrase=pending["passphrase"])     # shown to the user once
        self._notify("register_result", ev)

    def _on_login_result(self, ev):
        if not ev.get("ok"):
            self._notify("auth_error", ev.get("message", P.MSG_LOGIN_BAD))
            return
        was_logged_in = self.logged_in
        self.logged_in = True
        if self._pending_identity is not None:
            self.identity = self._pending_identity
        self.me = ev["user"]
        self._learn_profile(self.me)
        self.network.retry = True
        if self._last_login_msg:
            self._login_msg = self._last_login_msg
        self.loaded.clear()
        if was_logged_in and self.current:
            self._request_history(self.current)
        self._notify("login", ev)

    def _on_state(self, ev):
        self.friends = {f["username"]: {"fullname": f["fullname"], "online": f["online"],
                                        "public_key": f.get("public_key", ""), "bio": f.get("bio", ""),
                                        "avatar_ver": f.get("avatar_ver", "")}
                        for f in ev.get("friends", [])}
        for f in ev.get("friends", []):
            self._learn_profile(f)
        for r in ev.get("requests_in", []):
            self._learn_profile(r)
        self.requests_in = list(ev.get("requests_in", []))
        self.requests_out = set(ev.get("requests_out", []))
        self.groups = {g["group_id"]: g for g in ev.get("groups", [])}
        self._learn_keys()
        self._load_previews(ev.get("last") or {})
        self.online_users = {u["username"]: u["fullname"] for u in ev.get("online", [])}
        for u in ev.get("online", []):
            self._learn_profile(u)
        if self.current and not self._conversation_exists(self.current):
            self.current = None
        self._notify("state")

    def _on_presence(self, ev):
        u = ev["username"]
        if ev["online"]:
            self.online_users[u] = ev.get("fullname", u)
            self._learn_profile(ev)
        else:
            self.online_users.pop(u, None)
        if u in self.friends:
            self.friends[u]["online"] = ev["online"]
        self._notify("presence", u)

    def _on_friend_request_in(self, ev):
        if not any(r["username"] == ev["username"] for r in self.requests_in):
            self.requests_in.append({"username": ev["username"], "fullname": ev.get("fullname", "")})
        self._notify("requests")
        self._notify("notice", f"{ev['username']} sent you a friend request.")

    def _on_request_sent(self, ev):
        self.requests_out.add(ev["username"])
        self._notify("requests")

    def _on_request_cleared(self, ev):
        self.requests_in = [r for r in self.requests_in if r["username"] != ev["username"]]
        self.requests_out.discard(ev["username"])
        self._notify("requests")

    def _on_friend_added(self, ev):
        u = ev["user"]
        self.friends[u["username"]] = {"fullname": u["fullname"], "online": u["online"],
                                       "public_key": u.get("public_key", ""), "bio": u.get("bio", ""),
                                       "avatar_ver": u.get("avatar_ver", "")}
        self._learn_profile(u)
        self._learn_keys()
        self.requests_in = [r for r in self.requests_in if r["username"] != u["username"]]
        self.requests_out.discard(u["username"])
        self._notify("state")
        self._notify("notice", f"You are now friends with {u['fullname']}.")

    def _on_friend_removed(self, ev):
        self.friends.pop(ev["username"], None)
        if self.current == dm_key(ev["username"]):
            self.current = None
        self._notify("state")

    def _on_group_update(self, ev):
        g = ev["group"]
        for m in g.get("members", []):
            self._learn_profile(m)
        is_new = g["group_id"] not in self.groups
        self.groups[g["group_id"]] = g
        self._learn_keys()
        self._notify("groups", g["group_id"])
        if is_new and self.me and g["admin"] != self.me["username"]:
            self._notify("notice", f"You were added to the group '{g['name']}'.")

    def _on_group_removed(self, ev):
        gid = ev["group_id"]
        self.groups.pop(gid, None)
        key = group_key(gid)
        self.messages.pop(key, None)
        self.unread.pop(key, None)
        self.loaded.discard(key)
        if self.current == key:
            self.current = None
        self._notify("groups", gid)
        if ev.get("reason") == "removed":
            self._notify("notice", f"You were removed from the group '{ev.get('name', '')}'.")

    def _on_notice(self, ev):
        self._notify("notice", ev.get("message", ""))

    def _on_error(self, ev):
        self._call_pending_until = 0.0            # (a refused call request must not block the next try)
        self._notify("error", ev.get("message", "Error"))

    def _on_kicked(self, ev):
        self._teardown()
        self._notify("kicked", ev.get("message", "You were logged out."))

    def _on_logged_out(self, ev):
        self._teardown()
        self._notify("logged_out")

    # ---- profiles & pictures ------------------------------------------- #
    def _learn_profile(self, card):
        """Remember a user's name / status / picture version from any server message."""
        u = card.get("username")
        if not u:
            return
        p = self.profiles.setdefault(u, {"fullname": u, "bio": "", "avatar_ver": ""})
        for k in ("fullname", "bio", "avatar_ver"):
            if k in card and card[k] is not None:
                p[k] = card[k]

    def _on_profile(self, ev):
        """Somebody changed their name, status line or picture."""
        u = ev["username"]
        self._learn_profile(ev)
        if u in self.friends:
            self.friends[u].update(fullname=ev["fullname"], bio=ev.get("bio", ""),
                                   avatar_ver=ev.get("avatar_ver", ""))
        if u in self.online_users:
            self.online_users[u] = ev["fullname"]
        for g in self.groups.values():
            for m in g["members"]:
                if m["username"] == u:
                    m["fullname"] = ev["fullname"]
        for r in self.requests_in:
            if r["username"] == u:
                r["fullname"] = ev["fullname"]
        self._notify("profile", u)

    def _on_profile_result(self, ev):
        if ev.get("ok") and self.me:
            self.me.update(fullname=ev["fullname"], bio=ev.get("bio", ""), avatar_ver=ev.get("avatar_ver", ""))
            self._learn_profile(self.me)
        self._notify("profile_result", ev)

    def _on_password_result(self, ev):
        self._notify("password_result", ev)

    def _on_avatar(self, ev):
        u = ev["username"]
        if ev.get("data"):
            self.avatars[u] = (ev.get("avatar_ver", ""), ev["data"])
        else:
            self.avatars.pop(u, None)
        self._notify("avatar", u)

    def avatar_b64(self, username):
        """The user's current picture (base64 JPEG/PNG) or None. Asks the server for it the first time."""
        ver = self.profiles.get(username, {}).get("avatar_ver", "")
        if not ver:
            return None
        cached = self.avatars.get(username)
        if cached and cached[0] == ver:
            return cached[1]
        if (username, ver) not in self._avatar_asked and self.network is not None and self.connected:
            self._avatar_asked.add((username, ver))
            self.network.send({"type": "avatar_get", "username": username}, quiet=True)
        return cached[1] if cached else None

    def avatar_version(self, username):
        """Changes whenever the picture changes (use it as a cache key for rendered images)."""
        return self.profiles.get(username, {}).get("avatar_ver", "")

    # ---- messages ------------------------------------------------------ #
    def _learn_keys(self):
        """Remember every public key the server has told us about."""
        for u, f in self.friends.items():
            if f.get("public_key"):
                self.peer_keys[u] = f["public_key"]
        for g in self.groups.values():
            for m in g["members"]:
                if m.get("public_key"):
                    self.peer_keys[m["username"]] = m["public_key"]

    def _load_previews(self, last):
        """Decrypt the newest message of every conversation so the chat list can preview it."""
        for peer, m in (last.get("dm") or {}).items():
            if peer in self.friends:
                text, ok = self._decrypt_dm(m)
                self._store(dm_key(peer), m["id"], m["from"], text, ok, m["timestamp"])
                self._preview_ids.add((dm_key(peer), m["id"]))
        for gid, m in (last.get("group") or {}).items():
            if int(gid) in self.groups:
                text, ok = self._decrypt_group(m["from"], int(gid), m["payload"])
                self._store(group_key(gid), m["id"], m["from"], text, ok, m["timestamp"])
                self._preview_ids.add((group_key(gid), m["id"]))

    def _decrypt_dm(self, ev):
        other = ev["to"] if ev["from"] == self.me["username"] else ev["from"]
        try:
            return open_dm(self.identity, self.peer_keys.get(other, ""), ev["from"], ev["to"],
                           ev["payload"]), True
        except DecryptionError:
            return DECRYPT_FAIL, False

    def _decrypt_group(self, sender, group_id, payload):
        try:
            return open_group(self.identity, group_id, sender, payload,
                              self.peer_keys.get(sender)), True
        except DecryptionError:
            return DECRYPT_FAIL, False

    def _store(self, key, mid, sender, text, ok, timestamp, delivered=False):
        old = self.messages.setdefault(key, {}).get(mid)
        self.messages[key][mid] = {
            "id": mid, "from": sender, "text": text, "ok": ok, "timestamp": timestamp,
            "delivered": bool(delivered) or bool(old and old.get("delivered"))}

    def _bump_unread(self, key, sender):
        if sender != self.me["username"] and key != self.current:
            self.unread[key] = self.unread.get(key, 0) + 1
            self._notify("unread", key)

    def _on_message(self, ev):
        me = self.me["username"]
        other = ev["to"] if ev["from"] == me else ev["from"]
        key = dm_key(other)
        text, ok = self._decrypt_dm(ev)
        fresh = ev["id"] not in self.messages.get(key, {}) or (key, ev["id"]) in self._preview_ids
        self._preview_ids.discard((key, ev["id"]))
        self._store(key, ev["id"], ev["from"], text, ok, ev["timestamp"], ev.get("delivered", False))
        self._stop_typing(key, ev["from"])
        if fresh:
            self._bump_unread(key, ev["from"])
            if ev["from"] != me:
                self._notify("incoming", {"key": key, "from": ev["from"], "queued": bool(ev.get("queued"))})
        self._notify("conversation", key)

    def _on_group_message(self, ev):
        key = group_key(ev["group_id"])
        text, ok = self._decrypt_group(ev["from"], ev["group_id"], ev["payload"])
        fresh = ev["id"] not in self.messages.get(key, {}) or (key, ev["id"]) in self._preview_ids
        self._preview_ids.discard((key, ev["id"]))
        self._store(key, ev["id"], ev["from"], text, ok, ev["timestamp"])
        self._stop_typing(key, ev["from"])
        if fresh:
            self._bump_unread(key, ev["from"])
            if ev["from"] != self.me["username"]:
                self._notify("incoming", {"key": key, "from": ev["from"], "queued": bool(ev.get("queued"))})
        self._notify("conversation", key)

    def _on_last_message(self, ev):
        """The newest message of a conversation, for the chat list (never counts as unread)."""
        if ev.get("kind") == "group":
            key = group_key(ev["group_id"])
            text, ok = self._decrypt_group(ev["from"], ev["group_id"], ev["payload"])
        else:
            other = ev["to"] if ev["from"] == self.me["username"] else ev["from"]
            key = dm_key(other)
            text, ok = self._decrypt_dm(ev)
        if ev["id"] not in self.messages.get(key, {}):
            self._preview_ids.add((key, ev["id"]))     # a real delivery of it later still counts as new
        self._store(key, ev["id"], ev["from"], text, ok, ev["timestamp"])
        self._notify("conversation", key)

    def _on_history(self, ev):
        if ev["kind"] == "dm":
            key = dm_key(ev["with"])
            for m in ev["messages"]:
                text, ok = self._decrypt_dm(m)
                self._store(key, m["id"], m["from"], text, ok, m["timestamp"], m.get("delivered", False))
        else:
            key = group_key(ev["group_id"])
            for m in ev["messages"]:
                text, ok = self._decrypt_group(m["from"], ev["group_id"], m["payload"])
                self._store(key, m["id"], m["from"], text, ok, m["timestamp"])
        self.loaded.add(key)
        self._preview_ids = {pid for pid in self._preview_ids if pid[0] != key}
        self._notify("conversation", key)

    def _on_delivered(self, ev):
        """The recipient came online and received messages that were waiting: show the second tick."""
        key = dm_key(ev.get("peer", ""))
        for mid in ev.get("ids", []):
            if mid in self.messages.get(key, {}):
                self.messages[key][mid]["delivered"] = True
        self._notify("conversation", key)

    def _on_password_changed(self, ev):
        if ev.get("ok") and self._login_msg:
            self._login_msg = dict(self._login_msg, password=self._pending_new_password or self._login_msg["password"])
        self.call_history = []
        self.active_call = None
        self._pending_new_password = None
        self._notify("password_changed", ev)
        self._notify("password_result", ev)

    # ---- typing indicator ---------------------------------------------- #
    def _on_typing(self, ev):
        key = group_key(ev["group_id"]) if "group_id" in ev else dm_key(ev["from"])
        self.typing.setdefault(key, {})[ev["from"]] = time.monotonic() + TYPING_SECONDS
        self._notify("typing", key)

    def _stop_typing(self, key, username):
        if self.typing.get(key, {}).pop(username, None) is not None:
            self._notify("typing", key)

    def _expire_typing(self):
        now = time.monotonic()
        for key in list(self.typing):
            gone = [u for u, until in self.typing[key].items() if until <= now]
            for u in gone:
                del self.typing[key][u]
            if gone:
                self._notify("typing", key)

    def typing_names(self, key):
        """Display names of the people currently typing in a conversation."""
        now = time.monotonic()
        return [self.name_of(u) for u, until in self.typing.get(key, {}).items() if until > now]

    def notify_typing(self):
        """Call on every key press in the message box; tells the other side at most every 3 seconds."""
        key = self.current
        if not key or self.network is None or not self.connected:
            return
        now = time.monotonic()
        if now - self._typing_sent.get(key, 0) < TYPING_THROTTLE:
            return
        self._typing_sent[key] = now
        if key.startswith("dm:"):
            self.network.send({"type": "typing", "to": key[3:]})
        else:
            self.network.send({"type": "typing", "group_id": int(key[6:])})

    # ------------------------------------------------------------------ #
    # actions used by the GUI
    # ------------------------------------------------------------------ #
    def _conversation_exists(self, key):
        if key.startswith("dm:"):
            return key[3:] in self.friends
        return int(key[6:]) in self.groups

    def _request_history(self, key):
        if key.startswith("dm:"):
            self.network.send({"type": "history", "kind": "dm", "with": key[3:]})
        else:
            self.network.send({"type": "history", "kind": "group", "group_id": int(key[6:])})

    def open_conversation(self, key):
        self.current = key
        self.unread.pop(key, None)
        if key not in self.loaded:
            self._request_history(key)
        self._notify("conversation", key)
        self._notify("unread", key)

    def send_text(self, text):
        key, text = self.current, (text or "").strip()
        if not key or not text:
            return False
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            self._notify("error", "Message is too long.")
            return False
        me = self.me["username"]
        try:
            if key.startswith("dm:"):
                to = key[3:]
                payload = seal_dm(self.identity, self.peer_keys.get(to, ""), to, text)
                return self.network.send({"type": "send_dm", "to": to, "payload": payload})
            gid = int(key[6:])
            members = {m["username"]: m.get("public_key", "")
                       for m in self.groups[gid]["members"] if m.get("public_key")}
            members[me] = self.identity.public_key_b64
            payload = seal_group(self.identity, gid, text, members)
        except PeerKeyError:
            self._notify("error", "Cannot encrypt: a member has no encryption key.")
            return False
        except KeyError:
            self._notify("error", "That group no longer exists.")
            return False
        if len(payload) > P.MAX_PAYLOAD:
            self._notify("error", "Message is too long for this group.")
            return False
        return self.network.send({"type": "send_group", "group_id": gid, "payload": payload})

    # ---- calls ---------------------------------------------------------- #
    # The window hears about calls through ``call_incoming`` (someone is ringing me) and ``call_status``
    # (payload["status"]: ringing / accepted / busy / offline / declined / cancelled / no_answer / completed /
    # connection_lost, or one of the local refusals in CALL_REFUSALS). Only the call being tracked in
    # ``active_call`` is ever acted on, so a late message about an old call cannot disturb a new one.
    def call_block_reason(self, username):
        """Why a call with this friend must not start (None = fine). Calls use the same keys as chat."""
        if self.me is None or not self.logged_in:
            return "offline"
        if username not in self.friends:
            return "invalid_peer"
        if self.active_call or time.monotonic() < self._call_pending_until:
            return "in_call"
        if not self.peer_keys.get(username):
            return "no_key"
        if self.verification_state(username) == "changed":
            return "key_changed"
        return None

    def start_call(self, username, media="audio"):
        if media not in ("audio", "video"):
            media = "audio"
        reason = self.call_block_reason(username)
        if reason is None and (not self.network or not self.connected):
            reason = "offline"
        if reason:
            self._notify("call_status", {"status": reason, "peer": username})
            return False
        self._call_pending_until = time.monotonic() + 4
        return self.network.send({"type": "call_start", "to": username, "media": media})

    def answer_call(self, call_id, accept=True):
        call = self.active_call
        if not self.network or not call or call.get("call_id") != call_id:
            return False
        if accept:
            reason = self.call_block_reason_for_incoming(call.get("caller", ""))
            if reason:                                    # cannot be answered safely: decline and say why
                self._notify("call_status", {"status": reason, "peer": call.get("caller", "")})
                accept = False
        return self.network.send({"type": "call_accept" if accept else "call_decline", "call_id": call_id})

    def call_block_reason_for_incoming(self, caller):
        if not self.peer_keys.get(caller):
            return "no_key"
        if self.verification_state(caller) == "changed":
            return "key_changed"
        return None

    def end_call(self):
        if not self.network or not self.active_call:
            return False
        return self.network.send({"type": "call_end", "call_id": self.active_call.get("call_id", "")})

    def send_call_media(self, call_id, kind, seq, payload):
        if not self.network or not self.connected:
            return False
        return self.network.send({"type": "call_media", "call_id": call_id,
                                  "kind": kind, "seq": int(seq), "payload": payload}, quiet=True)

    def request_call_history(self):
        if self.network and self.connected:
            self.network.send({"type": "call_history"})

    def call_peer(self):
        """The friend in the call being tracked (or None)."""
        call = self.active_call
        if not call:
            return None
        return call.get("callee") if call.get("caller") == (self.me or {}).get("username") else call.get("caller")

    def _call_is_current(self, ev):
        call = self.active_call
        return bool(call) and bool(ev.get("call_id")) and call.get("call_id") == ev.get("call_id")

    def _on_call_incoming(self, ev):
        if self.active_call:                              # already busy (the server prevents this; be safe)
            if self.network:
                self.network.send({"type": "call_decline", "call_id": ev.get("call_id", "")})
            return
        self.active_call = dict(ev, status="ringing")
        self._notify("call_incoming", ev)

    def _on_call_result(self, ev):
        self._call_pending_until = 0.0
        status = ev.get("status")
        if status == "ringing" and ev.get("call_id"):
            if self.active_call and self.active_call.get("call_id") == ev["call_id"]:
                return                                    # (cannot happen: the server answers in order)
            self.active_call = dict(ev)
        self._notify("call_status", dict(ev))

    def _on_call_accepted(self, ev):
        if not self._call_is_current(ev):
            return
        self.active_call.update(status="active", media=ev.get("media", self.active_call.get("media")),
                                peer=ev.get("peer"))
        self._notify("call_status", dict(ev, status="accepted"))

    def _on_call_ended(self, ev):
        if not self._call_is_current(ev):
            return                                        # a message about a call I am not in: ignore
        call, self.active_call = self.active_call, None
        me = (self.me or {}).get("username")
        peer = call.get("callee") if call.get("caller") == me else call.get("caller")
        self._notify("call_status", {"type": "call_ended", "call_id": ev["call_id"], "status": ev.get("status", ""),
                                     "peer": peer, "media": call.get("media", "audio"),
                                     "incoming": call.get("callee") == me,
                                     "was_active": call.get("status") == "active"})
        self.request_call_history()

    def _drop_call_on_disconnect(self):
        """My connection dropped: the server has hung the call up, so close it here too."""
        if self.active_call:
            ev = {"type": "call_ended", "call_id": self.active_call.get("call_id"), "status": "connection_lost"}
            self._on_call_ended(ev)

    def _on_call_history(self, ev):
        calls = ev.get("calls", [])
        self.call_history = calls if isinstance(calls, list) else []
        self._notify("call_history", self.call_history)

    def _on_call_media(self, ev):
        if self._call_is_current(ev):
            self._notify("call_media", ev)

    def call_rows(self):
        """The call log prepared for display: newest first, from my point of view."""
        me = (self.me or {}).get("username")
        rows = []
        for c in self.call_history:
            outgoing = c.get("caller") == me
            peer = c.get("callee") if outgoing else c.get("caller")
            status, answered = c.get("status", ""), bool(c.get("answered"))
            if status in ("completed", "connection_lost") and answered:
                outcome = "Outgoing" if outgoing else "Incoming"
            elif status == "declined":
                outcome = "Declined"
            elif outgoing:
                outcome = {"busy": "Busy", "cancelled": "Cancelled"}.get(status, "No answer")
            else:
                outcome = "Missed"
            seconds = int(c.get("duration") or 0)
            rows.append({"call": c, "username": peer or "", "name": self.name_of(peer or ""),
                         "media": c.get("media", "audio"), "outgoing": outgoing, "outcome": outcome,
                         "missed": outcome == "Missed", "duration": seconds,
                         "duration_text": f"{seconds // 60}:{seconds % 60:02d}" if seconds else "",
                         "when": local_time(c.get("started", ""))})
        return rows

    def send_friend_request(self, username):
        self.network.send({"type": "friend_request", "to": username})

    def respond_request(self, username, accept):
        self.network.send({"type": "friend_respond", "from": username, "accept": bool(accept)})

    def remove_friend(self, username):
        self.network.send({"type": "friend_remove", "user": username})

    def create_group(self, name, members):
        self.network.send({"type": "group_create", "name": name, "members": list(members)})

    def group_add(self, gid, username):
        self.network.send({"type": "group_add", "group_id": gid, "username": username})

    def group_remove(self, gid, username):
        self.network.send({"type": "group_remove", "group_id": gid, "username": username})

    def group_leave(self, gid):
        self.network.send({"type": "group_leave", "group_id": gid})

    def search_users(self, query=""):
        """Ask the server for people to add. An empty query lists everybody."""
        self.network.send({"type": "user_search", "query": (query or "").strip()})

    def relationship(self, username):
        """friend / request_received / request_sent / none - always up to date with live events."""
        if username in self.friends:
            return "friend"
        if any(r["username"] == username for r in self.requests_in):
            return "request_received"
        if username in self.requests_out:
            return "request_sent"
        return "none"

    def is_online(self, username):
        return username in self.online_users

    def _on_user_list(self, ev):
        self.user_results = list(ev.get("users", []))
        self.user_more = bool(ev.get("more"))
        self.user_query = ev.get("query", "")
        for u in self.user_results:
            self._learn_profile(u)
        self._notify("user_list")

    def update_profile(self, fullname, bio):
        error = P.validate_profile(fullname, bio)
        if error:
            self._notify("profile_result", {"ok": False, "message": error})
            return False
        return self.network.send({"type": "profile_update", "fullname": fullname.strip(), "bio": bio.strip()})

    def set_avatar(self, b64):
        """b64 = a prepared picture (see client/images.prepare_avatar), or '' to remove it."""
        error = P.validate_avatar(b64)
        if error:
            self._notify("profile_result", {"ok": False, "message": error})
            return False
        return self.network.send({"type": "avatar_set", "data": b64})

    def set_muted(self, key, muted):
        self.prefs.set_muted(self.me["username"], key, muted)
        self._notify("unread", key)

    def is_muted(self, key):
        return bool(self.me and self.prefs.is_muted(self.me["username"], key))

    def clear_chat(self, key):
        """Hide this conversation's messages on THIS computer (the server's copy is untouched)."""
        ids = list(self.messages.get(key, {}))
        if ids:
            self.prefs.set_cleared(self.me["username"], key, max(ids))
        self._notify("conversation", key)

    def change_password(self, current, new, confirm):
        """Change my password. The answer arrives as a "password_changed" update (also sent as "password_result")."""
        error = P.validate_password(new, confirm)
        if not current:
            error = "Enter your current password."
        if error:
            ev = {"ok": False, "message": error}
            self._notify("password_changed", ev)
            self._notify("password_result", ev)
            return False
        self._pending_new_password = new
        return self.network.send({"type": "change_password", "current": current, "new": new, "confirm": confirm})

    def group_rename(self, gid, name):
        self.network.send({"type": "group_rename", "group_id": gid, "name": name})

    def group_transfer(self, gid, username):
        self.network.send({"type": "group_transfer", "group_id": gid, "username": username})

    # ---- safety numbers -------------------------------------------------- #
    def safety_number_for(self, peer):
        key = self.peer_keys.get(peer)
        if not key or self.identity is None:
            return None
        return safety_number(self.identity.public_key_b64, key)

    def verification_state(self, peer):
        """verified / changed (a verified friend's key is different now!) / unverified / unknown."""
        number = self.safety_number_for(peer)
        if number is None:
            return "unknown"
        saved = self.trust.verified_number(self.me["username"], peer)
        if saved is None:
            return "unverified"
        return "verified" if saved == number else "changed"

    def mark_verified(self, peer):
        number = self.safety_number_for(peer)
        if number:
            self.trust.mark(self.me["username"], peer, number)
            self._notify("verification", peer)

    def logout(self):
        if self.network is not None and self.connected:
            self.network.send({"type": "logout"})
        self._teardown()
        self._notify("logged_out")

    def _teardown(self):
        net = self.network
        self.network, self.connected = None, False
        if net is not None:
            net.stop()
        self._reset_state()

    def shutdown(self):
        self._teardown()

    # ------------------------------------------------------------------ #
    # read-only helpers for the GUI
    # ------------------------------------------------------------------ #
    def conversation(self, key):
        floor = self.prefs.cleared_upto(self.me["username"], key) if self.me else 0
        return sorted((m for m in self.messages.get(key, {}).values() if m["id"] > floor),
                      key=lambda m: m["id"])

    def last_message(self, key):
        msgs = self.conversation(key)
        return msgs[-1] if msgs else None

    def conversation_rows(self):
        """Everything for the chat list, newest conversation first.

        Each row: key, kind ('dm'/'group'), username/group_id, name, online, last (message or None),
        unread (count), muted.
        """
        rows = []
        for u, f in self.friends.items():
            key = dm_key(u)
            rows.append({"key": key, "kind": "dm", "username": u, "name": f["fullname"],
                         "online": f["online"], "last": self.last_message(key),
                         "unread": self.unread.get(key, 0), "muted": self.is_muted(key)})
        for g in self.groups.values():
            key = group_key(g["group_id"])
            rows.append({"key": key, "kind": "group", "group_id": g["group_id"], "name": g["name"],
                         "members": len(g["members"]), "online": False, "last": self.last_message(key),
                         "unread": self.unread.get(key, 0), "muted": self.is_muted(key)})
        rows.sort(key=lambda r: r["name"].lower())
        rows.sort(key=lambda r: (r["last"]["timestamp"], r["last"]["id"]) if r["last"] else ("", 0), reverse=True)
        return rows

    def shared_links(self, key, limit=5):
        """Links people posted in this conversation, newest first: [(url, host)]."""
        seen, out = set(), []
        for m in reversed(self.conversation(key)):
            for url in reversed(find_links(m["text"])):
                if url not in seen:
                    seen.add(url)
                    host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/")[0]
                    out.append((url, host.removeprefix("www.")))
                    if len(out) >= limit:
                        return out
        return out

    def bio_of(self, username):
        if self.me and username == self.me["username"]:
            return self.me.get("bio", "")
        return self.profiles.get(username, {}).get("bio", "")

    def title_for(self, key):
        if key is None:
            return ""
        if key.startswith("dm:"):
            u = key[3:]
            f = self.friends.get(u, {})
            return f"{f.get('fullname', u)} (@{u})"
        g = self.groups.get(int(key[6:]))
        return f"{g['name']} - {len(g['members'])} members" if g else ""

    def last_message_id(self, key):
        return max(self.messages.get(key, {}), default=0)

    def friend_list(self):
        """[(username, fullname, online)] - newest conversation first, then online, then by name."""
        rows = [(u, f["fullname"], f["online"]) for u, f in self.friends.items()]
        return sorted(rows, key=lambda r: (-self.last_message_id(dm_key(r[0])), not r[2], r[1].lower()))

    def group_list(self):
        return sorted(self.groups.values(),
                      key=lambda g: (-self.last_message_id(group_key(g["group_id"])), g["name"].lower()))

    def preview(self, key, limit=30):
        """One-line preview of the newest message of a conversation ('' if nothing is known yet)."""
        msgs = self.messages.get(key)
        if not msgs:
            return ""
        m = msgs[max(msgs)]
        text = " ".join(m["text"].split()) if m["ok"] else "[encrypted]"
        if len(text) > limit:
            text = text[:limit - 1].rstrip() + "…"
        if self.me and m["from"] == self.me["username"]:
            return "You: " + text
        if key.startswith("group:"):
            return self.name_of(m["from"]).split(" ")[0] + ": " + text
        return text

    def online_list(self):
        return sorted(self.online_users.items(), key=lambda kv: kv[1].lower())

    def name_of(self, username):
        if self.me and username == self.me["username"]:
            return "You"
        if username in self.friends:
            return self.friends[username]["fullname"]
        if username in self.online_users:
            return self.online_users[username]
        for g in self.groups.values():
            for m in g["members"]:
                if m["username"] == username:
                    return m["fullname"]
        return username
