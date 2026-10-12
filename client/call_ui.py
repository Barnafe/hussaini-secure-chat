"""Everything the person sees and hears during a call.

* ``Ringer``         - repeating ringtone (never blocks the window).
* ``IncomingPopup``  - small always-on-top "X is calling" window with Accept / Decline.
* ``CallWindow``     - "Calling...", then the running call: timer, mute, camera, end, corner self-view.
* ``CallManager``    - the one place that reacts to controller events and owns the media engine.

Thread rule: the media engine calls back from its own threads. Those callbacks only store the newest
frame / message under a lock; the Tk thread picks them up every 40 ms. Tk is never touched off-thread.
"""
import io
import threading
import time
import tkinter as tk

from client import notify
from client import theme as T
from client.widgets import PillButton

RING_GIVE_UP_MS = 40_000          # a popup that nobody closed (lost message) removes itself
HANG_UP_GRACE_MS = 3_000          # if the server never confirms a hang-up, close locally
END_LINGER_MS = 1_400             # how long "Call ended" stays on screen

REFUSALS = {
    "in_call": "You are already in a call.",
    "no_key": "This friend's encryption key is missing, so a secure call is not possible yet.",
    "key_changed": "Your friend's safety number changed. Verify it (open their info) before calling.",
    "invalid_peer": "You can only call your friends.",
    "offline": "You are offline - reconnect first.",
}


def media_word(media):
    return "Video" if media == "video" else "Voice"


class Ringer:
    """Plays a short alert every ``interval_ms`` until stopped (Tk ``after`` loop, so it cannot freeze the UI)."""

    def __init__(self, root, interval_ms=2200, beep=notify.beep):
        self.root, self.interval_ms, self._beep = root, interval_ms, beep
        self.running = False
        self._job = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._tick()

    def _tick(self):
        if not self.running:
            return
        try:
            self._beep(self.root)
        except Exception:
            pass
        self._job = self.root.after(self.interval_ms, self._tick)

    def stop(self):
        self.running = False
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None


def _avatar(app, username, size):
    try:
        from client.panels import user_avatar
        return user_avatar(app.ctrl, username, size, T.BG)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
class IncomingPopup(tk.Toplevel):
    """Non-blocking incoming-call popup. Closing it with the window button declines the call."""

    def __init__(self, manager, call):
        super().__init__(manager.app)
        self.manager, self.call_id = manager, call.get("call_id", "")
        app, peer = manager.app, call.get("caller", "")
        self.title("Incoming call")
        self.configure(bg=T.CARD)
        self.resizable(False, False)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        w, h = 340, 290
        self.geometry(f"{w}x{h}+{max(0, app.winfo_x() + (app.winfo_width() - w) // 2)}"
                      f"+{max(0, app.winfo_y() + 90)}")
        self.protocol("WM_DELETE_WINDOW", lambda: manager.answer(False))
        photo = _avatar(app, peer, 84)
        self._photo = photo
        tk.Label(self, image=photo or "", bg=T.CARD).pack(pady=(22, 6))
        tk.Label(self, text=app.ctrl.name_of(peer), bg=T.CARD, fg=T.TEXT, font=("Segoe UI", 16, "bold")).pack()
        tk.Label(self, text=f"@{peer} · {media_word(call.get('media'))} call", bg=T.CARD, fg=T.MUTED,
                 font=("Segoe UI", 11)).pack(pady=(2, 14))
        row = tk.Frame(self, bg=T.CARD)
        row.pack()
        self.decline_btn = PillButton(row, "Decline", lambda: manager.answer(False), style="end",
                                      width=130, height=44, bg=T.CARD)
        self.decline_btn.pack(side="left", padx=8)
        self.accept_btn = PillButton(row, "Accept", lambda: manager.answer(True), style="success",
                                     width=130, height=44, bg=T.CARD)
        self.accept_btn.pack(side="left", padx=8)
        self.after(RING_GIVE_UP_MS, self._give_up)
        try:
            self.lift()
        except tk.TclError:
            pass

    def _give_up(self):
        if self.winfo_exists():
            self.manager.close_popup()


# --------------------------------------------------------------------------- #
class CallWindow(tk.Toplevel):
    """The call window. States: ``calling`` (ringing out / connecting) -> ``active`` -> ``ended``."""

    def __init__(self, manager, peer, media, outgoing):
        super().__init__(manager.app)
        self.manager, self.peer, self.media = manager, peer, media
        self.state, self.engine, self.active_since = "calling", None, None
        self._lock = threading.Lock()
        self._pending = {"remote": None, "self": None, "errors": []}
        self._remote_photo = self._self_photo = None
        self._video_visible = False
        self.configure(bg=T.BG)
        self.title(f"{media_word(media)} call · @{peer}")
        self.protocol("WM_DELETE_WINDOW", manager.hang_up)
        self.minsize(360, 300)
        app = manager.app
        self.geometry(f"380x310+{max(0, app.winfo_x() + 80)}+{max(0, app.winfo_y() + 70)}")

        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", pady=(16, 4))
        self._avatar = _avatar(app, peer, 64)
        tk.Label(head, image=self._avatar or "", bg=T.BG).pack()
        self.name_label = tk.Label(head, text=app.ctrl.name_of(peer), bg=T.BG, fg=T.TEXT, font=("Segoe UI", 15, "bold"))
        self.name_label.pack(pady=(6, 0))
        self.status_label = tk.Label(head, text="Calling…" if outgoing else "Connecting…", bg=T.BG, fg=T.MUTED,
                                     font=("Segoe UI", 11))
        self.status_label.pack(pady=(2, 0))

        self.video_holder = tk.Frame(self, bg="#08101f", height=300)
        self.remote_label = tk.Label(self.video_holder, bg="#08101f", fg=T.MUTED, font=("Segoe UI", 11))
        self.remote_label.pack(fill="both", expand=True)
        self.self_label = tk.Label(self.video_holder, bg="#101827", bd=2, relief="flat")
        # the self-view floats in the bottom-right corner of the remote picture
        self.self_label.place(relx=1.0, rely=1.0, anchor="se", x=-8, y=-8)
        self.self_label.place_forget()

        self.controls = tk.Frame(self, bg=T.BG)
        self.controls.pack(side="bottom", pady=14)
        self.mute_btn = PillButton(self.controls, "Mute", self._toggle_mute, style="secondary", width=104,
                                   height=42, bg=T.BG)
        self.camera_btn = PillButton(self.controls, "Start camera", self._toggle_camera, style="secondary",
                                     width=140, height=42, bg=T.BG)
        self.end_btn = PillButton(self.controls, "Cancel" if outgoing else "End call", manager.hang_up,
                                  style="end", width=110, height=42, bg=T.BG)
        self.end_btn.pack(side="left", padx=6)
        self.after(40, self._pump)
        self.after(500, self._tick)

    # ---- state changes (Tk thread) -------------------------------------- #
    def set_active(self, engine):
        self.state, self.engine, self.active_since = "active", engine, time.monotonic()
        self.end_btn.configure(text="End call")
        self.mute_btn.pack(side="left", padx=6, before=self.end_btn)
        self.camera_btn.pack(side="left", padx=6, before=self.end_btn)
        self.sync_buttons()
        if self.media == "video":
            self._show_video_area()
        self._tick()

    def set_ended(self, text):
        self.state = "ended"
        self.status_label.configure(text=text, fg=T.MUTED)
        for b in (self.mute_btn, self.camera_btn):
            b.configure(state="disabled")
        self.end_btn.configure(state="disabled")

    def set_status(self, text, bad=False):
        self.status_label.configure(text=text, fg=T.RED if bad else T.MUTED)

    def sync_buttons(self):
        e = self.engine
        if e is None:
            return
        self.mute_btn.configure(text="Unmute" if e.muted else "Mute")
        self.camera_btn.configure(text="Camera off" if e.camera_enabled else "Start camera"
                                  if self.media != "video" else "Camera on")
        if e.camera_enabled:
            self._show_video_area()

    def _show_video_area(self):
        if self._video_visible:
            return
        self._video_visible = True
        self.video_holder.pack(fill="both", expand=True, padx=14, pady=(8, 0), before=self.controls)
        if self.winfo_height() < 520:
            self.geometry(f"{max(self.winfo_width(), 560)}x560")
        self.minsize(460, 480)

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.state == "active" and self.active_since is not None:
            s = int(time.monotonic() - self.active_since)
            self.status_label.configure(text=f"{s // 60:02d}:{s % 60:02d}", fg=T.MUTED)
        elif self.state == "calling":
            pass
        if self.state != "ended":
            self.after(500, self._tick)

    # ---- buttons --------------------------------------------------------- #
    def _toggle_mute(self):
        if self.engine:
            self.engine.set_muted(not self.engine.muted)
            self.sync_buttons()

    def _toggle_camera(self):
        if self.engine:
            self.engine.set_camera(not self.engine.camera_enabled)
            if self.engine.camera_enabled:
                self._show_video_area()
            else:
                self.self_label.place_forget()
                self._self_photo = None
            self.sync_buttons()

    # ---- thread-safe inputs (called from the media engine's threads) -------- #
    def push_remote(self, jpg):
        with self._lock:
            self._pending["remote"] = jpg

    def push_self(self, jpg):
        with self._lock:
            self._pending["self"] = jpg

    def push_error(self, message):
        with self._lock:
            self._pending["errors"].append(message)

    def _pump(self):
        if not self.winfo_exists():
            return
        with self._lock:
            remote, mine, errors = self._pending["remote"], self._pending["self"], self._pending["errors"]
            self._pending = {"remote": None, "self": None, "errors": []}
        try:
            if errors:
                for message in errors:
                    self.manager.toast(message, bad=True)
                self.sync_buttons()
            if remote is not None:
                self._draw_remote(remote)
            if mine is not None and self.engine is not None and self.engine.camera_enabled:
                self._draw_self(mine)
        finally:
            if self.state != "ended" or self._pending["remote"] is not None:
                self.after(40, self._pump)

    def _draw_remote(self, jpg):
        if not jpg:                                      # the friend switched their camera off
            self._remote_photo = None
            self.remote_label.configure(image="", text=f"{self.manager.app.ctrl.name_of(self.peer)}'s camera is off")
            return
        self._show_video_area()
        self._remote_photo = self._photo(jpg, self.remote_label, mirror=False, fit=True)
        if self._remote_photo is not None:
            self.remote_label.configure(image=self._remote_photo, text="")

    def _draw_self(self, jpg):
        self._self_photo = self._photo(jpg, self.self_label, mirror=True, size=(112, 84))
        if self._self_photo is not None:
            self.self_label.configure(image=self._self_photo)
            self.self_label.place(relx=1.0, rely=1.0, anchor="se", x=-8, y=-8)
            self.self_label.lift()

    def _photo(self, jpg, widget, mirror, fit=False, size=None):
        try:
            from PIL import Image, ImageOps, ImageTk
            image = Image.open(io.BytesIO(jpg)).convert("RGB")
            if mirror:
                image = ImageOps.mirror(image)
            if fit:
                w, h = max(160, widget.winfo_width()), max(120, widget.winfo_height())
                scale = min(w / image.width, h / image.height)
                size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
            if size:
                image = image.resize(size, Image.BILINEAR)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None


# --------------------------------------------------------------------------- #
class CallManager:
    """Owns the popup, the window and the media engine; driven by the app's controller events."""

    def __init__(self, app):
        self.app = app
        self.window = None
        self.popup = None
        self.engine = None
        self.ringer = Ringer(app)
        self.call_id = None
        self._linger_job = None

    # ---- helpers ----------------------------------------------------------- #
    @property
    def ctrl(self):
        return self.app.ctrl

    @property
    def busy(self):
        return bool(self.window or self.popup)

    def toast(self, text, bad=False):
        if self.app.main:
            self.app.main.toast(text, bad)

    def _name(self, username):
        return self.ctrl.name_of(username) if username else "your friend"

    def _alive(self, widget):
        try:
            return widget is not None and bool(widget.winfo_exists())
        except tk.TclError:
            return False

    # ---- controller events ----------------------------------------------- #
    def on_incoming(self, call):
        if self._alive(self.popup) or self._alive(self.window):
            return
        self.call_id = call.get("call_id")
        self.popup = IncomingPopup(self, call)
        self.ringer.start()
        if notify.window_in_background(self.app):
            notify.flash_taskbar(self.app)

    def on_status(self, payload):
        status = (payload or {}).get("status", "")
        if status in REFUSALS:
            self.toast(REFUSALS[status], bad=True)
            return
        if status == "ringing":                          # I am calling: show the "Calling…" window
            self._cancel_linger()
            self.call_id = payload.get("call_id")
            peer = payload.get("callee") or payload.get("peer", "")
            self._open_window(peer, payload.get("media", "audio"), outgoing=True)
            self.ringer.start()
        elif status == "accepted":
            self._connected(payload)
        elif status in ("busy", "offline"):
            peer = payload.get("peer", "")
            self.toast(f"{self._name(peer)} is " + ("busy on another call." if status == "busy" else "offline."), bad=True)
            self.ctrl.request_call_history()
        elif status in ("declined", "cancelled", "no_answer", "completed", "connection_lost"):
            self._ended(payload)

    def _open_window(self, peer, media, outgoing):
        self.close_window()
        self.window = CallWindow(self, peer, media, outgoing)

    def _connected(self, payload):
        self.ringer.stop()
        self.close_popup()
        peer = payload.get("peer") or self.ctrl.call_peer() or ""
        media = payload.get("media", "audio")
        if not self._alive(self.window):
            self._open_window(peer, media, outgoing=False)
        try:
            engine = self._make_engine(peer, media)
            engine.start()
        except Exception as exc:                         # no microphone, no packages, ... : say so and hang up
            self.toast(str(exc), bad=True)
            self.window.set_status(str(exc), bad=True)
            self.ctrl.end_call()
            return
        self.engine = engine
        self.window.set_active(engine)
        for note in engine.notes:
            self.toast(note)

    def _make_engine(self, peer, media):
        from client.media_engine import EncryptedMediaEngine
        call = self.ctrl.active_call or {}
        devices = self.app.prefs.call_devices(self.ctrl.me.get("username", ""))
        window = self.window
        return EncryptedMediaEngine(
            self.ctrl, call.get("call_id") or self.call_id, peer, media,
            on_video=window.push_remote, on_local_video=window.push_self, on_error=window.push_error,
            audio_input=devices.get("audio_input"), audio_output=devices.get("audio_output"),
            camera_index=devices.get("camera_index", 0))

    def _ended(self, payload):
        was_active = bool(self.engine) or bool(payload.get("was_active"))
        incoming, peer, status = bool(payload.get("incoming")), payload.get("peer", ""), payload.get("status", "")
        self.ringer.stop()
        self.close_popup()
        self._stop_engine()
        who = self._name(peer)
        if was_active:
            text = "Call ended" if status == "completed" else "Call ended - the connection was lost"
            if status == "completed" and self.window is not None and self.window.active_since:
                s = int(time.monotonic() - self.window.active_since)
                text = f"Call ended · {s // 60:02d}:{s % 60:02d}"
        elif incoming:
            text = {"declined": "Call declined"}.get(status, f"Missed call from {who}")
        else:
            text = {"declined": f"{who} declined the call", "no_answer": f"{who} did not answer",
                    "cancelled": "Call cancelled", "connection_lost": "Call failed - connection lost"
                    }.get(status, "Call ended")
        if self._alive(self.window):
            self.window.set_ended(text)
            self._cancel_linger()
            self._linger_job = self.app.after(END_LINGER_MS, self.close_window)
        self.toast(text, bad=(not was_active and status in ("declined", "no_answer", "connection_lost")
                              or (incoming and not was_active and status != "declined")))
        self.call_id = None

    # ---- user actions --------------------------------------------------------- #
    def answer(self, accept):
        call_id = self.call_id
        self.ringer.stop()
        self.close_popup()
        if not call_id or not self.ctrl.answer_call(call_id, accept):
            self.toast("That call has already ended.")
            return
        if accept:
            call = self.ctrl.active_call or {}
            self._open_window(call.get("caller", ""), call.get("media", "audio"), outgoing=False)

    def hang_up(self):
        """End / cancel the call. If the server never confirms, close locally after a short grace period."""
        call_id = self.call_id
        window = self.window
        if self._alive(window) and window.state == "ended":
            self.close_window()
            return
        sent = self.ctrl.end_call()
        if self._alive(window):
            window.set_status("Ending…")
        if not sent or not call_id:
            self._force_close()
            return
        self.app.after(HANG_UP_GRACE_MS, lambda: self._force_close() if self.call_id == call_id else None)

    def _force_close(self):
        self.ringer.stop()
        self.close_popup()
        self._stop_engine()
        self.close_window()
        self.call_id = None
        self.ctrl.active_call = None

    # ---- teardown ------------------------------------------------------------- #
    def _stop_engine(self):
        engine, self.engine = self.engine, None
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
        if self._alive(self.window):
            self.window.engine = None

    def _cancel_linger(self):
        if self._linger_job is not None:
            try:
                self.app.after_cancel(self._linger_job)
            except Exception:
                pass
            self._linger_job = None

    def close_popup(self):
        popup, self.popup = self.popup, None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass

    def close_window(self):
        self._cancel_linger()
        window, self.window = self.window, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    def shutdown(self, hang_up=True):
        """Logging out, being kicked or closing the app: end the call properly and free every device."""
        if hang_up and self.ctrl.active_call:
            try:
                self.ctrl.end_call()
            except Exception:
                pass
        self.ringer.stop()
        self.close_popup()
        self._stop_engine()
        self.close_window()
        self.call_id = None

    def on_media(self, payload):
        if self.engine is not None and (payload or {}).get("call_id") == self.engine.call_id:
            self.engine.receive(payload)
