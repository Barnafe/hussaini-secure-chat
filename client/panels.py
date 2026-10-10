"""The three columns of the main window: sidebar, conversation, contact info."""
import hashlib
import tkinter as tk
import webbrowser

from client import images
from client import theme as T
from client.controller import clock, day_label, dm_key, group_key, list_time, local_dt
from client.widgets import HitCanvas, ListCanvas, MessageView, PillEntry, Toggle, _wheel_units, bind_wheel

SIDEBAR_W = 330
INFO_W = 330
NAME_COLORS = ["#6ea8ff", "#ff9f6e", "#7ee0a1", "#f08fd0", "#ffd36e", "#a99bff", "#5fd6e8", "#ff8a8a"]


# --------------------------------------------------------------------------- #
# pictures
# --------------------------------------------------------------------------- #
def user_avatar(ctrl, username, size, bg=T.SIDEBAR, online=None):
    """Round picture of a user (their uploaded photo, or coloured initials). Cached."""
    me = ctrl.me and username == ctrl.me["username"]
    name = ctrl.me["fullname"] if me else ctrl.name_of(username)
    have = ctrl.avatar_b64(username) is not None            # also asks the server the first time
    ver = ctrl.avatar_version(username) if have else ""

    def make():
        if have:
            pic = images.decode_avatar(ctrl.avatars[username][1])
            base = images.circle_crop(pic, size) if pic else images.initials_avatar(name, username, size)
        else:
            base = images.initials_avatar(name, username, size)
        if online:
            base = images.add_status_dot(base, T.GREEN, bg)
        return base

    return T.photo(("avatar", username, ver, size, name if not have else "", bool(online), bg), make)


def group_photo(size, bg=T.SIDEBAR):
    return T.photo(("group", size, bg), lambda: images.group_avatar(size))


def first_name(name):
    return (name or "").split(" ")[0] or name


def name_color(username):
    return NAME_COLORS[int(hashlib.md5(username.encode()).hexdigest(), 16) % len(NAME_COLORS)]


def open_link(url):
    url = url if url.lower().startswith(("http://", "https://")) else "https://" + url
    try:
        webbrowser.open(url)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# sidebar
# --------------------------------------------------------------------------- #
NAV = [("chats", "Chats", "chat"), ("contacts", "Contacts", "user"), ("groups", "Groups", "users"),
       ("calls", "Calls", "phone"), ("settings", "Settings", "settings")]
HEADERS = {"chats": "DIRECT MESSAGES", "contacts": "CONTACTS", "groups": "GROUPS", "calls": "CALLS",
           "settings": "DIRECT MESSAGES"}


class Sidebar(tk.Frame):
    def __init__(self, parent, main):
        super().__init__(parent, bg=T.SIDEBAR, width=SIDEBAR_W)
        self.main, self.ctrl = main, main.ctrl
        self.pack_propagate(False)
        self.collapsed = False

        self.brand = HitCanvas(self, T.SIDEBAR, height=88)
        self.brand.pack(fill="x")
        self.brand.bind("<Configure>", lambda _e: self.draw_brand())

        self.search_var = tk.StringVar()
        self.search = PillEntry(self, textvariable=self.search_var, placeholder="Search conversations...",
                                icon="search", height=46, bg=T.SIDEBAR, field="#0c1a30", border="#122848")
        self.search.pack(fill="x", padx=20, pady=(0, 14))
        self.search_var.trace_add("write", lambda *_: self.refresh_list())

        self.nav = HitCanvas(self, T.SIDEBAR, height=5 * 50 + 6)
        self.nav.pack(fill="x")
        self.nav.bind("<Configure>", lambda _e: self.draw_nav())
        self.nav.bind("<Motion>", self._nav_motion, add="+")

        self.section = HitCanvas(self, T.SIDEBAR, height=44)
        self.section.pack(fill="x")
        self.section.bind("<Configure>", lambda _e: self.draw_section())

        self.list = ListCanvas(self, T.SIDEBAR, self._render, self._select, self._action)
        self.list.pack(fill="both", expand=True, pady=(0, 6))
        self._nav_hover = None

    # ---- static parts --------------------------------------------------- #
    def draw_brand(self):
        c, w = self.brand, max(self.brand.winfo_width(), SIDEBAR_W)
        c.reset()
        c.create_image(22, 18, image=T.icon("logo", 52, T.BLUE_HI, 1.9), anchor="nw")
        f = T.font(25, "bold")
        c.create_text(86, 18, text=T.BRAND[0], anchor="nw", fill=T.TEXT, font=f)
        c.create_text(86 + f.measure(T.BRAND[0]), 18, text=T.BRAND[1], anchor="nw", fill=T.BLUE_HI, font=f)
        c.create_text(88, 56, text=T.TAGLINE, anchor="nw", fill=T.MUTED, font=T.font(13))
        c.create_image(w - 36, 44, image=T.icon("menu", 24, T.TEXT2, 2.0))
        c.hit(w - 58, 20, w - 14, 68, self.main.show_app_menu)

    def draw_nav(self):
        c, w = self.nav, max(self.nav.winfo_width(), SIDEBAR_W)
        c.reset()
        active = self.main.view
        unread = self.main.total_unread()
        pending = len(self.ctrl.requests_in)
        for i, (key, label, icon) in enumerate(NAV):
            y = 4 + i * 50
            on = key == active
            hov = self._nav_hover == key and not on
            if on:
                img = T.photo(("navpill", w), lambda: images.gradient_rrect(w - 28, 44, 14, "#0f65fe", "#062f7f"))
                c.create_image(14, y, image=img, anchor="nw")
            elif hov:
                img = T.photo(("navhover", w), lambda: images.rounded_rect(w - 28, 44, 14, T.HOVER))
                c.create_image(14, y, image=img, anchor="nw")
            fg = "#ffffff" if on else T.TEXT2
            c.create_image(44, y + 22, image=T.icon(icon, 24, fg, 1.9))
            c.create_text(76, y + 22, text=label, anchor="w", fill=fg, font=T.font(17, "semibold" if on else "normal"))
            badge = unread if key == "chats" else pending if key == "contacts" else 0
            if badge:
                self._badge(c, w - 46, y + 22, badge, light=on)
            c.hit(14, y, w - 14, y + 44, lambda k=key: self.main.set_view(k))

    @staticmethod
    def _badge(c, x, y, n, light=False, muted=False):
        text = str(n) if n < 100 else "99+"
        d = 26 if len(text) < 3 else 32
        fill = "#d9e6ff" if light else "#2a3a5c" if muted else T.BLUE
        img = T.photo(("badge", d, fill), lambda: images.rounded_rect(d, 24, 12, fill))
        c.create_image(x, y, image=img)
        c.create_text(x, y, text=text, fill="#0a3fb5" if light else "#ffffff", font=T.font(13, "bold"))

    def _nav_motion(self, event):
        hov = None
        for i, (key, _l, _i) in enumerate(NAV):
            if 4 + i * 50 <= event.y <= 48 + i * 50:
                hov = key
        if hov != self._nav_hover:
            self._nav_hover = hov
            self.draw_nav()

    def draw_section(self):
        c, w = self.section, max(self.section.winfo_width(), SIDEBAR_W)
        c.reset()
        c.create_text(28, 28, text=HEADERS[self.main.view], anchor="w", fill=T.MUTED, font=T.font(14, "semibold"))
        c.create_image(w - 34, 28, image=T.icon("chevron_down" if self.collapsed else "chevron_up", 20, T.MUTED, 2.2))
        c.hit(0, 0, w, 44, self.toggle_section)

    def toggle_section(self):
        self.collapsed = not self.collapsed
        self.draw_section()
        if self.collapsed:
            self.list.pack_forget()
        else:
            self.list.pack(fill="both", expand=True, pady=(0, 6))

    # ---- list ------------------------------------------------------------ #
    def refresh(self):
        self.draw_nav()
        self.draw_section()
        self.refresh_list()

    def refresh_list(self):
        view, ctrl = self.main.view, self.ctrl
        query = self.search_var.get().strip().lower()
        rows = []
        if view in ("chats", "settings"):
            for r in ctrl.conversation_rows():
                if query and query not in r["name"].lower():
                    continue
                rows.append(dict(r, kind="chat", height=72, key=r["key"]))
            if not rows:
                msg = "No conversations match your search." if query else (
                    "No conversations yet.\nOpen Contacts to add a friend.")
                rows.append({"kind": "empty", "height": 150, "text": msg})
        elif view == "contacts":
            rows.append({"kind": "button", "height": 64, "text": "Add friend", "icon": "user_plus", "name": "add_friend"})
            reqs = [r for r in ctrl.requests_in if not query or query in r["fullname"].lower()
                    or query in r["username"]]
            if reqs:
                rows.append({"kind": "header", "height": 40, "text": f"FRIEND REQUESTS ({len(reqs)})"})
                rows += [dict(r, kind="request", height=70) for r in reqs]
            friends = [(u, n, o) for u, n, o in ctrl.friend_list() if not query or query in n.lower() or query in u]
            rows.append({"kind": "header", "height": 40, "text": f"CONTACTS ({len(friends)})"})
            for u, n, o in friends:
                rows.append({"kind": "friend", "height": 66, "username": u, "name": n, "online": o,
                             "key": dm_key(u), "bio": ctrl.bio_of(u)})
            if not friends:
                rows.append({"kind": "empty", "height": 90, "text": "No friends yet."})
            online = [(u, n) for u, n in ctrl.online_list() if u not in ctrl.friends
                      and (not query or query in n.lower() or query in u)]
            if online:
                rows.append({"kind": "header", "height": 40, "text": "ONLINE NOW"})
                for u, n in online:
                    rows.append({"kind": "online", "height": 66, "username": u, "name": n,
                                 "sent": u in ctrl.requests_out})
        elif view == "groups":
            rows.append({"kind": "button", "height": 64, "text": "New group", "icon": "plus", "name": "new_group"})
            groups = [g for g in ctrl.group_list() if not query or query in g["name"].lower()]
            for g in groups:
                rows.append({"kind": "group", "height": 68, "key": group_key(g["group_id"]), "group": g})
            if not groups:
                rows.append({"kind": "empty", "height": 110,
                             "text": "No groups yet.\nCreate one from your friends."})
        else:                                                      # calls
            rows.append({"kind": "empty", "height": 220,
                         "text": "Voice and video calls\nare not available yet."})
        self.list.set_rows(rows, self.ctrl.current)

    def _select(self, row):
        key = row.get("key")
        if key:
            self.main.open_conversation(key)

    def _action(self, row, name):
        main = self.main
        if name == "add_friend":
            main.add_friend()
        elif name == "new_group":
            main.new_group()
        elif name == "accept":
            self.ctrl.respond_request(row["username"], True)
        elif name == "decline":
            self.ctrl.respond_request(row["username"], False)
        elif name == "request" and not row.get("sent"):
            self.ctrl.send_friend_request(row["username"])
        elif name == "manage":
            main.manage_group(row["group"]["group_id"])

    # ---- row drawing --------------------------------------------------------- #
    def _render(self, c, row, y, w, hover, selected, regions):
        kind, h = row["kind"], row["height"]
        if kind == "empty":
            c.create_text(w // 2, y + h // 2, text=row["text"], fill=T.MUTED, font=T.font(15), justify="center",
                          width=w - 60)
            return
        if kind == "header":
            c.create_text(28, y + h - 14, text=row["text"], anchor="sw", fill=T.MUTED, font=T.font(13, "semibold"))
            return
        if kind == "button":
            img = T.photo(("sidebtn", w), lambda: images.rounded_rect(w - 40, 46, 23, "#0c1a30", outline="#17407f"))
            c.create_image(20, y + 9, image=img, anchor="nw")
            c.create_image(52, y + 32, image=T.icon(row["icon"], 20, T.BLUE_HI, 2.2))
            c.create_text(76, y + 32, text=row["text"], anchor="w", fill=T.TEXT, font=T.font(15, "semibold"))
            regions.append((20, w - 20, row["name"]))
            return
        # highlight
        if selected:
            img = T.photo(("rowsel", w, h), lambda: images.gradient_rrect(w - 20, h - 6, 16, T.BLUE_DEEP, T.BLUE_FADE))
            c.create_image(10, y + 3, image=img, anchor="nw")
        elif hover:
            img = T.photo(("rowhov", w, h), lambda: images.rounded_rect(w - 20, h - 6, 16, T.HOVER))
            c.create_image(10, y + 3, image=img, anchor="nw")
        bg = T.BLUE_DEEP if selected else T.HOVER if hover else T.SIDEBAR
        mid = y + h // 2
        if kind == "chat":
            self._render_chat(c, row, y, w, selected, bg, mid)
        elif kind == "friend":
            c.create_image(22, mid, image=user_avatar(self.ctrl, row["username"], 44, bg, row["online"]), anchor="w")
            c.create_text(78, mid - 10, text=T.ellipsize(T.font(16, "semibold"), row["name"], w - 110), anchor="w",
                          fill=T.TEXT, font=T.font(16, "semibold"))
            sub = row["bio"] or "@" + row["username"]
            c.create_text(78, mid + 12, text=T.ellipsize(T.font(13), sub, w - 110), anchor="w", fill=T.MUTED,
                          font=T.font(13))
        elif kind == "request":
            c.create_image(22, mid, image=user_avatar(self.ctrl, row["username"], 44, bg), anchor="w")
            c.create_text(78, mid - 10, text=T.ellipsize(T.font(16, "semibold"), row["fullname"], w - 190), anchor="w",
                          fill=T.TEXT, font=T.font(16, "semibold"))
            c.create_text(78, mid + 12, text="wants to be your friend", anchor="w", fill=T.MUTED, font=T.font(13))
            for name, icon, color, x in (("decline", "x", T.RED, w - 38), ("accept", "check", T.GREEN, w - 84)):
                img = T.photo(("rq", color), lambda col=color: images.circle_shape(36, fill=T.CIRCLE, outline=col, ow=2))
                c.create_image(x, mid, image=img)
                c.create_image(x, mid, image=T.icon(icon, 18, color, 2.4))
                regions.append((x - 18, x + 18, name))
        elif kind == "online":
            c.create_image(22, mid, image=user_avatar(self.ctrl, row["username"], 44, bg, True), anchor="w")
            c.create_text(78, mid - 10, text=T.ellipsize(T.font(16, "semibold"), row["name"], w - 170), anchor="w",
                          fill=T.TEXT, font=T.font(16, "semibold"))
            c.create_text(78, mid + 12, text="@" + row["username"], anchor="w", fill=T.MUTED, font=T.font(13))
            if row["sent"]:
                c.create_text(w - 26, mid, text="Request sent", anchor="e", fill=T.MUTED, font=T.font(13))
            else:
                img = T.photo(("addbtn",), lambda: images.circle_shape(36, fill=T.CIRCLE, outline="#1b4fb4", ow=2))
                c.create_image(w - 42, mid, image=img)
                c.create_image(w - 42, mid, image=T.icon("user_plus", 18, T.BLUE_HI, 2.0))
                regions.append((w - 60, w - 24, "request"))
        elif kind == "group":
            g = row["group"]
            c.create_image(22, mid, image=group_photo(48, bg), anchor="w")
            c.create_text(84, mid - 10, text=T.ellipsize(T.font(16, "semibold"), g["name"], w - 150), anchor="w",
                          fill=T.TEXT, font=T.font(16, "semibold"))
            admin = " \u2022 you are admin" if g["admin"] == self.ctrl.me["username"] else ""
            c.create_text(84, mid + 12, text=f"{len(g['members'])} members{admin}", anchor="w", fill=T.MUTED,
                          font=T.font(13))
            c.create_image(w - 36, mid, image=T.icon("dots_h", 22, T.MUTED), anchor="center")
            regions.append((w - 56, w - 16, "manage"))

    def _render_chat(self, c, row, y, w, selected, bg, mid):
        ctrl = self.ctrl
        if row["kind"] == "chat" and row.get("group_id") is not None:
            c.create_image(20, mid, image=group_photo(50, bg), anchor="w")
        else:
            c.create_image(20, mid, image=user_avatar(ctrl, row["username"], 50, bg, row["online"]), anchor="w")
        last = row["last"]
        c.create_text(84, y + 15, text=T.ellipsize(T.font(16, "semibold"), row["name"], w - 190), anchor="nw",
                      fill=T.TEXT, font=T.font(16, "semibold"))
        if last:
            mine = last["from"] == ctrl.me["username"]
            text = last["text"].replace("\n", " ")
            if mine:
                text = "You: " + text
            elif row.get("group_id") is not None:
                text = f"{first_name(ctrl.name_of(last['from']))}: {text}"
            c.create_text(w - 22, y + 17, text=list_time(last["timestamp"]), anchor="ne", fill=T.TEXT2 if selected else T.MUTED,
                          font=T.font(13))
        else:
            mine, text = False, "No messages yet"
        right_pad = 70 if (row["unread"] or (last and mine)) else 40
        c.create_text(84, y + 40, text=T.ellipsize(T.font(14), text, w - 84 - right_pad), anchor="nw",
                      fill=T.TEXT2 if selected else T.MUTED, font=T.font(14))
        if row["unread"]:
            self._badge(c, w - 36, y + 50, row["unread"], muted=row["muted"])
        elif last and mine:
            c.create_image(w - 30, y + 50, image=T.icon("check2", 19, T.BLUE_HI if not selected else "#8fc0ff", 2.2))
        if row["muted"] and not row["unread"] and not (last and mine):
            c.create_image(w - 30, y + 50, image=T.icon("bell_off", 16, T.DIM, 2.0))


# --------------------------------------------------------------------------- #
# conversation
# --------------------------------------------------------------------------- #
class ChatPane(tk.Frame):
    def __init__(self, parent, main):
        super().__init__(parent, bg=T.BG)
        self.main, self.ctrl = main, main.ctrl
        self.header = HitCanvas(self, T.HEADER, height=80)
        self.header.pack(fill="x")
        self.header.bind("<Configure>", lambda _e: self.draw_header())
        tk.Frame(self, bg=T.LINE, height=1).pack(fill="x")

        self.view = MessageView(self, on_copy=lambda: main.toast("Message copied."))
        self.view.pack(fill="both", expand=True)

        self.bar = HitCanvas(self, T.BG, height=96)
        self.bar.pack(fill="x", side="bottom")
        self.bar.bind("<Configure>", lambda _e: self.draw_bar())
        self.var = tk.StringVar()
        self.entry = tk.Entry(self.bar, textvariable=self.var, bd=0, relief="flat", font=T.font(16), bg=T.FIELD,
                              fg=T.TEXT, insertbackground=T.TEXT, disabledbackground=T.FIELD,
                              selectbackground=T.BLUE, selectforeground="#ffffff", highlightthickness=0)
        self.entry.bind("<Return>", lambda _e: self.send())
        self.ph = tk.Label(self.bar, text="Type a message...", font=T.font(16), bg=T.FIELD, fg=T.DIM, anchor="w",
                           bd=0, cursor="xterm")
        self.ph.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.var.trace_add("write", lambda *_: self.bar.itemconfigure("ph", state="hidden" if self.var.get() else "normal"))
        self._wall = None

    # ---- header ----------------------------------------------------------- #
    def draw_header(self):
        c, w = self.header, max(self.header.winfo_width(), 300)
        c.reset()
        key, ctrl = ctrl_key(self.ctrl), self.ctrl
        if not key:
            return
        c.create_image(32, 40, image=T.icon("chevron_left", 26, T.TEXT2, 2.2))
        c.hit(12, 14, 56, 66, self.main.close_conversation)
        if key.startswith("dm:"):
            u = key[3:]
            online = ctrl.friends.get(u, {}).get("online", False)
            c.create_image(68, 40, image=user_avatar(ctrl, u, 52, T.HEADER, online), anchor="w")
            title = ctrl.friends.get(u, {}).get("fullname", u)
            status, color = ("Online", "#6ea8ff") if online else ("Offline", T.MUTED)
        else:
            g = ctrl.groups.get(int(key[6:]))
            c.create_image(68, 40, image=group_photo(52, T.HEADER), anchor="w")
            title = g["name"] if g else ""
            status, color = (f"{len(g['members'])} members" if g else ""), T.MUTED
        c.create_text(136, 28, text=T.ellipsize(T.font(19, "semibold"), title, w - 330), anchor="w", fill=T.TEXT,
                      font=T.font(19, "semibold"))
        c.create_text(136, 54, text=status, anchor="w", fill=color, font=T.font(15))
        c.hit(60, 8, w - 150, 72, self.main.toggle_info)
        for name, x, size, cb in (("phone_fill", w - 124, 24, self.main.call_unavailable),
                                  ("video_fill", w - 78, 28, self.main.call_unavailable)):
            c.create_image(x, 40, image=T.icon(name, size, T.BLUE_HI, 1.6))
            c.hit(x - 20, 18, x + 20, 62, cb)
        c.create_image(w - 32, 40, image=T.icon("dots_v", 24, T.TEXT2))
        c.hit(w - 52, 18, w - 12, 62, self.main.show_chat_menu)

    # ---- input bar ---------------------------------------------------------- #
    def draw_bar(self):
        c, w = self.bar, max(self.bar.winfo_width(), 300)
        c.reset()
        img = T.photo(("inputbar", w), lambda: images.rounded_rect(w - 48, 58, 29, "#0c1b31", outline="#12315a"))
        c.create_image(24, 19, image=img, anchor="nw")
        c.create_image(58, 48, image=T.icon("smile", 28, T.MUTED, 1.8))
        c.hit(36, 28, 82, 68, self.main.show_emoji)
        inner = max(40, w - 96 - 230)
        c.create_window(96, 48, window=self.entry, anchor="w", width=inner, height=34)
        c.create_window(96, 48, window=self.ph, anchor="w", width=inner, height=34, tags="ph",
                        state="hidden" if self.var.get() else "normal")
        c.create_image(w - 192, 48, image=T.icon("paperclip", 26, T.MUTED, 1.8))
        c.hit(w - 214, 28, w - 170, 68, self.main.attach_unavailable)
        c.create_image(w - 140, 48, image=T.icon("image", 26, T.MUTED, 1.8))
        c.hit(w - 162, 28, w - 118, 68, self.main.attach_unavailable)
        send = T.photo(("sendbtn",), lambda: images.gradient_rrect(48, 48, 24, "#1a7bff", "#0a60f0"))
        c.create_image(w - 36 - 48 + 4, 24, image=send, anchor="nw")
        c.create_image(w - 36 - 24 + 4, 48, image=T.icon("send", 24, "#ffffff", 1.6))
        c.hit(w - 84, 24, w - 28, 72, self.send)

    def set_enabled(self, on):
        self.entry.configure(state="normal" if on else "disabled")

    # ---- messages ---------------------------------------------------------------- #
    def render_messages(self, stick=True):
        ctrl, key = self.ctrl, self.ctrl.current
        wall = ctrl.prefs.wallpaper(ctrl.me["username"]) if ctrl.me else "doodle"
        if wall != self._wall:
            self._wall = wall
            self.view.set_wallpaper(wall)
        if not key:
            self.view.set_items([{"type": "empty", "text": "Select a conversation to start chatting.\n\n"
                                                           "Messages are encrypted on your computer\nbefore they are sent."}])
            return
        msgs = ctrl.conversation(key)
        items, last_day, me = [], None, ctrl.me["username"]
        if not msgs:
            items.append({"type": "empty", "text": "Your conversation starts here.\nSend a message to say hello."})
        is_group = key.startswith("group:")
        for m in msgs:
            label = day_label(m["timestamp"])
            if label and label != last_day:
                items.append({"type": "day", "text": label})
                last_day = label
            mine = m["from"] == me
            item = {"type": "msg", "mine": mine, "text": m["text"], "ok": m["ok"], "time": clock(m["timestamp"])}
            if not mine:
                item["avatar"] = user_avatar(ctrl, m["from"], 38, T.BG)
                if is_group:
                    item["sender"] = ctrl.name_of(m["from"])
                    item["name_color"] = name_color(m["from"])
            items.append(item)
        self.view.set_items(items, stick=stick)

    def refresh(self):
        self.draw_header()
        self.render_messages()
        self.set_enabled(bool(self.ctrl.current))

    def send(self):
        if self.ctrl.send_text(self.entry.get()):
            self.entry.delete(0, tk.END)


def ctrl_key(ctrl):
    key = ctrl.current
    if key and not ctrl._conversation_exists(key):
        return None
    return key


# --------------------------------------------------------------------------- #
# contact / group info
# --------------------------------------------------------------------------- #
class InfoPane(HitCanvas):
    def __init__(self, parent, main):
        super().__init__(parent, T.RIGHT, width=INFO_W)
        self.main, self.ctrl = main, main.ctrl
        self.bind("<Configure>", lambda _e: self.draw())
        bind_wheel(self, self._wheel)

    def _wheel(self, event):
        if self._height > self.winfo_height():
            self.yview_scroll(_wheel_units(event), "units")

    _height = 0

    def draw(self):
        c, ctrl, main = self, self.ctrl, self.main
        w = max(self.winfo_width(), 200)
        c.reset()
        key = ctrl_key(ctrl)
        cx = w // 2
        y = 34
        if not key:
            self._draw_me(w)
            return
        is_group = key.startswith("group:")
        if is_group:
            g = ctrl.groups[int(key[6:])]
            c.create_image(cx, y + 52, image=group_photo(104, T.RIGHT))
            title, sub = g["name"], f"{len(g['members'])} members"
            online, bio = False, ""
        else:
            u = key[3:]
            f = ctrl.friends.get(u, {})
            online = f.get("online", False)
            c.create_image(cx, y + 52, image=user_avatar(ctrl, u, 104, T.RIGHT, online))
            title, bio = f.get("fullname", u), ctrl.bio_of(u)
        y += 122
        c.create_text(cx, y + 16, text=T.ellipsize(T.font(23, "semibold"), title, w - 40), fill=T.TEXT,
                      font=T.font(23, "semibold"))
        y += 42
        if is_group:
            c.create_text(cx, y + 10, text=sub, fill=T.MUTED, font=T.font(15))
            y += 48
        else:
            col, label = (T.GREEN, "Online") if online else (T.DIM, "Offline")
            fnt = T.font(15)
            tw = fnt.measure(label) + 22
            x0 = cx - tw // 2
            c.create_image(x0 + 5, y + 10, image=T.photo(("dot", col), lambda: images.circle_shape(10, fill=col)))
            c.create_text(x0 + 22, y + 10, text=label, anchor="w", fill=T.MUTED, font=fnt)
            y += 30
            if bio:
                c.create_text(cx, y + 6, text=bio, fill=T.TEXT2, font=T.font(15), width=w - 48, justify="center",
                              anchor="n")
                y += 12 + T.font(15).metrics("linespace") * (1 + T.font(15).measure(bio) // (w - 48))
            y += 10
        # round action buttons
        acts = [("Call", "phone_fill", main.call_unavailable), ("Video", "video_fill", main.call_unavailable),
                ("Info", "info", main.show_info_dialog), ("More", "dots_h", main.show_chat_menu)]
        for i, (label, icon, cb) in enumerate(acts):
            x = int(w * (2 * i + 1) / 8)
            ring = T.photo(("act",), lambda: images.circle_shape(58, fill=T.CIRCLE, outline="#1b4fb4", ow=2))
            c.create_image(x, y + 29, image=ring)
            c.create_image(x, y + 29, image=T.icon(icon, 24 if icon != "dots_h" else 26, "#ffffff", 1.6))
            c.create_text(x, y + 72, text=label, fill=T.TEXT2, font=T.font(14))
            c.hit(x - 32, y, x + 32, y + 84, cb)
        y += 104
        y = self._divider(y, w)

        if is_group:
            y = self._section(y, w, "Members", None)
            members = g["members"]
            for m in members[:6]:
                c.create_image(24, y + 20, image=user_avatar(ctrl, m["username"], 38, T.RIGHT,
                                                            ctrl.friends.get(m["username"], {}).get("online")),
                               anchor="w")
                who = "You" if m["username"] == ctrl.me["username"] else m["fullname"]
                tag = "  \u2022 admin" if m["username"] == g["admin"] else ""
                c.create_text(74, y + 20, text=T.ellipsize(T.font(15), who + tag, w - 100), anchor="w", fill=T.TEXT,
                              font=T.font(15))
                y += 48
            if len(members) > 6:
                c.create_text(24, y + 6, text=f"and {len(members) - 6} more", anchor="w", fill=T.MUTED, font=T.font(14))
                y += 28
            y = self._divider(y + 4, w)
        else:
            y = self._section(y, w, "Media & Files", "See all", self.main.media_unavailable)
            y = self._empty_box(y, w, "image_fill", "No photos or files shared yet")
            y = self._divider(y + 6, w)
            y = self._section(y, w, "Shared Documents", None)
            y = self._empty_box(y, w, "file", "No documents shared yet", compact=True)
            y = self._divider(y + 6, w)

        y = self._section(y, w, "Shared Links", None)
        links = ctrl.shared_links(key, 3)
        if not links:
            y = self._empty_box(y, w, "link", "No links shared yet", compact=True)
        for url, host in links:
            tile = T.photo(("linktile",), lambda: images.rounded_rect(46, 46, 12, T.BLUE))
            c.create_image(24, y + 6, image=tile, anchor="nw")
            c.create_image(47, y + 29, image=T.icon("link", 22, "#ffffff", 2.0))
            c.create_text(86, y + 19, text=T.ellipsize(T.font(14), url, w - 140), anchor="w", fill=T.LINK, font=T.font(14))
            c.create_text(86, y + 40, text=host, anchor="w", fill=T.MUTED, font=T.font(13))
            c.create_image(w - 28, y + 29, image=T.icon("chevron_right", 18, T.MUTED, 2.0))
            c.hit(14, y, w - 10, y + 58, lambda u=url: open_link(u))
            y += 62
        y = self._divider(y + 6, w)

        # settings rows
        muted = ctrl.is_muted(key)
        c.create_image(34, y + 25, image=T.icon("bell_off" if muted else "bell", 22, T.TEXT2, 1.8))
        c.create_text(70, y + 25, text="Mute Notifications", anchor="w", fill=T.TEXT, font=T.font(15))
        c.create_image(w - 24 - 23, y + 25, image=T.photo(("toggle", muted), lambda: images.toggle(muted)))
        c.hit(10, y, w - 10, y + 50, lambda: main.set_muted(key, not muted))
        y += 52
        for icon, label, color, cb, chevron in (
                ("wallpaper", "Chat Wallpaper", T.TEXT, main.choose_wallpaper, True),
                ("trash", "Clear Chat", T.TEXT, main.clear_chat, False),
                *(([("users", "Manage Group", T.TEXT, main.manage_current_group, True),
                    ("logout", "Leave Group", T.RED, main.leave_current_group, False)]) if is_group else
                  [("ban", "Block Contact", T.RED, main.block_current, False)])):
            c.create_image(34, y + 25, image=T.icon(icon, 22, color if color == T.RED else T.TEXT2, 1.8))
            c.create_text(70, y + 25, text=label, anchor="w", fill=color, font=T.font(15))
            if chevron:
                c.create_image(w - 28, y + 25, image=T.icon("chevron_right", 18, T.MUTED, 2.0))
            c.hit(10, y, w - 10, y + 50, cb)
            y += 52
        self._finish(y + 24, w)

    # ---- helpers ------------------------------------------------------------------- #
    def _finish(self, y, w):
        self._height = y
        self.configure(scrollregion=(0, 0, w, max(y, self.winfo_height())))

    def _divider(self, y, w):
        self.create_line(20, y, w - 20, y, fill=T.LINE)
        return y + 18

    def _section(self, y, w, title, link, cb=None):
        self.create_text(24, y + 14, text=title, anchor="w", fill=T.TEXT, font=T.font(17, "semibold"))
        if link:
            self.create_text(w - 24, y + 14, text=link, anchor="e", fill=T.LINK, font=T.font(14))
            self.hit(w - 100, y, w - 10, y + 28, cb)
        return y + 38

    def _empty_box(self, y, w, icon, text, compact=False):
        h = 46 if compact else 88
        img = T.photo(("emptybox", w, h), lambda: images.rounded_rect(w - 48, h, 14, "#071225", outline=T.LINE))
        self.create_image(24, y, image=img, anchor="nw")
        if compact:
            self.create_image(48, y + h // 2, image=T.icon(icon, 18, T.DIM, 1.8))
            self.create_text(70, y + h // 2, text=text, anchor="w", fill=T.MUTED, font=T.font(14))
        else:
            self.create_image(w // 2, y + 32, image=T.icon(icon, 26, T.DIM, 1.8))
            self.create_text(w // 2, y + 64, text=text, fill=T.MUTED, font=T.font(14))
        return y + h + 4

    def _draw_me(self, w):
        c, ctrl, main = self, self.ctrl, self.main
        cx, y = w // 2, 34
        me = ctrl.me["username"]
        c.create_image(cx, y + 52, image=user_avatar(ctrl, me, 104, T.RIGHT, True))
        y += 122
        c.create_text(cx, y + 16, text=T.ellipsize(T.font(23, "semibold"), ctrl.me["fullname"], w - 40), fill=T.TEXT,
                      font=T.font(23, "semibold"))
        c.create_text(cx, y + 48, text="@" + me, fill=T.MUTED, font=T.font(15))
        bio = ctrl.me.get("bio", "")
        c.create_text(cx, y + 76, text=bio or "Add a status in Settings", fill=T.TEXT2 if bio else T.DIM,
                      font=T.font(15), width=w - 48, justify="center", anchor="n")
        y += 130
        btn = T.photo(("editbtn", w), lambda: images.gradient_rrect(w - 80, 46, 23, "#1a74ff", "#0a55e0"))
        c.create_image(40, y, image=btn, anchor="nw")
        c.create_image(w // 2 - 54, y + 23, image=T.icon("pencil", 18, "#ffffff", 2.0))
        c.create_text(w // 2 - 36, y + 23, text="Edit profile", anchor="w", fill="#ffffff", font=T.font(15, "semibold"))
        c.hit(40, y, w - 40, y + 46, lambda: main.set_view("settings"))
        y += 80
        self._divider(y, w)
        c.create_image(cx, y + 56, image=T.icon("lock", 26, T.DIM, 1.8))
        c.create_text(cx, y + 100, text="Pick a conversation to see\ncontact details here.\n\nYour messages are encrypted\n"
                                         "with AES-256-GCM.", fill=T.MUTED, font=T.font(14), justify="center")
        self._finish(y + 160, w)
