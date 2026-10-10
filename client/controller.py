"""Client controller - all chat logic, with no GUI code in it.

The Tkinter window calls the action methods (start_auth, send_text, ...) and
reacts to ``on_update(topic, payload)`` callbacks. Keeping this separate makes
the whole client testable without a screen.

Encryption happens HERE: text is encrypted with AES-256-GCM before it is handed
to the network layer, and decrypted only after it arrives. The key comes from the
user's own passphrase (see shared/keys.py), so the server only ever sees ciphertext.
"""
from datetime import datetime, timezone

from client.network import NetworkClient, normalize_address
from shared import protocol as P
from shared.crypto_utils import PBKDF2_ITERATIONS, DecryptionError
from shared.keys import (Identity, PeerKeyError, generate_passphrase, normalize_passphrase,
                         open_dm, open_group, seal_dm, seal_group)

DECRYPT_FAIL = "[Unable to decrypt this message]"
MAX_TEXT_BYTES = 12_000


def dm_key(username):
    return f"dm:{username}"


def group_key(group_id):
    return f"group:{int(group_id)}"


def local_time(ts: str) -> str:
    """Server timestamps are UTC 'YYYY-MM-DD HH:MM:SS'; show them in local time."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%d %b %H:%M")
    except (TypeError, ValueError):
        return str(ts or "")


class ChatController:
    def __init__(self, network_factory=NetworkClient, kdf_iterations=PBKDF2_ITERATIONS):
        self._network_factory = network_factory
        self._kdf_iterations = kdf_iterations
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
        self.network.retry = True
        if self._last_login_msg:
            self._login_msg = self._last_login_msg
        self.loaded.clear()
        if was_logged_in and self.current:
            self._request_history(self.current)
        self._notify("login", ev)

    def _on_state(self, ev):
        self.friends = {f["username"]: {"fullname": f["fullname"], "online": f["online"],
                                        "public_key": f.get("public_key", "")}
                        for f in ev.get("friends", [])}
        self.requests_in = list(ev.get("requests_in", []))
        self.requests_out = set(ev.get("requests_out", []))
        self.groups = {g["group_id"]: g for g in ev.get("groups", [])}
        self._learn_keys()
        self.online_users = {u["username"]: u["fullname"] for u in ev.get("online", [])}
        if self.current and not self._conversation_exists(self.current):
            self.current = None
        self._notify("state")

    def _on_presence(self, ev):
        u = ev["username"]
        if ev["online"]:
            self.online_users[u] = ev.get("fullname", u)
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
                                       "public_key": u.get("public_key", "")}
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
        self._notify("error", ev.get("message", "Error"))

    def _on_kicked(self, ev):
        self._teardown()
        self._notify("kicked", ev.get("message", "You were logged out."))

    def _on_logged_out(self, ev):
        self._teardown()
        self._notify("logged_out")

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

    def _store(self, key, mid, sender, text, ok, timestamp):
        self.messages.setdefault(key, {})[mid] = {
            "id": mid, "from": sender, "text": text, "ok": ok, "timestamp": timestamp}

    def _bump_unread(self, key, sender):
        if sender != self.me["username"] and key != self.current:
            self.unread[key] = self.unread.get(key, 0) + 1
            self._notify("unread", key)

    def _on_message(self, ev):
        me = self.me["username"]
        other = ev["to"] if ev["from"] == me else ev["from"]
        key = dm_key(other)
        text, ok = self._decrypt_dm(ev)
        fresh = ev["id"] not in self.messages.get(key, {})
        self._store(key, ev["id"], ev["from"], text, ok, ev["timestamp"])
        if fresh:
            self._bump_unread(key, ev["from"])
        self._notify("conversation", key)

    def _on_group_message(self, ev):
        key = group_key(ev["group_id"])
        text, ok = self._decrypt_group(ev["from"], ev["group_id"], ev["payload"])
        fresh = ev["id"] not in self.messages.get(key, {})
        self._store(key, ev["id"], ev["from"], text, ok, ev["timestamp"])
        if fresh:
            self._bump_unread(key, ev["from"])
        self._notify("conversation", key)

    def _on_history(self, ev):
        if ev["kind"] == "dm":
            key = dm_key(ev["with"])
            for m in ev["messages"]:
                text, ok = self._decrypt_dm(m)
                self._store(key, m["id"], m["from"], text, ok, m["timestamp"])
        else:
            key = group_key(ev["group_id"])
            for m in ev["messages"]:
                text, ok = self._decrypt_group(m["from"], ev["group_id"], m["payload"])
                self._store(key, m["id"], m["from"], text, ok, m["timestamp"])
        self.loaded.add(key)
        self._notify("conversation", key)

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
        self._notify("user_list")

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
        return sorted(self.messages.get(key, {}).values(), key=lambda m: m["id"])

    def title_for(self, key):
        if key is None:
            return ""
        if key.startswith("dm:"):
            u = key[3:]
            f = self.friends.get(u, {})
            return f"{f.get('fullname', u)} (@{u})"
        g = self.groups.get(int(key[6:]))
        return f"{g['name']} - {len(g['members'])} members" if g else ""

    def friend_list(self):
        """[(username, fullname, online)] - online first, then by name."""
        rows = [(u, f["fullname"], f["online"]) for u, f in self.friends.items()]
        return sorted(rows, key=lambda r: (not r[2], r[1].lower()))

    def group_list(self):
        return sorted(self.groups.values(), key=lambda g: g["name"].lower())

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
