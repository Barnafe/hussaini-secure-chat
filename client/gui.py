"""Tkinter desktop client for Hussaini Secure Chat - ChatSphere look.

The GUI is deliberately thin: all logic lives in ``ChatController``. The look (rounded
shapes, gradients, avatars) comes from client/theme.py, images.py, widgets.py and panels.py.
"""
import hashlib
import base64
import zlib
import tkinter as tk
import time
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from client import images, notify
from client import theme as T
from client.controller import ChatController, dm_key, group_key
from client.call_ui import CallManager
from client.prefs import Prefs
from client.resources import resource_path
from client.panels import INFO_W, SIDEBAR_W, ChatPane, InfoPane, Sidebar, group_photo, user_avatar
from client.widgets import PillButton, PillEntry, ScrollFrame, Toast, Toggle
from shared import protocol as P
from shared import settings as app_settings
from shared import version

APP_TITLE = version.APP_NAME
MONO = ("Consolas", 17, "bold")
IMAGE_TYPES = [("Pictures", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All files", "*.*")]
EMOJIS = ("\U0001F600 \U0001F604 \U0001F60A \U0001F609 \U0001F60D \U0001F618 \U0001F60E \U0001F914 \U0001F62E \U0001F622 "
          "\U0001F62D \U0001F621 \U0001F44D \U0001F44E \U0001F44F \U0001F64F \U0001F4AA \U0001F44B \U0001F64C \U0001F91D "
          "\u2764\ufe0f \U0001F525 \u2728 \U0001F389 \U0001F4AF \u2705 \u274C \u2B50 \U0001F680 \U0001F4A1 "
          "\U0001F3B5 \U0001F355 \u2615 \U0001F31F \U0001F308 \U0001F3E0 \U0001F4DA \U0001F4BB \U0001F4F1 \U0001F4F7").split()


def default_server():
    """Server address shown in the login window (settings.txt: server_address)."""
    return app_settings.load()["server_address"]


def fingerprint(public_key_b64):
    """Short, comparable fingerprint of a public key: 'A1B2 C3D4 ...' (SHA-256, first 16 bytes)."""
    try:
        digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest().upper()
    except Exception:
        return ""
    return " ".join(digest[i:i + 4] for i in range(0, 32, 4))


def label(parent, text="", size=14, weight="normal", fg=T.TEXT, bg=T.CARD, **kw):
    return tk.Label(parent, text=text, font=T.font(size, weight), fg=fg, bg=bg, **kw)


# --------------------------------------------------------------------------- #
# Login / register screen
# --------------------------------------------------------------------------- #
class LoginFrame(tk.Canvas):
    def __init__(self, app):
        super().__init__(app, bg=T.BG, highlightthickness=0, bd=0)
        self.app = app
        self.server = tk.StringVar(value=default_server())
        self.l_user, self.l_pass, self.l_phrase = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.r_name, self.r_user = tk.StringVar(), tk.StringVar()
        self.r_pass, self.r_conf = tk.StringVar(), tk.StringVar()
        self.status = tk.StringVar()
        self.passphrase_dialog = None        # the "your new passphrase" window, while it is open
        self.new_passphrase = None           # the passphrase created by the last registration
        self._bg_size = None
        self.bind("<Configure>", self._paint_bg)
        PillButton(self, "About", app.show_about, style="secondary", width=90, height=38, font_px=13, bg=T.BG
                   ).place(relx=1.0, rely=1.0, anchor="se", x=-18, y=-18)

        card = tk.Frame(self, bg=T.CARD, highlightthickness=1, highlightbackground=T.LINE)
        self.card = card
        self.create_window(0, 0, window=card, tags="card")
        inner = tk.Frame(card, bg=T.CARD)
        inner.pack(padx=40, pady=30)
        head = tk.Frame(inner, bg=T.CARD)
        head.pack(pady=(0, 4))
        tk.Label(head, image=T.icon("logo", 54, T.BLUE_HI, 1.9), bg=T.CARD).pack(side="left", padx=(0, 12))
        tk.Label(head, text=T.BRAND[0], font=T.font(30, "bold"), fg=T.TEXT, bg=T.CARD).pack(side="left")
        tk.Label(head, text=T.BRAND[1], font=T.font(30, "bold"), fg=T.BLUE_HI, bg=T.CARD).pack(side="left")
        label(inner, "Private messaging  \u2022  AES-256-GCM encryption", 12, fg=T.MUTED).pack(pady=(0, 16))

        label(inner, "Server (host:port)", 13, "semibold", T.MUTED).pack(anchor="w")
        PillEntry(inner, textvariable=self.server, width=380, height=44, radius=14).pack(pady=(4, 14))

        tabs = tk.Frame(inner, bg=T.CARD)
        tabs.pack(fill="x")
        self.tab_labels = []
        for i, text in enumerate(("Login", "Register")):
            lb = tk.Label(tabs, text=text, font=T.font(16, "semibold"), bg=T.CARD, cursor="hand2", pady=6)
            lb.pack(side="left", expand=True, fill="x")
            lb.bind("<Button-1>", lambda _e, i=i: self.show_tab(i))
            self.tab_labels.append(lb)
        tk.Frame(inner, bg=T.LINE, height=1).pack(fill="x", pady=(0, 14))

        self.login_tab, self.reg_tab = tk.Frame(inner, bg=T.CARD), tk.Frame(inner, bg=T.CARD)
        self._field(self.login_tab, "Username", self.l_user)
        self._field(self.login_tab, "Password", self.l_pass, show="\u2022")
        self._field(self.login_tab, "Passphrase", self.l_phrase, show="\u2022")
        label(self.login_tab, "The passphrase you were given when you registered.", 11, fg=T.MUTED).pack(anchor="w")
        self.login_btn = PillButton(self.login_tab, "Sign in securely", self.do_login, width=380, height=48)
        self.login_btn.pack(pady=(14, 0))

        self._field(self.reg_tab, "Full name", self.r_name)
        self._field(self.reg_tab, "Username", self.r_user)
        self._field(self.reg_tab, "Password", self.r_pass, show="\u2022")
        self._field(self.reg_tab, "Confirm password", self.r_conf, show="\u2022")
        label(self.reg_tab, "Your personal passphrase is created for you automatically\n"
                            "and shown once the account is created.", 11, fg=T.MUTED, justify="left").pack(anchor="w")
        self.reg_btn = PillButton(self.reg_tab, "Create account", self.do_register, width=380, height=48)
        self.reg_btn.pack(pady=(14, 0))

        self.status_label = tk.Label(inner, textvariable=self.status, font=T.font(13), bg=T.CARD, fg=T.RED,
                                     wraplength=380, justify="center")
        self.status_label.pack(pady=(12, 0))
        self.tab = 0
        self.show_tab(0)
        self.bind_all("<Return>", self._enter)

    @staticmethod
    def _field(parent, text, var, show=None):
        label(parent, text, 13, "semibold", T.MUTED).pack(anchor="w", pady=(0, 3))
        PillEntry(parent, textvariable=var, show=show, width=380, height=44, radius=14).pack(pady=(0, 10))

    def _paint_bg(self, event):
        w, h = event.width, event.height
        if w < 20:
            return
        self.coords("card", w // 2, h // 2)
        bw, bh = -(-w // 300) * 300, -(-h // 300) * 300
        if self._bg_size != (bw, bh):
            self._bg_size = (bw, bh)
            self.delete("bg")
            img = T.photo(("wall", "doodle", bw, bh), lambda: images.wallpaper("doodle", bw, bh))
            self.create_image(0, 0, image=img, anchor="nw", tags="bg")
            self.tag_lower("bg")

    def show_tab(self, i):
        self.tab = i
        for j, lb in enumerate(self.tab_labels):
            lb.configure(fg=T.BLUE_HI if j == i else T.MUTED)
        self.login_tab.pack_forget()
        self.reg_tab.pack_forget()
        (self.login_tab, self.reg_tab)[i].pack(fill="x", before=self.status_label)

    def _enter(self, _event=None):
        if self.winfo_ismapped():
            self.do_login() if self.tab == 0 else self.do_register()

    def set_status(self, text, ok=False):
        self.status.set(text)
        self.status_label.configure(fg=T.GREEN if ok else T.RED)

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
        self.show_tab(0)
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
        self.configure(bg=T.CARD)
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


def _listbox(parent, **kw):
    return tk.Listbox(parent, exportselection=False, bg=T.FIELD, fg=T.TEXT, selectbackground=T.BLUE,
                      selectforeground="#ffffff", relief="flat", highlightthickness=1,
                      highlightbackground=T.FIELD_LINE, highlightcolor=T.BLUE, font=T.font(14),
                      activestyle="none", **kw)


class PassphraseDialog(_Dialog):
    """Shows a newly created passphrase. It is displayed only here, so ask the user to save it."""

    def __init__(self, parent, username, passphrase):
        super().__init__(parent, "Your chat passphrase", 520, 440)
        self.username, self.passphrase = username, passphrase
        self.saved = tk.BooleanVar(value=False)
        label(self, "Account created \u2714", 18, "bold", T.GREEN).pack(anchor="w", padx=24, pady=(22, 4))
        label(self, f"This is the personal passphrase for @{username}. You need it every time you "
                    "sign in, and it unlocks your messages.", 13, fg=T.TEXT2, wraplength=470,
              justify="left").pack(anchor="w", padx=24)
        box = tk.Entry(self, font=MONO, justify="center", relief="flat", bg=T.FIELD, fg=T.BLUE_HI,
                       readonlybackground=T.FIELD, insertbackground=T.BLUE_HI, highlightthickness=1,
                       highlightbackground=T.FIELD_LINE, highlightcolor=T.BLUE)
        box.insert(0, passphrase)
        box.configure(state="readonly")
        box.pack(fill="x", padx=24, pady=(18, 10), ipady=12)
        row = tk.Frame(self, bg=T.CARD)
        row.pack(fill="x", padx=24)
        self.copy_btn = PillButton(row, "Copy", self.copy, style="secondary", width=220, height=42, font_px=14)
        self.copy_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))
        PillButton(row, "Save to a file...", self.save_file, style="secondary", width=220, height=42,
                   font_px=14).pack(side="left", expand=True, fill="x", padx=(5, 0))
        label(self, "Write it down or save it now. It is shown only once, and nobody (not even the "
                    "server) can recover it for you.", 12, fg=T.RED, wraplength=470, justify="left"
              ).pack(anchor="w", padx=24, pady=(16, 8))
        ttk.Checkbutton(self, text="I have saved my passphrase", variable=self.saved, style="Dlg.TCheckbutton",
                        command=self._toggle).pack(anchor="w", padx=24)
        self.go_btn = PillButton(self, "Continue to sign in", self.destroy, width=470, height=46)
        self.go_btn.configure(state="disabled")
        self.go_btn.pack(fill="x", padx=24, pady=18)
        self.protocol("WM_DELETE_WINDOW", self._try_close)

    def _toggle(self):
        self.go_btn.configure(state="normal" if self.saved.get() else "disabled")

    def _try_close(self):
        if self.saved.get():
            self.destroy()
        else:
            messagebox.showinfo("Save your passphrase", "Please copy or save your passphrase first, then tick "
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
                   "request_received": "Wants to add you - accept in your requests", "none": ""}

    def __init__(self, parent, ctrl):
        super().__init__(parent, "Add friend", 460, 580)
        self.ctrl = ctrl
        self.rows = []                       # usernames, in the order shown
        self._timer = None
        label(self, "Find people", 18, "bold").pack(anchor="w", padx=24, pady=(20, 2))
        label(self, "Everyone who has an account is listed (\u25cf online, \u25cb offline). "
                    "Type to search by name or username.", 12, fg=T.MUTED, wraplength=410, justify="left"
              ).pack(anchor="w", padx=24)
        self.query = tk.StringVar()
        self.field = PillEntry(self, textvariable=self.query, placeholder="Search people", icon="search",
                               width=410, height=44)
        self.field.pack(padx=24, pady=(10, 4))
        self.query.trace_add("write", lambda *_: self._typed())
        self.field.entry.bind("<Return>", lambda _e: self.send())
        self.count = label(self, "Loading...", 12, fg=T.MUTED)
        self.count.pack(anchor="w", padx=24, pady=(2, 2))
        box = tk.Frame(self, bg=T.CARD)
        box.pack(fill="both", expand=True, padx=24)
        self.lb = _listbox(box, height=10)
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<<ListboxSelect>>", lambda _e: self._update_button())
        self.lb.bind("<Double-Button-1>", lambda _e: self.send())
        self.msg = label(self, "", 12, fg=T.MUTED)
        self.msg.pack(anchor="w", padx=24, pady=(4, 0))
        self.btn = PillButton(self, "Send friend request", self.send, width=410, height=46)
        self.btn.configure(state="disabled")
        self.btn.pack(padx=24, pady=(4, 18))
        self.field.focus_set()
        self.ctrl.search_users("")

    # ---- searching ---------------------------------------------------- #
    def _typed(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
        self._timer = self.after(300, self._search)          # wait until the typing pauses

    def _search(self):
        self._timer = None
        self.ctrl.search_users(self.query.get())

    def destroy(self):
        if self._timer is not None:
            try:
                self.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None
        super().destroy()

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
        self.msg.configure(text="" if rel in (None, "none") else self.STATUS_TEXT[rel], fg=T.MUTED)

    def send(self):
        user = self._selected()
        if user and self.ctrl.relationship(user) == "none":
            self.ctrl.send_friend_request(user)
            self.msg.configure(text=f"Sending a request to @{user}...", fg=T.GREEN)


class AboutDialog(_Dialog):
    """Version, credits and the technology behind the program."""

    def __init__(self, parent):
        super().__init__(parent, "About " + version.APP_NAME, 460, 500)
        self._image = None
        try:
            self._image = tk.PhotoImage(file=resource_path("assets", "icon.png")).subsample(2, 2)
            tk.Label(self, image=self._image, bg=T.CARD).pack(pady=(20, 4))
        except tk.TclError:
            pass
        label(self, version.APP_NAME, 20, "bold").pack()
        label(self, f"Version {version.VERSION}", 14, "semibold", T.BLUE_HI).pack(pady=(2, 10))
        label(self, version.DESCRIPTION, 13, fg=T.TEXT2, wraplength=400, justify="center").pack(padx=24)
        label(self, version.TECH, 12, fg=T.MUTED, wraplength=400, justify="center").pack(padx=24, pady=(8, 14))
        lines = [f"Developed by {version.AUTHOR}", version.INSTITUTION]
        if version.SUPERVISOR:
            lines.append(f"Supervisor: {version.SUPERVISOR}")
        for line in lines:
            label(self, line, 14).pack()
        label(self, "Messages are encrypted on your computer. The server only ever stores unreadable ciphertext.",
              12, fg=T.MUTED, wraplength=400, justify="center").pack(padx=24, pady=(14, 8))
        PillButton(self, "Close", self.destroy, style="secondary", width=400, height=44, font_px=14).pack(pady=(6, 16))


class ChangePasswordDialog(_Dialog):
    """Change the account password (asks for the current one first). The passphrase is not affected."""

    def __init__(self, parent, ctrl):
        super().__init__(parent, "Change password", 420, 520)
        self.ctrl = ctrl
        self.vars = {k: tk.StringVar() for k in ("current", "new", "confirm")}
        label(self, "Change password", 18, "bold").pack(anchor="w", padx=24, pady=(20, 2))
        label(self, "Your passphrase stays the same, so your old chats remain readable.", 12, fg=T.MUTED,
              wraplength=370, justify="left").pack(anchor="w", padx=24, pady=(0, 8))
        self.fields = []
        for key, text in (("current", "Current password"), ("new", "New password (8+ characters)"),
                          ("confirm", "Confirm new password")):
            label(self, text, 12, "semibold", T.MUTED).pack(anchor="w", padx=24, pady=(6, 2))
            f = PillEntry(self, textvariable=self.vars[key], show="\u2022", icon="lock", width=370, height=44)
            f.pack(padx=24)
            f.entry.bind("<Return>", lambda _e: self.submit())
            self.fields.append(f)
        self.msg = label(self, "", 12, fg=T.MUTED, wraplength=370, justify="left")
        self.msg.pack(anchor="w", padx=24, pady=(10, 0))
        self.btn = PillButton(self, "Change password", self.submit, width=370, height=46)
        self.btn.pack(padx=24, pady=14)
        self.fields[0].focus_set()

    def submit(self):
        if self.btn.cget("text") == "Close":
            self.destroy()
            return
        self.msg.configure(text="Working...", fg=T.MUTED)
        self.ctrl.change_password(self.vars["current"].get(), self.vars["new"].get(), self.vars["confirm"].get())

    def result(self, ev):
        ok = bool(ev.get("ok"))
        self.msg.configure(text=ev.get("message", ""), fg=T.GREEN if ok else T.RED)
        if ok:
            for f in self.fields:
                f.entry.configure(state="disabled")
            self.btn.configure(text="Close")


class VerifyDialog(_Dialog):
    """Safety number: two friends compare it to be sure nobody swapped their encryption keys."""

    STATES = {"verified": ("Verified", T.GREEN),
              "unverified": ("Not verified yet", T.MUTED),
              "changed": ("WARNING: the number is different from the one you verified before!", T.RED),
              "unknown": ("No key available for this person.", T.RED)}

    def __init__(self, parent, ctrl, peer):
        super().__init__(parent, "Verify security", 460, 420)
        self.ctrl, self.peer = ctrl, peer
        label(self, f"Verify your chat with {ctrl.name_of(peer)}", 18, "bold", wraplength=410, justify="left"
              ).pack(anchor="w", padx=24, pady=(20, 4))
        label(self, "Compare the number below with the one on their screen (meet, or call them). "
                    "If both are identical, nobody is intercepting your messages.", 12, fg=T.MUTED,
              wraplength=410, justify="left").pack(anchor="w", padx=24)
        number = ctrl.safety_number_for(peer) or "-"
        groups = number.split()
        shown = "\n".join(" ".join(groups[i:i + 3]) for i in range(0, len(groups), 3)) if len(groups) == 6 else number
        tk.Label(self, text=shown, font=("Consolas", 20, "bold"), fg=T.BLUE_HI, bg=T.FIELD, pady=18
                 ).pack(fill="x", padx=24, pady=14)
        self.state = label(self, "", 14, "semibold")
        self.state.pack(anchor="w", padx=24)
        self.btn = PillButton(self, "Mark as verified", self.mark, width=410, height=46)
        self.btn.pack(padx=24, pady=(14, 6))
        PillButton(self, "Close", self.destroy, style="secondary", width=410, height=44, font_px=14).pack(
            padx=24, pady=(0, 14))
        self.refresh()

    def refresh(self):
        state = self.ctrl.verification_state(self.peer)
        text, color = self.STATES[state]
        self.state.configure(text=text, fg=color)
        self.btn.configure(state="disabled" if state in ("verified", "unknown") else "normal",
                           text="Mark as verified" if state != "changed" else "I checked - mark as verified again")

    def mark(self):
        self.ctrl.mark_verified(self.peer)
        self.refresh()


class CreateGroupDialog(_Dialog):
    def __init__(self, parent, ctrl):
        super().__init__(parent, "New group", 380, 500)
        self.ctrl = ctrl
        self.keys = [u for u, _n, _o in ctrl.friend_list()]
        label(self, "New group", 18, "bold").pack(anchor="w", padx=24, pady=(20, 8))
        self.name = tk.StringVar()
        PillEntry(self, textvariable=self.name, placeholder="Group name", icon="users", width=330, height=44
                  ).pack(padx=24)
        label(self, "Add friends (click to select several)", 13, fg=T.MUTED).pack(anchor="w", padx=24, pady=(14, 4))
        self.lb = _listbox(self, selectmode=tk.MULTIPLE, height=10)
        for u, n, _o in ctrl.friend_list():
            self.lb.insert(tk.END, f"  {n}  (@{u})")
        self.lb.pack(fill="both", expand=True, padx=24)
        self.msg = label(self, "", 12, fg=T.RED)
        self.msg.pack(anchor="w", padx=24, pady=(4, 0))
        PillButton(self, "Create group", self.create, width=330, height=46).pack(padx=24, pady=(4, 18))

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
        super().__init__(parent, "Manage group", 400, 660)
        self.ctrl, self.gid = ctrl, gid
        self.title_var = tk.StringVar()
        tk.Label(self, textvariable=self.title_var, font=T.font(19, "bold"), fg=T.TEXT, bg=T.CARD).pack(
            anchor="w", padx=24, pady=(20, 8))
        label(self, "Members", 13, fg=T.MUTED).pack(anchor="w", padx=24)
        self.lb = _listbox(self, height=9)
        self.lb.pack(fill="both", expand=True, padx=24, pady=(4, 10))
        self.admin_box = tk.Frame(self, bg=T.CARD)
        self.admin_box.pack(fill="x", padx=24)
        label(self.admin_box, "Group name", 13, fg=T.MUTED).pack(anchor="w")
        name_row = tk.Frame(self.admin_box, bg=T.CARD)
        name_row.pack(fill="x", pady=(4, 8))
        self.rename_var = tk.StringVar()
        self._last_name = ""
        PillEntry(name_row, textvariable=self.rename_var, width=220, height=40, radius=14).pack(
            side="left", fill="x", expand=True)
        PillButton(name_row, "Rename", self.rename, width=90, height=40, font_px=14).pack(side="left", padx=(8, 0))
        label(self.admin_box, "Add a friend", 13, fg=T.MUTED).pack(anchor="w")
        row = tk.Frame(self.admin_box, bg=T.CARD)
        row.pack(fill="x", pady=(4, 8))
        self.combo = ttk.Combobox(row, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True, ipady=4)
        PillButton(row, "Add", self.add, width=80, height=36, font_px=14).pack(side="left", padx=(8, 0))
        PillButton(self.admin_box, "Remove selected member", self.remove, style="secondary", height=42,
                   font_px=14).pack(fill="x")
        PillButton(self.admin_box, "Make selected member the admin", self.make_admin, style="secondary", height=42,
                   font_px=14).pack(fill="x", pady=(6, 0))
        PillButton(self, "Leave group", self.leave, style="danger", height=44, font_px=14).pack(
            fill="x", padx=24, pady=16)
        self.members, self.candidates = [], []
        self.refresh()

    def refresh(self):
        g = self.ctrl.groups.get(self.gid)
        if not g:
            self.destroy()
            return
        me = self.ctrl.me["username"]
        self.title_var.set(g["name"])
        if self.rename_var.get() in ("", self._last_name):          # do not overwrite what the admin is typing
            self.rename_var.set(g["name"])
        self._last_name = g["name"]
        self.members = [m["username"] for m in g["members"]]
        self.lb.delete(0, tk.END)
        for m in g["members"]:
            tag = "  (admin)" if m["username"] == g["admin"] else ""
            you = "  (you)" if m["username"] == me else ""
            self.lb.insert(tk.END, f"  {m['fullname']} (@{m['username']}){tag}{you}")
        if g["admin"] == me:
            self.admin_box.pack(fill="x", padx=24, before=self.winfo_children()[-1])
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

    def rename(self):
        name = self.rename_var.get().strip()
        if name:
            self.ctrl.group_rename(self.gid, name)

    def make_admin(self):
        sel = self.lb.curselection()
        if not sel:
            return
        user = self.members[sel[0]]
        if user == self.ctrl.me["username"]:
            return
        if messagebox.askyesno("Hand over admin", f"Make {self.ctrl.name_of(user)} the admin of this group?\n"
                               "You will no longer be able to add or remove members.", parent=self):
            self.ctrl.group_transfer(self.gid, user)

    def leave(self):
        if messagebox.askyesno("Leave group", "Leave this group?", parent=self):
            self.ctrl.group_leave(self.gid)
            self.destroy()


class InfoDialog(_Dialog):
    """Contact details, including the encryption-key fingerprint to compare in person."""

    def __init__(self, parent, ctrl, username):
        super().__init__(parent, "Contact info", 440, 470)
        me = username == ctrl.me["username"]
        name = ctrl.me["fullname"] if me else ctrl.name_of(username)
        tk.Label(self, image=user_avatar(ctrl, username, 96, T.CARD), bg=T.CARD).pack(pady=(22, 8))
        label(self, name, 20, "bold").pack()
        label(self, "@" + username, 14, fg=T.MUTED).pack()
        bio = ctrl.bio_of(username)
        if bio:
            label(self, bio, 14, fg=T.TEXT2, wraplength=380).pack(pady=(8, 0))
        tk.Frame(self, bg=T.LINE, height=1).pack(fill="x", padx=24, pady=16)
        label(self, "Encryption key fingerprint", 13, "semibold", T.MUTED).pack(anchor="w", padx=24)
        key = ctrl.identity.public_key_b64 if me else ctrl.peer_keys.get(username, "")
        tk.Label(self, text=fingerprint(key) or "not available yet", font=("Consolas", 14), fg=T.BLUE_HI,
                 bg=T.CARD, wraplength=390, justify="left").pack(anchor="w", padx=24, pady=(4, 6))
        label(self, "Compare this with your contact (in person or by phone). If it matches on both "
                    "screens, nobody is intercepting your chat.", 12, fg=T.MUTED, wraplength=390, justify="left"
              ).pack(anchor="w", padx=24)
        PillButton(self, "Close", self.destroy, style="secondary", width=392, height=44, font_px=14).pack(pady=18)


class EmojiPicker(tk.Toplevel):
    def __init__(self, parent, entry, x, y):
        super().__init__(parent)
        self.overrideredirect(True)
        self.configure(bg=T.LINE)
        self.entry = entry
        frame = tk.Frame(self, bg=T.CARD)
        frame.pack(padx=1, pady=1)
        fam = "Segoe UI Emoji" if "Segoe UI Emoji" in tkfont.families() else T.family()
        for i, e in enumerate(EMOJIS):
            b = tk.Label(frame, text=e, font=(fam, -22), bg=T.CARD, fg=T.TEXT, cursor="hand2", width=2)
            b.grid(row=i // 10, column=i % 10, padx=2, pady=2)
            b.bind("<Button-1>", lambda _e, e=e: self.pick(e))
            b.bind("<Enter>", lambda _e, b=b: b.configure(bg=T.HOVER))
            b.bind("<Leave>", lambda _e, b=b: b.configure(bg=T.CARD))
        self.update_idletasks()
        self.geometry(f"+{max(0, x - self.winfo_reqwidth() + 40)}+{max(0, y - self.winfo_reqheight() - 10)}")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<FocusOut>", lambda _e: self.after(120, self._maybe_close))
        self.after(50, self.focus_force)

    def _maybe_close(self):
        try:
            if self.focus_displayof() is None:
                self.destroy()
        except tk.TclError:
            pass

    def pick(self, e):
        self.entry.insert(tk.INSERT, e)
        self.entry.focus_set()
        self.destroy()


# --------------------------------------------------------------------------- #
# Settings / my profile
# --------------------------------------------------------------------------- #
class SettingsPage(tk.Frame):
    def __init__(self, parent, main):
        super().__init__(parent, bg=T.BG)
        self.main, self.ctrl = main, main.ctrl
        scroll = ScrollFrame(self, T.BG)
        scroll.pack(fill="both", expand=True)
        body = tk.Frame(scroll.body, bg=T.BG)
        body.pack(anchor="n", padx=40, pady=30)
        self.name_var, self.bio_var = tk.StringVar(), tk.StringVar()
        self.old_var, self.new_var, self.conf_var = tk.StringVar(), tk.StringVar(), tk.StringVar()

        tk.Label(body, text="Settings", font=T.font(28, "bold"), fg=T.TEXT, bg=T.BG).pack(anchor="w")
        tk.Label(body, text="Your profile is what your friends see.", font=T.font(14), fg=T.MUTED, bg=T.BG
                 ).pack(anchor="w", pady=(2, 18))

        # ---- profile card
        card = self._card(body)
        top = tk.Frame(card, bg=T.CARD)
        top.pack(fill="x")
        self.avatar_label = tk.Label(top, bg=T.CARD)
        self.avatar_label.pack(side="left", padx=(0, 22))
        side = tk.Frame(top, bg=T.CARD)
        side.pack(side="left", anchor="w")
        self.title_label = tk.Label(side, font=T.font(21, "bold"), fg=T.TEXT, bg=T.CARD)
        self.title_label.pack(anchor="w")
        self.user_label = tk.Label(side, font=T.font(14), fg=T.MUTED, bg=T.CARD)
        self.user_label.pack(anchor="w", pady=(0, 10))
        btns = tk.Frame(side, bg=T.CARD)
        btns.pack(anchor="w")
        self.change_btn = PillButton(btns, "Change photo", self.choose_avatar, width=160, height=40, icon="camera",
                                     font_px=14)
        self.change_btn.pack(side="left")
        self.remove_btn = PillButton(btns, "Remove", self.remove_avatar, style="secondary", width=100, height=40,
                                     font_px=14)
        self.remove_btn.pack(side="left", padx=(8, 0))
        tk.Label(side, text="JPG, PNG, GIF, BMP or WebP. It is cropped to a square.", font=T.font(12), fg=T.DIM,
                 bg=T.CARD).pack(anchor="w", pady=(8, 0))
        self.photo_msg = tk.Label(card, text="", font=T.font(13), bg=T.CARD, fg=T.RED, anchor="w")
        self.photo_msg.pack(anchor="w", pady=(8, 0))

        # ---- details
        card = self._card(body)
        tk.Label(card, text="Your details", font=T.font(17, "semibold"), fg=T.TEXT, bg=T.CARD).pack(anchor="w", pady=(0, 10))
        self.name_field = self._field(card, "Full name", self.name_var, icon="user")
        tk.Label(card, text="Username (cannot be changed)", font=T.font(13, "semibold"), fg=T.MUTED, bg=T.CARD
                 ).pack(anchor="w", pady=(0, 3))
        self.username_var = tk.StringVar()
        PillEntry(card, textvariable=self.username_var, width=440, height=46, radius=14, state="disabled").pack(
            anchor="w", pady=(0, 12))
        tk.Label(card, text="Status (shown under your name)", font=T.font(13, "semibold"), fg=T.MUTED, bg=T.CARD
                 ).pack(anchor="w", pady=(0, 3))
        self.bio_field = PillEntry(card, textvariable=self.bio_var, width=440, height=46, radius=14,
                                   placeholder="Good vibes. Great conversations.")
        self.bio_field.pack(anchor="w")
        self.count_label = tk.Label(card, font=T.font(12), fg=T.DIM, bg=T.CARD, anchor="e", width=60)
        self.count_label.pack(anchor="w")
        self.bio_var.trace_add("write", lambda *_: self._count())
        self.save_btn = PillButton(card, "Save changes", self.save_profile, width=200, height=46)
        self.save_btn.pack(anchor="w", pady=(8, 0))
        self.profile_msg = tk.Label(card, text="", font=T.font(13), bg=T.CARD, fg=T.GREEN, anchor="w")
        self.profile_msg.pack(anchor="w", pady=(8, 0))

        # ---- password
        card = self._card(body)
        tk.Label(card, text="Change password", font=T.font(17, "semibold"), fg=T.TEXT, bg=T.CARD).pack(anchor="w", pady=(0, 10))
        self._field(card, "Current password", self.old_var, show="\u2022", icon="lock")
        self._field(card, "New password", self.new_var, show="\u2022", icon="lock")
        self._field(card, "Confirm new password", self.conf_var, show="\u2022", icon="lock")
        self.pw_btn = PillButton(card, "Update password", self.change_password, style="secondary", width=200, height=46)
        self.pw_btn.pack(anchor="w", pady=(4, 0))
        self.pw_msg = tk.Label(card, text="", font=T.font(13), bg=T.CARD, fg=T.GREEN, anchor="w")
        self.pw_msg.pack(anchor="w", pady=(8, 0))

        # ---- calling devices (saved locally on this computer)
        card = self._card(body)
        tk.Label(card, text="Calling devices", font=T.font(17, "semibold"), fg=T.TEXT, bg=T.CARD).pack(anchor="w", pady=(0, 4))
        tk.Label(card, text="Choose the microphone, speaker and camera used for calls. These choices stay on this device.",
                 font=T.font(12), fg=T.MUTED, bg=T.CARD, wraplength=560, justify="left").pack(anchor="w", pady=(0, 12))
        self.audio_input_var = tk.StringVar(value="Default")
        self.audio_output_var = tk.StringVar(value="Default")
        self.camera_index_var = tk.StringVar(value="0")
        self.audio_input_options = {"Default": None}
        self.audio_output_options = {"Default": None}
        self.device_combos = [
            self._device_row(card, "Microphone", self.audio_input_var, list(self.audio_input_options)),
            self._device_row(card, "Speaker / headphones", self.audio_output_var, list(self.audio_output_options)),
            self._device_row(card, "Camera index", self.camera_index_var, [str(i) for i in range(5)])]
        device_buttons = tk.Frame(card, bg=T.CARD); device_buttons.pack(anchor="w", pady=(8, 0))
        PillButton(device_buttons, "Refresh devices", self.refresh_call_devices, style="secondary", width=160, height=40).pack(side="left")
        PillButton(device_buttons, "Save devices", self.save_call_devices, width=150, height=40).pack(side="left", padx=(8, 0))
        self.devices_msg = tk.Label(card, text="", font=T.font(12), bg=T.CARD, fg=T.MUTED, anchor="w", wraplength=560, justify="left")
        self.devices_msg.pack(anchor="w", pady=(8, 0))
        self.refresh_call_devices(initial=True)

        # ---- account
        card = self._card(body)
        tk.Label(card, text="Account", font=T.font(17, "semibold"), fg=T.TEXT, bg=T.CARD).pack(anchor="w", pady=(0, 8))
        self.server_label = tk.Label(card, font=T.font(13), fg=T.MUTED, bg=T.CARD, justify="left")
        self.server_label.pack(anchor="w")
        tk.Label(card, text="Messages are encrypted on this computer with AES-256-GCM before they are sent.",
                 font=T.font(13), fg=T.MUTED, bg=T.CARD, wraplength=440, justify="left").pack(anchor="w", pady=(6, 14))
        row = tk.Frame(card, bg=T.CARD)
        row.pack(fill="x", pady=(0, 14))
        Toggle(row, main.app.sound_var.get(), lambda v: main.app.sound_var.set(v), bg=T.CARD).pack(side="left")
        tk.Label(row, text="Play a sound for new messages", font=T.font(14), fg=T.TEXT, bg=T.CARD).pack(
            side="left", padx=12)
        PillButton(card, "About this program", main.app.show_about, style="secondary", width=200, height=46
                   ).pack(anchor="w", pady=(0, 10))
        PillButton(card, "Sign out", main.app.logout, style="danger", width=200, height=46, icon="logout").pack(anchor="w")
        self.refresh()

    @staticmethod
    def _card(parent):
        outer = tk.Frame(parent, bg=T.CARD, highlightthickness=1, highlightbackground=T.LINE)
        outer.pack(fill="x", pady=(0, 18))
        inner = tk.Frame(outer, bg=T.CARD)
        inner.pack(fill="x", padx=26, pady=22)
        return inner

    @staticmethod
    def _field(parent, text, var, show=None, icon=None):
        tk.Label(parent, text=text, font=T.font(13, "semibold"), fg=T.MUTED, bg=T.CARD).pack(anchor="w", pady=(0, 3))
        f = PillEntry(parent, textvariable=var, show=show, width=440, height=46, radius=14, icon=icon)
        f.pack(anchor="w", pady=(0, 12))
        return f

    def _device_row(self, parent, title, variable, values):
        row = tk.Frame(parent, bg=T.CARD); row.pack(fill="x", pady=(0, 9))
        tk.Label(row, text=title, font=T.font(13, "semibold"), fg=T.MUTED, bg=T.CARD, width=22, anchor="w").pack(side="left")
        combo = ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=38)
        combo.pack(side="left", fill="x", expand=True)
        return combo

    def refresh_call_devices(self, initial=False):
        inputs, outputs = {"Default": None}, {"Default": None}
        camera_choices = [str(i) for i in range(5)]
        try:
            import sounddevice as sd
            try:                                         # Windows lists every device once per audio system: show one
                usable = set(sd.query_hostapis(sd.default.hostapi)["devices"])
            except Exception:
                usable = None
            for index, dev in enumerate(sd.query_devices()):
                if usable is not None and index not in usable:
                    continue
                name = f"{index}: {dev['name']}"
                if int(dev.get("max_input_channels", 0)) > 0:
                    inputs[name] = index
                if int(dev.get("max_output_channels", 0)) > 0:
                    outputs[name] = index
            note = ("Audio devices detected. Camera choices use indexes 0-4; an unavailable camera "
                    "shows an error during a video call.")
        except Exception:
            note = ("Audio device listing needs the optional calling packages. Install requirements-media.txt, "
                    "then refresh. Default devices remain available.")
        self.audio_input_options, self.audio_output_options = inputs, outputs
        for combo, values in zip(self.device_combos, (list(inputs), list(outputs), camera_choices)):
            combo.configure(values=values)
        if not initial:                                  # a chosen device that was unplugged falls back to Default
            if self.audio_input_var.get() not in inputs:
                self.audio_input_var.set("Default")
            if self.audio_output_var.get() not in outputs:
                self.audio_output_var.set("Default")
        if initial:
            try:
                username = self.ctrl.me["username"] if self.ctrl.me else ""
                saved = self.main.app.prefs.call_devices(username) if username else {}
                in_idx, out_idx = saved.get("audio_input"), saved.get("audio_output")
                self.audio_input_var.set(next((k for k,v in inputs.items() if v == in_idx), "Default"))
                self.audio_output_var.set(next((k for k,v in outputs.items() if v == out_idx), "Default"))
                self.camera_index_var.set(str(saved.get("camera_index", 0)))
            except Exception: pass
        self.devices_msg.configure(text=note, fg=T.MUTED)

    def save_call_devices(self):
        try:
            username = self.ctrl.me["username"]
            self.main.app.prefs.set_call_devices(username,
                self.audio_input_options.get(self.audio_input_var.get()),
                self.audio_output_options.get(self.audio_output_var.get()),
                self.camera_index_var.get())
            self.devices_msg.configure(text="Calling devices saved on this computer.", fg=T.GREEN)
        except Exception as exc:
            self.devices_msg.configure(text=f"Could not save calling devices: {exc}", fg=T.RED)

    def _count(self):
        n = len(self.bio_var.get())
        self.count_label.configure(text=f"{n} / {P.MAX_BIO}", fg=T.RED if n > P.MAX_BIO else T.DIM)

    def refresh(self):
        ctrl = self.ctrl
        me = ctrl.me
        if not me:
            return
        self.avatar_label.configure(image=user_avatar(ctrl, me["username"], 118, T.CARD, True))
        self.title_label.configure(text=me["fullname"])
        self.user_label.configure(text="@" + me["username"])
        self.username_var.set("@" + me["username"])
        self.remove_btn.configure(state="normal" if ctrl.avatar_version(me["username"]) else "disabled")
        self.server_label.configure(text=f"Connected to  {ctrl.network.address if ctrl.network else ''}")

    def load_fields(self):
        """Fill the form with the saved details (done when the page is opened)."""
        me = self.ctrl.me
        self.name_var.set(me["fullname"])
        self.bio_var.set(me.get("bio", ""))
        for v in (self.old_var, self.new_var, self.conf_var):
            v.set("")
        for lb in (self.photo_msg, self.profile_msg, self.pw_msg):
            lb.configure(text="")
        self._count()
        self.refresh()

    # ---- actions ---------------------------------------------------------------- #
    def choose_avatar(self):
        path = filedialog.askopenfilename(parent=self, title="Choose a profile picture", filetypes=IMAGE_TYPES)
        if path:
            self.upload_avatar(path)

    def upload_avatar(self, path):
        try:
            data = images.prepare_avatar(path)
        except images.AvatarError as exc:
            self.photo_msg.configure(text=str(exc), fg=T.RED)
            return False
        self.photo_msg.configure(text="Uploading...", fg=T.MUTED)
        return self.ctrl.set_avatar(data)

    def remove_avatar(self):
        self.photo_msg.configure(text="Removing...", fg=T.MUTED)
        self.ctrl.set_avatar("")

    def save_profile(self):
        self.profile_msg.configure(text="Saving...", fg=T.MUTED)
        self.ctrl.update_profile(self.name_var.get(), self.bio_var.get())

    def change_password(self):
        self.pw_msg.configure(text="Updating...", fg=T.MUTED)
        self.ctrl.change_password(self.old_var.get(), self.new_var.get(), self.conf_var.get())

    def on_profile_result(self, ev):
        ok = ev.get("ok")
        if self.photo_msg.cget("text") in ("Uploading...", "Removing..."):
            self.photo_msg.configure(text="Photo updated." if ok else ev.get("message", ""),
                                     fg=T.GREEN if ok else T.RED)
        if self.profile_msg.cget("text") == "Saving...":
            self.profile_msg.configure(text="Saved." if ok else ev.get("message", ""), fg=T.GREEN if ok else T.RED)
        elif not ok:
            self.profile_msg.configure(text=ev.get("message", ""), fg=T.RED)
        self.refresh()

    def on_password_result(self, ev):
        ok = ev.get("ok")
        self.pw_msg.configure(text=ev.get("message", ""), fg=T.GREEN if ok else T.RED)
        if ok:
            for v in (self.old_var, self.new_var, self.conf_var):
                v.set("")


# --------------------------------------------------------------------------- #
# Main chat screen
# --------------------------------------------------------------------------- #
class MainFrame(tk.Frame):
    def __init__(self, app):
        super().__init__(app, bg=T.BG)
        self.app, self.ctrl = app, app.ctrl
        self.view = "chats"
        self.show_info = True
        self._connection_lost = False

        self.sidebar = Sidebar(self, self)
        self.sidebar.pack(side="left", fill="y")
        tk.Frame(self, bg=T.LINE, width=1).pack(side="left", fill="y")
        self.info = InfoPane(self, self)
        self.info_line = tk.Frame(self, bg=T.LINE, width=1)
        self.chat = ChatPane(self, self)
        self.entry = self.chat.entry
        self.settings = SettingsPage(self, self)
        self.toast_widget = Toast(self)
        self.bind("<Configure>", lambda _e: self._layout())
        self._layout()
        self.refresh_all()

    # ---- layout --------------------------------------------------------------- #
    def _layout(self):
        for w in (self.info, self.info_line, self.chat, self.settings):
            w.pack_forget()
        if self.view == "settings":
            self.settings.pack(side="left", fill="both", expand=True)
            return
        room = self.winfo_width() >= SIDEBAR_W + INFO_W + 440 or self.winfo_width() <= 1
        if self.show_info and room:
            self.info.pack(side="right", fill="y")
            self.info_line.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)

    def toast(self, text, bad=False):
        self.toast_widget.show(text, bad)

    # ---- refresh ---------------------------------------------------------------- #
    def refresh_all(self):
        self.sidebar.refresh()
        self.chat.refresh()
        self.info.draw()
        self.settings.refresh()

    def total_unread(self):
        return sum(max(0, int(n)) for k, n in self.ctrl.unread.items() if not self.ctrl.is_muted(k))

    def refresh_unread(self):
        self.sidebar.refresh()
        self.info.draw()

    def open_verify(self):
        key = self.ctrl.current
        if key and key.startswith("dm:"):
            self.app.verify_dialog = VerifyDialog(self, self.ctrl, key[3:])

    def on_conversation(self, key):
        self.sidebar.refresh_list()
        if key == self.ctrl.current:
            self.chat.render_messages(stick=False)
            self.chat.draw_header()
            self.info.draw()

    def on_profile(self, username=None):
        self.refresh_all()

    # ---- navigation ------------------------------------------------------------- #
    def set_view(self, view):
        if view == self.view:
            return
        self.view = view
        self.sidebar.search_var.set("")
        if view == "settings":
            self.settings.load_fields()
        if view == "calls":
            self.ctrl.request_call_history()
        self.sidebar.refresh()
        self._layout()

    def open_conversation(self, key):
        if self.view in ("settings", "calls"):
            self.view = "chats"
            self._layout()
        self.ctrl.open_conversation(key)
        self.refresh_all()
        self.entry.focus_set()

    def close_conversation(self):
        self.ctrl.current = None
        self.refresh_all()

    def toggle_info(self):
        self.show_info = not self.show_info
        self._layout()

    # ---- actions used by the panels ---------------------------------------------- #
    def call_audio(self):
        self.call_current_chat("audio")

    def call_video(self):
        self.call_current_chat("video")

    def call_current_chat(self, media):
        key = self.ctrl.current or ""
        if not key.startswith("dm:"):
            self.toast("Open a friend's chat before calling.", bad=True)
            return
        self.place_call(key[3:], media)

    def place_call(self, peer, media="audio"):
        """Ring a friend. Refusals (not a friend, offline, key changed, already in a call) are shown as toasts."""
        self.ctrl.start_call(peer, media)

    def attach_unavailable(self):
        self.toast("Sending photos and files is not available yet.")

    media_unavailable = attach_unavailable

    def add_friend(self):
        self.app.find_dialog = AddFriendDialog(self, self.ctrl)

    def new_group(self):
        if not self.ctrl.friends:
            self.toast("Add some friends first - groups are made from your friends.", bad=True)
            return
        CreateGroupDialog(self, self.ctrl)

    def manage_group(self, gid):
        self.app.manage_dialog = ManageGroupDialog(self, self.ctrl, gid)

    def manage_current_group(self):
        if self.ctrl.current and self.ctrl.current.startswith("group:"):
            self.manage_group(int(self.ctrl.current[6:]))

    def leave_current_group(self):
        key = self.ctrl.current
        if key and key.startswith("group:") and messagebox.askyesno("Leave group", "Leave this group?", parent=self):
            self.ctrl.group_leave(int(key[6:]))

    def block_current(self):
        key = self.ctrl.current
        if key and key.startswith("dm:"):
            name = self.ctrl.name_of(key[3:])
            if messagebox.askyesno("Block contact", f"Block {name}?\n\nThey will be removed from your friends and "
                                   "can no longer message you. You can add them again later.", parent=self):
                self.ctrl.remove_friend(key[3:])

    def set_muted(self, key, muted):
        self.ctrl.set_muted(key, muted)
        self.toast("Notifications muted." if muted else "Notifications on.")

    def clear_chat(self):
        key = self.ctrl.current
        if key and messagebox.askyesno("Clear chat", "Clear this chat on this computer?\n\nThe messages stay on "
                                       "the server and your friend still has them.", parent=self):
            self.ctrl.clear_chat(key)

    def choose_wallpaper(self):
        menu = tk.Menu(self, tearoff=0, bg=T.CARD, fg=T.TEXT, activebackground=T.BLUE, activeforeground="#ffffff",
                       bd=0, font=T.font(14))
        names = {"doodle": "Doodles", "plain": "Plain navy", "ocean": "Ocean glow", "midnight": "Midnight"}
        for key, text in names.items():
            menu.add_command(label=text, command=lambda k=key: self._set_wallpaper(k))
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _set_wallpaper(self, kind):
        self.ctrl.prefs.set_wallpaper(self.ctrl.me["username"], kind)
        self.chat.render_messages(stick=False)

    def show_chat_menu(self):
        key = self.ctrl.current
        if not key:
            return
        menu = tk.Menu(self, tearoff=0, bg=T.CARD, fg=T.TEXT, activebackground=T.BLUE, activeforeground="#ffffff",
                       bd=0, font=T.font(14))
        menu.add_command(label="Contact info" if key.startswith("dm:") else "Group info", command=self.show_info_dialog)
        menu.add_command(label="Show / hide details panel", command=self.toggle_info)
        muted = self.ctrl.is_muted(key)
        menu.add_command(label="Unmute notifications" if muted else "Mute notifications",
                         command=lambda: self.set_muted(key, not muted))
        menu.add_command(label="Clear chat", command=self.clear_chat)
        menu.add_separator()
        if key.startswith("group:"):
            menu.add_command(label="Manage group", command=self.manage_current_group)
            menu.add_command(label="Leave group", command=self.leave_current_group)
        else:
            menu.add_command(label="Verify security", command=self.open_verify)
            menu.add_command(label="Block contact", command=self.block_current)
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def show_app_menu(self):
        menu = tk.Menu(self, tearoff=0, bg=T.CARD, fg=T.TEXT, activebackground=T.BLUE, activeforeground="#ffffff",
                       bd=0, font=T.font(14))
        menu.add_command(label="My profile", command=lambda: self.set_view("settings"))
        menu.add_command(label="Add friend", command=self.add_friend)
        menu.add_command(label="Change password", command=self.app.show_change_password)
        menu.add_command(label="About", command=self.app.show_about)
        menu.add_separator()
        menu.add_command(label="Sign out", command=self.app.logout)
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def show_emoji(self):
        if not self.ctrl.current:
            return
        EmojiPicker(self, self.entry, self.winfo_pointerx(), self.winfo_pointery())

    def show_info_dialog(self):
        key = self.ctrl.current
        if key and key.startswith("dm:"):
            InfoDialog(self, self.ctrl, key[3:])
        elif key:
            self.show_info = True
            self._layout()

    # ---- connection ---------------------------------------------------------------- #
    def set_connection(self, text):
        ok = text.lower().startswith("connected")
        if ok and self._connection_lost:
            self.toast("Back online.")
        elif not ok:
            self.toast(text, bad=True)
        self._connection_lost = not ok


# --------------------------------------------------------------------------- #
# Application window
# --------------------------------------------------------------------------- #


class App(tk.Tk):
    def __init__(self, controller=None):
        super().__init__()
        self.title(APP_TITLE)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1320, sw - 60), min(820, sh - 100)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(min(940, w), min(600, h))
        self.configure(bg=T.BG)
        self._setup_theme()
        self.ctrl = controller or ChatController()
        self.prefs = Prefs()
        self.ctrl.on_update = self.on_update
        self.manage_dialog = None
        self.find_dialog = None
        self.password_dialog = None
        self.verify_dialog = None
        self.sound_var = tk.BooleanVar(value=True)
        self.login = None
        self.main = None
        self._icon_image = None
        self.calls = CallManager(self)
        self._set_icon()
        self.show_login()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<FocusIn>", self._on_focus_in)
        self.after(100, self._poll)

    def _set_icon(self):
        """Window / taskbar icon (works from source and from the packaged .exe)."""
        try:
            self._icon_image = tk.PhotoImage(file=resource_path("assets", "icon.png"))
            self.iconphoto(True, self._icon_image)
        except tk.TclError:
            pass
        if notify.IS_WINDOWS:
            try:
                self.iconbitmap(resource_path("assets", "icon.ico"))
            except tk.TclError:
                pass

    def _on_focus_in(self, _event=None):
        notify.stop_flashing(self)
        self._update_title()

    def _update_title(self):
        total = self.main.total_unread() if self.main else 0
        self.title(f"({total}) {APP_TITLE}" if total else APP_TITLE)

    def show_about(self):
        AboutDialog(self)

    def show_change_password(self):
        if self.main is None:
            return
        self.password_dialog = ChangePasswordDialog(self.main, self.ctrl)

    def _alert_for(self, info):
        """A message from someone else arrived: sound + taskbar flash when the window is in the background."""
        try:
            if notify.window_in_background(self):
                notify.alert(self, sound=self.sound_var.get())
            elif info["key"] != self.ctrl.current and not info.get("queued") and self.sound_var.get():
                notify.beep(self)                      # another chat got a message while you look at this one
        except tk.TclError:
            pass

    def _setup_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam" if "clam" in style.theme_names() else "default")
        style.configure("Dlg.TCheckbutton", background=T.CARD, foreground=T.TEXT, focuscolor=T.CARD,
                        font=T.font(13, "semibold"))
        style.map("Dlg.TCheckbutton", background=[("active", T.CARD)], foreground=[("active", T.BLUE_HI)])
        style.configure("TCombobox", fieldbackground=T.FIELD, background=T.FIELD, foreground=T.TEXT,
                        arrowcolor=T.BLUE_HI, bordercolor=T.FIELD_LINE, lightcolor=T.FIELD, darkcolor=T.FIELD)
        style.map("TCombobox", fieldbackground=[("readonly", T.FIELD)], foreground=[("readonly", T.TEXT)])
        self.option_add("*TCombobox*Listbox.background", T.FIELD)
        self.option_add("*TCombobox*Listbox.foreground", T.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", T.BLUE)

    def _poll(self):
        try:
            self.ctrl.poll()
        finally:
            # during a call, audio must be handed to the speaker promptly: poll every 20 ms instead of 100 ms
            self.after(20 if self.calls.engine is not None else 100, self._poll)

    def _clear(self):
        for f in (self.login, self.main):
            if f is not None:
                f.destroy()
        self.login = self.main = None
        self.unbind_all("<Return>")

    def show_login(self, message=None, ok=False):
        self._clear()
        self.title(APP_TITLE)
        self.login = LoginFrame(self)
        self.login.pack(fill="both", expand=True)
        if message:
            self.login.set_status(message, ok)

    def show_main(self):
        self._clear()
        self.main = MainFrame(self)
        self.main.pack(fill="both", expand=True)
        if not self.ctrl.me.get("avatar_ver"):
            self.main.toast("Tip: add a profile photo in Settings.")

    def logout(self):
        self.calls.shutdown()
        self.ctrl.logout()

    def close(self):
        self.calls.shutdown()                    # hang up properly and free the microphone / camera first
        self.ctrl.shutdown()
        self.destroy()

    def _refresh_find_dialog(self):
        dlg = self.find_dialog
        if dlg is None:
            return
        if dlg.winfo_exists():
            dlg.refresh()
        else:
            self.find_dialog = None

    # ---- controller callbacks ---------------------------------------- #
    def on_update(self, topic, payload=None):
        login, main = self.login, self.main
        if topic in ("user_list", "state", "requests", "presence", "profile"):
            self._refresh_find_dialog()
        if topic == "status" and login:
            login.set_status(payload, ok=True)
        elif topic == "auth_error":
            if login:
                login.set_busy(False)
                login.set_status(payload)
        elif topic == "register_result" and login:
            if payload.get("ok"):
                login.registered(payload.get("username", ""), payload["message"], payload.get("passphrase", ""))
            else:
                login.set_busy(False)
                login.set_status(payload["message"])
        elif topic == "login":
            if login:
                self.show_main()
        elif topic == "logged_out":
            self.calls.shutdown(hang_up=False)
            self.show_login()
        elif topic == "kicked":
            self.calls.shutdown(hang_up=False)
            self.show_login(payload)
        elif main is None:
            return
        elif topic in ("state", "groups", "requests", "profile", "avatar"):
            main.refresh_all()
            if topic == "groups" and self.manage_dialog is not None and self.manage_dialog.winfo_exists():
                self.manage_dialog.refresh()
        elif topic == "presence":
            main.sidebar.refresh_list()
            main.chat.draw_header()
            main.info.draw()
        elif topic == "unread":
            main.refresh_unread()
            self._update_title()
        elif topic == "incoming":
            self._alert_for(payload)
        elif topic == "call_incoming":
            self.calls.on_incoming(payload or {})
        elif topic == "call_status":
            self.calls.on_status(payload or {})
        elif topic == "call_media":
            self.calls.on_media(payload or {})
        elif topic == "call_history":
            main.sidebar.refresh()
        elif topic == "typing":
            if payload == self.ctrl.current:
                main.chat.draw_header()
        elif topic == "verification":
            main.chat.draw_header()
            main.info.draw()
            if self.verify_dialog is not None and self.verify_dialog.winfo_exists():
                self.verify_dialog.refresh()
        elif topic == "password_changed":
            dlg = self.password_dialog
            if dlg is not None and dlg.winfo_exists():
                dlg.result(payload)
        elif topic == "conversation":
            main.on_conversation(payload)
        elif topic == "profile_result":
            main.settings.on_profile_result(payload)
            main.refresh_all()
        elif topic == "password_result":
            main.settings.on_password_result(payload)
        elif topic == "notice":
            main.toast(payload)
        elif topic == "error":
            main.toast(payload, bad=True)
        elif topic == "connection":
            main.set_connection(payload)
