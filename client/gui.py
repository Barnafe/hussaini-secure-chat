"""Tkinter desktop client for Hussaini Secure Chat.

The GUI is deliberately thin: all logic lives in ``ChatController``.
"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from client.controller import ChatController, dm_key, group_key, local_time
from shared import settings as app_settings

APP_TITLE = "Hussaini Secure Chat"
MONO = ("Consolas", 17, "bold")


def default_server():
    """Server address shown in the login window (settings.txt: server_address)."""
    return app_settings.load()["server_address"]

# Professional midnight-blue theme with cyan accents.
BG = "#0b1220"
PANEL = "#111c2e"
PANEL_ALT = "#17253a"
FIELD = "#0d1728"
ACCENT = "#38bdf8"
ACCENT_DARK = "#087eae"
TEXT = "#e7eef8"
MUTED = "#9aabc2"
OK_COLOR = "#34d399"
BAD_COLOR = "#fb7185"
ME_BG = "#123c56"
OTHER_BG = "#1d2b40"


# --------------------------------------------------------------------------- #
# Login / register screen
# --------------------------------------------------------------------------- #
class LoginFrame(ttk.Frame):
    def __init__(self, app):
        super().__init__(app, padding=24)
        self.app = app
        self.server = tk.StringVar(value=default_server())
        self.l_user, self.l_pass, self.l_phrase = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.r_name, self.r_user = tk.StringVar(), tk.StringVar()
        self.r_pass, self.r_conf = tk.StringVar(), tk.StringVar()
        self.status = tk.StringVar()
        self.passphrase_dialog = None        # the "your new passphrase" window, while it is open
        self.new_passphrase = None           # the passphrase created by the last registration

        wrap = ttk.Frame(self, style="Card.TFrame", padding=(30, 26))
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(wrap, text="H", style="BrandMark.TLabel").grid(row=0, column=0, columnspan=2, pady=(0, 5))
        ttk.Label(wrap, text=APP_TITLE, style="Brand.TLabel").grid(row=1, column=0, columnspan=2, pady=(0, 3))
        ttk.Label(wrap, text="PRIVATE MESSAGING  \u2022  AES-256-GCM ENCRYPTION",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2, pady=(0, 20))

        ttk.Label(wrap, text="Server (host:port)", style="FieldLabel.TLabel").grid(
            row=3, column=0, sticky="w", pady=(5, 14), padx=(0, 10))
        ttk.Entry(wrap, textvariable=self.server, width=34).grid(row=3, column=1, pady=(5, 14), ipady=4)

        nb = ttk.Notebook(wrap)
        nb.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.notebook = nb
        login_tab, reg_tab = (ttk.Frame(nb, padding=12, style="Panel.TFrame") for _ in range(2))
        nb.add(login_tab, text="  Login  ")
        nb.add(reg_tab, text="  Register  ")

        self._row(login_tab, 0, "Username", self.l_user)
        self._row(login_tab, 1, "Password", self.l_pass, show="*")
        self._row(login_tab, 2, "Passphrase", self.l_phrase, show="*")
        ttk.Label(login_tab, text="The passphrase you were given when you registered.",
                  style="Hint.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        self.login_btn = ttk.Button(login_tab, text="Sign in securely", command=self.do_login, style="Accent.TButton")
        self.login_btn.grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        self._row(reg_tab, 0, "Full name", self.r_name)
        self._row(reg_tab, 1, "Username", self.r_user)
        self._row(reg_tab, 2, "Password", self.r_pass, show="*")
        self._row(reg_tab, 3, "Confirm password", self.r_conf, show="*")
        ttk.Label(reg_tab, text="Your personal passphrase is created for you automatically\n"
                                "and shown once the account is created.",
                  style="Hint.TLabel").grid(row=4, column=0, columnspan=2, sticky="w")
        self.reg_btn = ttk.Button(reg_tab, text="Create account", command=self.do_register, style="Accent.TButton")
        self.reg_btn.grid(row=5, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        self.status_label = ttk.Label(wrap, textvariable=self.status, style="Status.TLabel",
                                      wraplength=420, justify="center")
        self.status_label.grid(row=5, column=0, columnspan=2, pady=(12, 0))
        self.bind_all("<Return>", self._enter)

    @staticmethod
    def _row(parent, row, label, var, show=None):
        ttk.Label(parent, text=label, style="FieldLabel.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
        ttk.Entry(parent, textvariable=var, show=show, width=30).grid(row=row, column=1, pady=5, ipady=4)

    def _enter(self, _event=None):
        if self.winfo_ismapped():
            if self.notebook.index(self.notebook.select()) == 0:
                self.do_login()
            else:
                self.do_register()

    def set_status(self, text, ok=False):
        self.status.set(text)
        self.status_label.configure(foreground=OK_COLOR if ok else BAD_COLOR)

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.login_btn.configure(state=state)
        self.reg_btn.configure(state=state)

    def do_login(self):
        self.set_busy(True)
        self.set_status("Logging in...", ok=True)
        self.app.ctrl.start_auth(self.server.get(), "login", self.l_phrase.get(),
                                 self.l_user.get(), self.l_pass.get())

    def do_register(self):
        self.set_busy(True)
        self.set_status("Creating your account...", ok=True)
        self.app.ctrl.start_auth(self.server.get(), "register", "",
                                 self.r_user.get(), self.r_pass.get(), fullname=self.r_name.get(),
                                 confirm=self.r_conf.get())

    def registered(self, username, message, passphrase=""):
        """The account exists: show the new passphrase and get the login tab ready."""
        self.set_busy(False)
        self.l_user.set(username)
        self.l_pass.set(self.r_pass.get())
        self.l_phrase.set(passphrase)
        self.new_passphrase = passphrase
        for v in (self.r_name, self.r_user, self.r_pass, self.r_conf):
            v.set("")
        self.notebook.select(0)
        self.set_status(message + " Keep your passphrase safe, then sign in.", ok=True)
        if passphrase:
            self.passphrase_dialog = PassphraseDialog(self.winfo_toplevel(), username, passphrase)


# --------------------------------------------------------------------------- #
# Dialogs
# --------------------------------------------------------------------------- #
class _Dialog(tk.Toplevel):
    def __init__(self, parent, title, width=360, height=420):
        super().__init__(parent)
        self.title(title)
        self._place(parent, width, height)
        self.transient(parent)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.after(50, self._grab)

    def _place(self, parent, width, height):
        """Open the dialog in the middle of the main window (not in the screen corner)."""
        try:
            top = parent.winfo_toplevel()
            top.update_idletasks()
            x = top.winfo_rootx() + (top.winfo_width() - width) // 2
            y = top.winfo_rooty() + (top.winfo_height() - height) // 2
            self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        except tk.TclError:
            self.geometry(f"{width}x{height}")

    def _grab(self):
        try:
            self.grab_set()
        except tk.TclError:
            pass


class PassphraseDialog(_Dialog):
    """Shows a newly created passphrase. It is displayed only here, so ask the user to save it."""

    def __init__(self, parent, username, passphrase):
        super().__init__(parent, "Your chat passphrase", 500, 392)
        self.username, self.passphrase = username, passphrase
        self.saved = tk.BooleanVar(value=False)
        ttk.Label(self, text="Account created \u2714", foreground=OK_COLOR,
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=20, pady=(18, 2))
        ttk.Label(self, text=f"This is the personal passphrase for @{username}. You need it every time you "
                             "sign in, and it unlocks your messages.", wraplength=455,
                  justify="left").pack(anchor="w", padx=20)
        box = tk.Entry(self, font=MONO, justify="center", relief="flat", bg=FIELD, fg=ACCENT,
                       readonlybackground=FIELD, insertbackground=ACCENT, highlightthickness=1,
                       highlightbackground=ACCENT_DARK, highlightcolor=ACCENT)
        box.insert(0, passphrase)
        box.configure(state="readonly")
        box.pack(fill="x", padx=20, pady=(16, 8), ipady=10)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=20)
        self.copy_btn = ttk.Button(row, text="Copy", command=self.copy)
        self.copy_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(row, text="Save to a file...", command=self.save_file).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        ttk.Label(self, text="Write it down or save it now. It is shown only once, and nobody (not even the "
                             "server) can recover it for you.", wraplength=455, justify="left",
                  foreground=BAD_COLOR).pack(anchor="w", padx=20, pady=(14, 6))
        ttk.Checkbutton(self, text="I have saved my passphrase", variable=self.saved, style="Dlg.TCheckbutton",
                        command=self._toggle).pack(anchor="w", padx=20)
        self.go_btn = ttk.Button(self, text="Continue to sign in", command=self.destroy,
                                 style="Accent.TButton", state="disabled")
        self.go_btn.pack(fill="x", padx=20, pady=16)
        self.protocol("WM_DELETE_WINDOW", self._try_close)

    def _toggle(self):
        self.go_btn.configure(state="normal" if self.saved.get() else "disabled")

    def _try_close(self):
        if self.saved.get():
            self.destroy()
        else:
            messagebox.showinfo("Save your passphrase",
                                "Please copy or save your passphrase first, then tick "
                                "'I have saved my passphrase'.", parent=self)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.passphrase)
        self.copy_btn.configure(text="Copied \u2714")

    def save_file(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save passphrase", defaultextension=".txt",
            initialfile=f"hussaini-passphrase-{self.username}.txt", filetypes=[("Text file", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"Hussaini Secure Chat\nUsername:   {self.username}\nPassphrase: {self.passphrase}\n"
                         "Keep this file private.\n")
            self.saved.set(True)
            self._toggle()
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self)


class AddFriendDialog(_Dialog):
    """"Find people": everybody on the server is listed straight away, with a search bar to narrow it down."""

    STATUS_TEXT = {"friend": "Friends", "request_sent": "Request sent",
                   "request_received": "Wants to add you - accept in the Friends tab", "none": ""}

    def __init__(self, parent, ctrl):
        super().__init__(parent, "Add friend", 460, 580)
        self.ctrl = ctrl
        self.rows = []                       # usernames, in the order shown
        self._timer = None
        ttk.Label(self, text="Find people", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(14, 0))
        ttk.Label(self, text="Everyone who has an account is listed (\u25cf online, \u25cb offline). Type to search by name or username.",
                  foreground=MUTED, wraplength=430, justify="left").pack(anchor="w", padx=14, pady=(2, 8))
        self.query = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.query)
        self.entry.pack(fill="x", padx=14)
        self.entry.bind("<KeyRelease>", self._typed)
        self.entry.bind("<Return>", lambda e: self.send())
        self.count = ttk.Label(self, text="Loading...", foreground=MUTED)
        self.count.pack(anchor="w", padx=14, pady=(6, 2))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=14)
        self.lb = tk.Listbox(box, exportselection=False, bg=FIELD, fg=TEXT, selectbackground=ACCENT_DARK,
                             selectforeground="white", relief="flat", highlightthickness=1,
                             highlightbackground=PANEL_ALT, font=("Segoe UI", 10), activestyle="none")
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<<ListboxSelect>>", lambda e: self._update_button())
        self.lb.bind("<Double-Button-1>", lambda e: self.send())
        self.msg = ttk.Label(self, text="", foreground=MUTED)
        self.msg.pack(anchor="w", padx=14, pady=(4, 0))
        self.btn = ttk.Button(self, text="Send friend request", command=self.send, state="disabled")
        self.btn.pack(fill="x", padx=14, pady=12)
        self.entry.focus_set()
        self.ctrl.search_users("")

    # ---- searching ---------------------------------------------------- #
    def _typed(self, _event=None):
        if self._timer is not None:
            self.after_cancel(self._timer)
        self._timer = self.after(300, self._search)          # wait until the typing pauses

    def _search(self):
        self._timer = None
        self.ctrl.search_users(self.query.get())

    # ---- showing results ---------------------------------------------- #
    def refresh(self):
        """Redraw from the controller (also called when friend/online information changes)."""
        keep = self.rows[self.lb.curselection()[0]] if self.lb.curselection() and self.rows else None
        users = self.ctrl.user_results
        self.rows = [u["username"] for u in users]
        self.lb.delete(0, tk.END)
        for u in users:
            dot = "\u25cf" if self.ctrl.is_online(u["username"]) else "\u25cb"
            note = self.STATUS_TEXT[self.ctrl.relationship(u["username"])]
            self.lb.insert(tk.END, f" {dot}  {u['fullname']}  (@{u['username']})" + (f"     {note}" if note else ""))
        if keep in self.rows:
            self.lb.selection_set(self.rows.index(keep))
        q = (self.ctrl.user_query or "").strip()
        if not users:
            self.count.configure(text=f'No one matches "{q}".' if q else "No other users have registered yet.")
        elif self.ctrl.user_more:
            self.count.configure(text=f"Showing the first {len(users)} people - type to narrow the list.")
        else:
            self.count.configure(text=f"{len(users)} {'person' if len(users) == 1 else 'people'}"
                                      + (f' matching "{q}"' if q else ""))
        self._update_button()

    def _selected(self):
        sel = self.lb.curselection()
        return self.rows[sel[0]] if sel and self.rows else None

    def _update_button(self):
        user = self._selected()
        rel = self.ctrl.relationship(user) if user else None
        self.btn.configure(state="normal" if rel == "none" else "disabled")
        self.msg.configure(text="" if rel in (None, "none") else self.STATUS_TEXT[rel], foreground=MUTED)

    def send(self):
        user = self._selected()
        if user and self.ctrl.relationship(user) == "none":
            self.ctrl.send_friend_request(user)
            self.msg.configure(text=f"Sending a request to @{user}...", foreground=OK_COLOR)


class CreateGroupDialog(_Dialog):
    def __init__(self, parent, ctrl):
        super().__init__(parent, "New group", 360, 440)
        self.ctrl = ctrl
        self.keys = [u for u, _n, _o in ctrl.friend_list()]
        ttk.Label(self, text="Group name").pack(anchor="w", padx=14, pady=(14, 2))
        self.name = tk.StringVar()
        ttk.Entry(self, textvariable=self.name).pack(fill="x", padx=14)
        ttk.Label(self, text="Add friends (click to select several)").pack(anchor="w", padx=14, pady=(12, 2))
        self.lb = tk.Listbox(self, selectmode=tk.MULTIPLE, exportselection=False, height=12,
                             bg=FIELD, fg=TEXT, selectbackground=ACCENT_DARK,
                             selectforeground="white", relief="flat", highlightthickness=1,
                             highlightbackground=PANEL_ALT, font=("Segoe UI", 10))
        for u, n, _o in ctrl.friend_list():
            self.lb.insert(tk.END, f"{n} (@{u})")
        self.lb.pack(fill="both", expand=True, padx=14)
        self.msg = ttk.Label(self, text="", foreground=BAD_COLOR)
        self.msg.pack(anchor="w", padx=14)
        ttk.Button(self, text="Create group", command=self.create).pack(fill="x", padx=14, pady=12)

    def create(self):
        name = self.name.get().strip()
        picked = [self.keys[i] for i in self.lb.curselection()]
        if not name:
            self.msg.configure(text="Enter a group name.")
            return
        if not picked:
            self.msg.configure(text="Pick at least one friend.")
            return
        self.ctrl.create_group(name, picked)
        self.destroy()


class ManageGroupDialog(_Dialog):
    def __init__(self, parent, ctrl, gid):
        super().__init__(parent, "Manage group", 380, 480)
        self.ctrl, self.gid = ctrl, gid
        self.title_var = tk.StringVar()
        ttk.Label(self, textvariable=self.title_var, font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=14, pady=(14, 6))
        ttk.Label(self, text="Members").pack(anchor="w", padx=14)
        self.lb = tk.Listbox(self, exportselection=False, height=10, bg=FIELD, fg=TEXT,
                             selectbackground=ACCENT_DARK, selectforeground="white", relief="flat",
                             highlightthickness=1, highlightbackground=PANEL_ALT, font=("Segoe UI", 10))
        self.lb.pack(fill="both", expand=True, padx=14, pady=(2, 8))
        self.admin_box = ttk.Frame(self)
        self.admin_box.pack(fill="x", padx=14)
        ttk.Label(self.admin_box, text="Add a friend").pack(anchor="w")
        row = ttk.Frame(self.admin_box)
        row.pack(fill="x", pady=(2, 6))
        self.combo = ttk.Combobox(row, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Add", command=self.add).pack(side="left", padx=(6, 0))
        ttk.Button(self.admin_box, text="Remove selected member", command=self.remove).pack(fill="x")
        ttk.Button(self, text="Leave group", command=self.leave).pack(fill="x", padx=14, pady=12)
        self.members = []
        self.candidates = []
        self.refresh()

    def refresh(self):
        g = self.ctrl.groups.get(self.gid)
        if not g:
            self.destroy()
            return
        me = self.ctrl.me["username"]
        self.title_var.set(g["name"])
        self.members = [m["username"] for m in g["members"]]
        self.lb.delete(0, tk.END)
        for m in g["members"]:
            tag = "  (admin)" if m["username"] == g["admin"] else ""
            you = "  (you)" if m["username"] == me else ""
            self.lb.insert(tk.END, f"{m['fullname']} (@{m['username']}){tag}{you}")
        is_admin = g["admin"] == me
        if is_admin:
            self.admin_box.pack(fill="x", padx=14)
            self.candidates = [(u, n) for u, n, _o in self.ctrl.friend_list() if u not in self.members]
            self.combo["values"] = [f"{n} (@{u})" for u, n in self.candidates]
            self.combo.set("")
        else:
            self.admin_box.pack_forget()

    def add(self):
        i = self.combo.current()
        if i >= 0:
            self.ctrl.group_add(self.gid, self.candidates[i][0])

    def remove(self):
        sel = self.lb.curselection()
        if sel:
            self.ctrl.group_remove(self.gid, self.members[sel[0]])

    def leave(self):
        if messagebox.askyesno("Leave group", "Leave this group?", parent=self):
            self.ctrl.group_leave(self.gid)
            self.destroy()


# --------------------------------------------------------------------------- #
# Main chat screen
# --------------------------------------------------------------------------- #
class MainFrame(ttk.Frame):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.ctrl = app, app.ctrl
        self.friend_keys, self.group_ids, self.online_keys, self.request_keys = [], [], [], []

        top = ttk.Frame(self, padding=(16, 12), style="Card.TFrame")
        top.pack(fill="x")
        self.me_label = ttk.Label(top, font=("Segoe UI", 10, "bold"), foreground=ACCENT, background=PANEL)
        self.me_label.pack(side="left")
        ttk.Button(top, text="Logout", command=app.logout).pack(side="right")
        self.conn_label = ttk.Label(top, text="● Connected", foreground=OK_COLOR, background=PANEL,
                                    font=("Segoe UI", 9, "bold"))
        self.conn_label.pack(side="right", padx=12)
        self.inbox_badge = ttk.Label(top, text="", foreground="#67e8f9", background=PANEL,
                                     font=("Segoe UI", 9, "bold"))
        self.inbox_badge.pack(side="right", padx=12)

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        left = ttk.Frame(body, width=300, style="Panel.TFrame")
        right = ttk.Frame(body, style="Panel.TFrame", padding=12)
        body.add(left, weight=0)
        body.add(right, weight=1)

        nb = ttk.Notebook(left)
        nb.pack(fill="both", expand=True)
        friends_tab, groups_tab, online_tab = (ttk.Frame(nb, padding=8, style="Panel.TFrame") for _ in range(3))
        nb.add(friends_tab, text="Friends")
        nb.add(groups_tab, text="Groups")
        nb.add(online_tab, text="Online")
        self.notebook = nb

        # --- friends tab
        self.req_label = ttk.Label(friends_tab, text="Friend requests", font=("Segoe UI", 9, "bold"))
        self.req_label.pack(anchor="w")
        self.req_lb = tk.Listbox(friends_tab, height=3, exportselection=False, bg=FIELD, fg=TEXT, selectbackground=ACCENT_DARK, selectforeground="white", relief="flat", highlightthickness=1, highlightbackground=PANEL_ALT, font=("Segoe UI", 9))
        self.req_lb.pack(fill="x")
        rb = ttk.Frame(friends_tab)
        rb.pack(fill="x", pady=(2, 8))
        ttk.Button(rb, text="Accept", command=lambda: self.answer_request(True)).pack(side="left", expand=True, fill="x")
        ttk.Button(rb, text="Decline", command=lambda: self.answer_request(False)).pack(side="left", expand=True, fill="x")
        ttk.Label(friends_tab, text="Friends", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.friend_lb = tk.Listbox(friends_tab, exportselection=False, bg=FIELD, fg=TEXT, selectbackground=ACCENT_DARK, selectforeground="white", relief="flat", highlightthickness=1, highlightbackground=PANEL_ALT, font=("Segoe UI", 9))
        self.friend_lb.pack(fill="both", expand=True)
        self.friend_lb.bind("<<ListboxSelect>>", self.pick_friend)
        fb = ttk.Frame(friends_tab)
        fb.pack(fill="x", pady=(4, 0))
        ttk.Button(fb, text="Add friend...", command=self.add_friend).pack(side="left", expand=True, fill="x")
        ttk.Button(fb, text="Remove", command=self.remove_friend).pack(side="left", expand=True, fill="x")

        # --- groups tab
        self.group_lb = tk.Listbox(groups_tab, exportselection=False, bg=FIELD, fg=TEXT, selectbackground=ACCENT_DARK, selectforeground="white", relief="flat", highlightthickness=1, highlightbackground=PANEL_ALT, font=("Segoe UI", 9))
        self.group_lb.pack(fill="both", expand=True)
        self.group_lb.bind("<<ListboxSelect>>", self.pick_group)
        gb = ttk.Frame(groups_tab)
        gb.pack(fill="x", pady=(4, 0))
        ttk.Button(gb, text="New group...", command=self.new_group).pack(side="left", expand=True, fill="x")
        ttk.Button(gb, text="Manage", command=self.manage_group).pack(side="left", expand=True, fill="x")

        # --- online tab
        ttk.Label(online_tab, text="Everyone online right now").pack(anchor="w")
        self.online_lb = tk.Listbox(online_tab, exportselection=False, bg=FIELD, fg=TEXT, selectbackground=ACCENT_DARK, selectforeground="white", relief="flat", highlightthickness=1, highlightbackground=PANEL_ALT, font=("Segoe UI", 9))
        self.online_lb.pack(fill="both", expand=True)
        ttk.Button(online_tab, text="Send friend request", command=self.request_selected_online).pack(
            fill="x", pady=(4, 0))

        # --- chat area
        self.title_var = tk.StringVar(value="Select a friend or group to start chatting")
        ttk.Label(right, textvariable=self.title_var, font=("Segoe UI", 12, "bold"),
                  padding=(10, 10), background=PANEL, foreground=TEXT).pack(fill="x")
        box = ttk.Frame(right)
        box.pack(fill="both", expand=True)
        self.text = tk.Text(box, state="disabled", wrap="word", bg=BG, fg=TEXT, insertbackground=TEXT,
                            relief="flat", padx=14, pady=12, font=("Segoe UI", 10),
                            selectbackground=ACCENT_DARK, highlightthickness=1, highlightbackground=PANEL_ALT)
        sb = ttk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        # Readable, modern chat typography with distinct sender labels and message panels.
        self.text.tag_configure("meta_me", foreground="#67d8ff", font=("Segoe UI Semibold", 9),
                                justify="right", lmargin1=100, lmargin2=100, rmargin=18,
                                spacing1=13, spacing3=4)
        self.text.tag_configure("meta_other", foreground="#a9bad1", font=("Segoe UI Semibold", 9),
                                lmargin1=18, lmargin2=18, rmargin=100, spacing1=13, spacing3=4)
        self.text.tag_configure("body_me", foreground="#f0f9ff", background="#164e70",
                                font=("Segoe UI", 11), justify="right", lmargin1=100,
                                lmargin2=100, rmargin=18, spacing1=4, spacing3=9,
                                borderwidth=0, wrap="word")
        self.text.tag_configure("body_other", foreground="#edf4ff", background="#1c2b40",
                                font=("Segoe UI", 11), lmargin1=18, lmargin2=18,
                                rmargin=100, spacing1=4, spacing3=9, borderwidth=0,
                                wrap="word")
        self.text.tag_configure("bad", foreground=BAD_COLOR, background="#3a1e30")
        self.text.tag_configure("chat_empty", foreground=MUTED, font=("Segoe UI", 11), justify="center")

        entry_row = ttk.Frame(right, padding=(0, 6, 0, 0))
        entry_row.pack(fill="x")
        self.entry = ttk.Entry(entry_row)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.send())
        self.send_btn = ttk.Button(entry_row, text="Send message  ➤", command=self.send, style="Accent.TButton")
        self.send_btn.pack(side="left", padx=(6, 0))

        self.status = tk.StringVar()
        self.status_label = ttk.Label(self, textvariable=self.status, padding=(12, 7), foreground=MUTED, background=BG)
        self.status_label.pack(fill="x")

        self.me_label.configure(text=f"Signed in as {self.ctrl.me['fullname']} (@{self.ctrl.me['username']})")
        self.refresh_all()

    # ---- refresh -------------------------------------------------------- #
    def refresh_all(self):
        self.refresh_requests()
        self.refresh_friends()
        self.refresh_groups()
        self.refresh_online()
        self.refresh_unread_badge()
        self.render_conversation()

    def refresh_unread_badge(self):
        total = sum(max(0, int(count)) for count in self.ctrl.unread.values())
        self.inbox_badge.configure(text=(f"●  {total} UNREAD MESSAGE{'S' if total != 1 else ''}" if total else "✓  ALL CAUGHT UP"))

    @staticmethod
    def _fill(listbox, rows, selected_index=None):
        listbox.delete(0, tk.END)
        for r in rows:
            listbox.insert(tk.END, r)
        if selected_index is not None:
            listbox.selection_set(selected_index)

    def _unread(self, key):
        """Make unread conversations impossible to miss in the conversation list."""
        n = self.ctrl.unread.get(key, 0)
        return f"   ● {n} UNREAD" if n else ""

    def refresh_requests(self):
        reqs = self.ctrl.requests_in
        self.request_keys = [r["username"] for r in reqs]
        self._fill(self.req_lb, [f"{r['fullname']} (@{r['username']})" for r in reqs])
        self.req_label.configure(text=f"Friend requests ({len(reqs)})" if reqs else "Friend requests")

    def refresh_friends(self):
        rows = self.ctrl.friend_list()
        self.friend_keys = [u for u, _n, _o in rows]
        sel = self.friend_keys.index(self.ctrl.current[3:]) if (
            self.ctrl.current and self.ctrl.current.startswith("dm:")
            and self.ctrl.current[3:] in self.friend_keys) else None
        self._fill(self.friend_lb,
                   [f"{'●' if o else '○'} {n} (@{u}){self._unread(dm_key(u))}" for u, n, o in rows], sel)
        for i, (u, _name, _online) in enumerate(rows):
            if self.ctrl.unread.get(dm_key(u), 0):
                self.friend_lb.itemconfig(i, foreground="#67e8f9", background="#153047")
        self.refresh_unread_badge()

    def refresh_groups(self):
        groups = self.ctrl.group_list()
        self.group_ids = [g["group_id"] for g in groups]
        sel = self.group_ids.index(int(self.ctrl.current[6:])) if (
            self.ctrl.current and self.ctrl.current.startswith("group:")
            and int(self.ctrl.current[6:]) in self.group_ids) else None
        self._fill(self.group_lb, [f"{g['name']}  [{len(g['members'])}]{self._unread(group_key(g['group_id']))}"
                                   for g in groups], sel)
        for i, g in enumerate(groups):
            if self.ctrl.unread.get(group_key(g['group_id']), 0):
                self.group_lb.itemconfig(i, foreground="#67e8f9", background="#153047")
        self.refresh_unread_badge()

    def refresh_online(self):
        rows = self.ctrl.online_list()
        self.online_keys = [u for u, _n in rows]
        self._fill(self.online_lb, [f"{n} (@{u})" for u, n in rows])

    def render_conversation(self):
        key = self.ctrl.current
        self.title_var.set(self.ctrl.title_for(key) if key else "Select a friend or group to start chatting")
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", tk.END)
        if key:
            messages = self.ctrl.conversation(key)
            if not messages:
                t.insert(tk.END, "\n\n\n  Your conversation starts here\n  Send a message to say hello.", "chat_empty")
            me = self.ctrl.me["username"]
            for m in messages:
                mine = m["from"] == me
                who = "YOU" if mine else self.ctrl.name_of(m["from"])
                meta_tag = "meta_me" if mine else "meta_other"
                body_tag = "body_me" if mine else "body_other"
                t.insert(tk.END, f"{who}   ·   {local_time(m['timestamp'])}\n", meta_tag)
                tags = (body_tag,) + (() if m["ok"] else ("bad",))
                # Preserve user text exactly while keeping the colored message panel inset.
                t.insert(tk.END, f"  {m['text']}  \n", tags)
        t.configure(state="disabled")
        t.see(tk.END)
        state = "normal" if key else "disabled"
        self.entry.configure(state=state)
        self.send_btn.configure(state=state)

    # ---- user actions ---------------------------------------------------- #
    def pick_friend(self, _e=None):
        sel = self.friend_lb.curselection()
        if sel:
            self.group_lb.selection_clear(0, tk.END)
            self.ctrl.open_conversation(dm_key(self.friend_keys[sel[0]]))
            self.entry.focus_set()

    def pick_group(self, _e=None):
        sel = self.group_lb.curselection()
        if sel:
            self.friend_lb.selection_clear(0, tk.END)
            self.ctrl.open_conversation(group_key(self.group_ids[sel[0]]))
            self.entry.focus_set()

    def send(self):
        text = self.entry.get()
        if self.ctrl.send_text(text):
            self.entry.delete(0, tk.END)

    def add_friend(self):
        self.app.find_dialog = AddFriendDialog(self, self.ctrl)

    def remove_friend(self):
        sel = self.friend_lb.curselection()
        if sel and messagebox.askyesno("Remove friend", "Remove this friend?", parent=self):
            self.ctrl.remove_friend(self.friend_keys[sel[0]])

    def answer_request(self, accept):
        sel = self.req_lb.curselection()
        if sel:
            self.ctrl.respond_request(self.request_keys[sel[0]], accept)

    def request_selected_online(self):
        sel = self.online_lb.curselection()
        if not sel:
            return
        user = self.online_keys[sel[0]]
        if user in self.ctrl.friends:
            self.set_status("You are already friends.")
        else:
            self.ctrl.send_friend_request(user)

    def new_group(self):
        if not self.ctrl.friends:
            self.set_status("Add some friends first - groups are made from your friends.", bad=True)
            return
        CreateGroupDialog(self, self.ctrl)

    def manage_group(self):
        sel = self.group_lb.curselection()
        if not sel:
            self.set_status("Select a group first.", bad=True)
            return
        self.app.manage_dialog = ManageGroupDialog(self, self.ctrl, self.group_ids[sel[0]])

    def set_status(self, text, bad=False):
        self.status.set(text)
        self.status_label.configure(foreground=BAD_COLOR if bad else MUTED)

    def set_connection(self, text):
        ok = text.lower().startswith("connected")
        self.conn_label.configure(text=text, foreground=OK_COLOR if ok else BAD_COLOR)


# --------------------------------------------------------------------------- #
# Application window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self, controller=None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1020x660")
        self.minsize(820, 520)
        self.configure(bg=BG)
        self._setup_theme()
        self.ctrl = controller or ChatController()
        self.ctrl.on_update = self.on_update
        self.manage_dialog = None
        self.find_dialog = None
        self.login = None
        self.main = None
        self.show_login()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self._poll)

    def _setup_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam" if "clam" in style.theme_names() else "default")
        self.configure(bg=BG)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL, relief="flat")
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 9))
        # Labels that sit on a card/panel must use the panel colour, otherwise they show as darker boxes.
        style.configure("FieldLabel.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9, "bold"))
        style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 8))
        style.configure("Status.TLabel", background=PANEL, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 8, "bold"))
        style.configure("Brand.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 22, "bold"))
        style.configure("BrandMark.TLabel", background=ACCENT_DARK, foreground="white", font=("Segoe UI", 18, "bold"), padding=(14, 5))
        style.configure("TButton", background=PANEL_ALT, foreground=TEXT, padding=(10, 7), borderwidth=0,
                        bordercolor=PANEL_ALT, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT, focuscolor=PANEL_ALT,
                        font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK), ("disabled", PANEL)],
                  foreground=[("active", "white"), ("disabled", MUTED)],
                  bordercolor=[("active", ACCENT_DARK)], lightcolor=[("active", ACCENT_DARK)],
                  darkcolor=[("active", ACCENT_DARK)])
        style.configure("Accent.TButton", background=ACCENT_DARK, foreground="white",
                        bordercolor=ACCENT_DARK, lightcolor=ACCENT_DARK, darkcolor=ACCENT_DARK, focuscolor=ACCENT_DARK)
        style.map("Accent.TButton", background=[("active", ACCENT), ("pressed", ACCENT_DARK), ("disabled", PANEL_ALT)],
                  bordercolor=[("active", ACCENT)], lightcolor=[("active", ACCENT)], darkcolor=[("active", ACCENT)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                        bordercolor=PANEL_ALT, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT, padding=7)
        style.map("TEntry", bordercolor=[("focus", ACCENT_DARK)], lightcolor=[("focus", ACCENT_DARK)],
                  darkcolor=[("focus", ACCENT_DARK)])
        # The "clam" theme paints a white highlight around notebooks and tabs; recolour every edge.
        style.configure("TNotebook", background=PANEL, borderwidth=0, bordercolor=PANEL,
                        lightcolor=PANEL, darkcolor=PANEL, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=PANEL_ALT, foreground=MUTED, padding=(14, 8),
                        borderwidth=0, bordercolor=PANEL, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT,
                        focuscolor=PANEL_ALT, font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT_DARK), ("active", PANEL)],
                  foreground=[("selected", "white"), ("active", TEXT)],
                  bordercolor=[("selected", PANEL), ("!selected", PANEL)],
                  lightcolor=[("selected", ACCENT_DARK), ("!selected", PANEL_ALT)],
                  darkcolor=[("selected", ACCENT_DARK), ("!selected", PANEL_ALT)])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, focuscolor=PANEL)
        style.configure("Dlg.TCheckbutton", background=BG, foreground=TEXT, focuscolor=BG,
                        font=("Segoe UI", 9, "bold"))
        style.map("Dlg.TCheckbutton", background=[("active", BG)], foreground=[("active", ACCENT)])
        style.configure("TPanedwindow", background=BG)
        style.configure("TScrollbar", background=PANEL_ALT, troughcolor=BG, borderwidth=0, arrowsize=12,
                        bordercolor=PANEL_ALT, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT)
        style.configure("TCombobox", fieldbackground=FIELD, background=PANEL_ALT, foreground=TEXT,
                        arrowcolor=ACCENT, bordercolor=PANEL_ALT, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])

    def _poll(self):
        try:
            self.ctrl.poll()
        finally:
            self.after(100, self._poll)

    def _clear(self):
        for f in (self.login, self.main):
            if f is not None:
                f.destroy()
        self.login = self.main = None
        self.unbind_all("<Return>")

    def show_login(self, message=None, ok=False):
        self._clear()
        self.login = LoginFrame(self)
        self.login.pack(fill="both", expand=True)
        if message:
            self.login.set_status(message, ok)

    def show_main(self):
        self._clear()
        self.main = MainFrame(self)
        self.main.pack(fill="both", expand=True)

    def logout(self):
        self.ctrl.logout()

    def close(self):
        self.ctrl.shutdown()
        self.destroy()

    # ---- controller callbacks ---------------------------------------- #
    def on_update(self, topic, payload=None):
        self._handle_update(topic, payload)
        dlg = self.find_dialog
        if topic in ("user_list", "state", "requests", "presence") and dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.refresh()
            except tk.TclError:
                self.find_dialog = None

    def _handle_update(self, topic, payload=None):
        login, main = self.login, self.main
        if topic == "status" and login:
            login.set_status(payload, ok=True)
        elif topic == "auth_error":
            if login:
                login.set_busy(False)
                login.set_status(payload)
        elif topic == "register_result" and login:
            if payload.get("ok"):
                login.registered(payload.get("username", ""), payload["message"],
                                 payload.get("passphrase", ""))
            else:
                login.set_busy(False)
                login.set_status(payload["message"])
        elif topic == "login":
            if login:
                self.show_main()
        elif topic == "logged_out":
            self.show_login()
        elif topic == "kicked":
            self.show_login(payload)
        elif main is None:
            return
        elif topic == "state":
            main.refresh_all()
        elif topic == "presence":
            main.refresh_friends()
            main.refresh_online()
        elif topic == "requests":
            main.refresh_requests()
        elif topic == "groups":
            main.refresh_groups()
            if self.manage_dialog is not None and self.manage_dialog.winfo_exists():
                self.manage_dialog.refresh()
            if self.ctrl.current is None:
                main.render_conversation()
        elif topic == "unread":
            main.refresh_friends()
            main.refresh_groups()
        elif topic == "conversation":
            if payload == self.ctrl.current:
                main.render_conversation()
        elif topic == "notice":
            main.set_status(payload)
        elif topic == "error":
            main.set_status(payload, bad=True)
        elif topic == "connection":
            main.set_connection(payload)
