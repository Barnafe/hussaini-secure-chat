"""Calls, GUI - the real Tk window driven under xvfb: popup, ringtone, call window, Calls tab, shutdown.

Alice is the real application window. Bob is a controller + media engine (the other computer).
Hardware is simulated; everything else is real.
"""
import gc
import re
import sys
import time
import types

import pytest

tk = pytest.importorskip("tkinter")

from client.controller import ChatController  # noqa: E402
from client.media_engine import EncryptedMediaEngine  # noqa: E402
from server.database import Database  # noqa: E402
from tests.fake_media import FakeSoundDevice, install_fake_camera  # noqa: E402
from tests.helpers import ServerThread  # noqa: E402
from tests.test_client import PW, Peer  # noqa: E402


def _display_ok():
    try:
        root = tk.Tk()
        root.destroy()
        return True
    except tk.TclError:
        return False


pytestmark = pytest.mark.skipif(not _display_ok(), reason="no display available (try xvfb-run)")

BOB_MIC, BOB_SPK = 2, 12


class BobSide:
    """Bob's computer: a controller that starts its media engine when its call connects."""

    def __init__(self, peer, camera=1):
        self.peer, self.camera, self.engine = peer, camera, None
        self.video, self.statuses, self.incoming = [], [], []
        previous = peer.ctrl.on_update

        def on_update(topic, payload=None):
            previous(topic, payload)
            if topic == "call_status":
                self.statuses.append(payload.get("status"))
                if payload.get("status") == "accepted" and self.engine is None:
                    call = peer.ctrl.active_call
                    try:
                        self.engine = EncryptedMediaEngine(
                            peer.ctrl, call["call_id"], peer.ctrl.call_peer(), call["media"],
                            on_video=self.video.append, audio_input=BOB_MIC, audio_output=BOB_SPK,
                            camera_index=self.camera).start()
                    except Exception:
                        self.engine = None                    # (bob's machine has no calling packages either)
                if payload.get("status") in ("completed", "cancelled", "declined", "no_answer", "connection_lost"):
                    self.stop_engine()
            elif topic == "call_incoming":
                self.incoming.append(payload)
            elif topic == "call_media" and self.engine:
                self.engine.receive(payload)
        peer.ctrl.on_update = on_update

    def stop_engine(self):
        if self.engine:
            self.engine.stop()
            self.engine = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    from client import call_ui
    from client.gui import App
    fake = FakeSoundDevice({BOB_MIC: 880, None: 440}).install(monkeypatch)
    db = Database(sqlite_path=str(tmp_path / "g.db"))
    srv = ServerThread(db).start()
    app = App(ChatController(kdf_iterations=1000))
    beeps = []
    monkeypatch.setattr(call_ui.notify, "beep", lambda root: beeps.append(time.time()))
    app.calls.ringer._beep = lambda root: beeps.append(time.time())
    app.calls.ringer.interval_ms = 120
    h = types.SimpleNamespace(app=app, srv=srv, fake=fake, beeps=beeps, toasts=[], bob=None, bobs=None)
    yield h
    for side in (h.bobs,):
        if side:
            side.stop_engine()
    try:
        app.close()
    except tk.TclError:
        pass
    h.app = None
    gc.collect()
    srv.stop()
    db.close()


def pump(env, cond, timeout=8.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        env.app.update()
        if env.bob:
            env.bob.ctrl.poll()
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def settle(env, seconds):
    end = time.time() + seconds
    while time.time() < end:
        env.app.update()
        if env.bob:
            env.bob.ctrl.poll()
        time.sleep(0.01)


@pytest.fixture
def friends(env):
    """alice (GUI, logged in) and bob (controller) are friends and online; alice has bob's chat open."""
    app, srv = env.app, env.srv
    login = app.login
    login.server.set(srv.address)
    login.r_name.set("Alice Adamu"); login.r_user.set("alice"); login.r_pass.set(PW); login.r_conf.set(PW)
    login.do_register()
    end = time.time() + 8
    while not login.new_passphrase and time.time() < end:
        app.update(); time.sleep(0.01)
    assert login.new_passphrase
    if login.passphrase_dialog is not None:
        login.passphrase_dialog.destroy()
    login.l_pass.set(PW)
    login.do_login()
    end = time.time() + 8
    while app.main is None and time.time() < end:
        app.update(); time.sleep(0.01)
    assert app.main is not None
    bob = Peer(srv)
    assert bob.register("bob", "Bob Bello")["ok"]
    bob.login("bob")
    env.bob = bob
    bob.ctrl.send_friend_request("alice")
    pump(env, lambda: app.ctrl.requests_in, what="request reaches alice")
    app.ctrl.respond_request("bob", True)
    pump(env, lambda: "bob" in app.ctrl.friends and "alice" in bob.ctrl.friends, what="friends")
    pump(env, lambda: app.ctrl.friends["bob"]["online"], what="bob online")
    env.bobs = BobSide(bob)
    app.main.toast = lambda text, bad=False: env.toasts.append((text, bad))   # record every toast
    app.ctrl.open_conversation("dm:bob")
    app.main.refresh_all()
    return env


def wait_active(env):
    pump(env, lambda: env.app.calls.engine is not None and env.bobs.engine is not None
         and env.app.ctrl.active_call.get("status") == "active", what="call active on both sides")


def toast_texts(env):
    return [t for t, _ in env.toasts]


# --------------------------------------------------------------------------- #
def test_header_and_info_buttons_start_the_right_kind_of_call(friends):
    env = friends
    main = env.app.main
    main.call_audio()
    pump(env, lambda: env.bobs.incoming, what="voice call rings bob")
    assert env.bobs.incoming[-1]["media"] == "audio"
    pump(env, lambda: env.app.calls.window is not None, what="'Calling…' window")
    assert env.app.calls.window.state == "calling"
    assert env.app.calls.window.status_label.cget("text").startswith("Calling")
    env.app.calls.hang_up()
    pump(env, lambda: not env.bob.ctrl.active_call and not env.app.ctrl.active_call, what="cancelled")
    main.call_video()
    pump(env, lambda: len(env.bobs.incoming) == 2, what="video call rings bob")
    assert env.bobs.incoming[-1]["media"] == "video"
    pump(env, lambda: env.app.calls.window is not None and env.app.calls.window.state == "calling",
         what="the new 'Calling…' window (not the old 'Call ended' one)")
    env.app.calls.hang_up()
    pump(env, lambda: not env.bob.ctrl.active_call and not env.app.ctrl.active_call, what="cancelled again")
    import inspect
    from client import panels
    assert "call_unavailable" not in inspect.getsource(panels)


def test_calling_a_stranger_or_an_offline_friend_explains_why(friends):
    env = friends
    main = env.app.main
    env.bob.ctrl.logout()
    pump(env, lambda: not env.app.ctrl.friends["bob"]["online"], what="bob offline")
    main.call_audio()
    pump(env, lambda: any("offline" in t for t in toast_texts(env)), what="offline explanation")
    assert env.app.calls.window is None or env.app.calls.window.state != "active"
    pump(env, lambda: not env.app.ctrl.active_call, what="no call left behind")
    main.place_call("nobody", "audio")
    pump(env, lambda: any("only call your friends" in t for t in toast_texts(env)), what="stranger explanation")


def test_incoming_voice_call_rings_then_connects_and_both_hear_each_other(friends):
    env = friends
    app = env.app
    env.bob.ctrl.start_call("alice", "audio")
    pump(env, lambda: app.calls.popup is not None, what="incoming popup")
    popup = app.calls.popup
    pump(env, lambda: len(env.beeps) >= 3, what="ringtone repeating")           # it keeps ringing ...
    app.update()                                                                  # ... and the window is not frozen
    assert popup.winfo_exists() and str(popup.accept_btn.winfo_ismapped()) in ("1", "True")
    n = len(env.beeps)
    popup.accept_btn.command()                                                    # press Accept
    wait_active(env)
    assert app.calls.popup is None and not app.calls.ringer.running
    settle(env, 0.3)
    stopped_at = len(env.beeps)
    settle(env, 0.5)
    assert len(env.beeps) == stopped_at, "ringtone must stop once the call is answered"
    settle(env, 1.8)
    assert abs(env.fake.dominant_frequency(None) - 880) < 15, "alice hears bob"
    assert abs(env.fake.dominant_frequency(BOB_SPK) - 440) < 15, "bob hears alice"
    win = app.calls.window
    assert win.state == "active" and re.fullmatch(r"\d\d:\d\d", win.status_label.cget("text"))
    assert win.name_label.cget("text") == "Bob Bello"
    win.mute_btn.command()
    assert app.calls.engine.muted and win.mute_btn.cget("text") == "Unmute"
    settle(env, 1.0)
    assert env.fake.loudness(BOB_SPK, 0.4) < 100
    win.end_btn.command()                                                         # End call
    pump(env, lambda: not app.ctrl.active_call and not env.bob.ctrl.active_call, what="hang up")
    pump(env, lambda: app.calls.window is None, what="window closes after 'Call ended'")
    assert env.fake.live == 0, "every audio stream must be closed"
    assert "completed" in env.bobs.statuses


def test_decline_from_the_popup(friends):
    env = friends
    app = env.app
    env.bob.ctrl.start_call("alice", "video")
    pump(env, lambda: app.calls.popup is not None, what="popup")
    app.calls.popup.decline_btn.command()
    pump(env, lambda: "declined" in env.bobs.statuses, what="bob told")
    assert app.calls.popup is None and not app.calls.ringer.running and app.calls.window is None
    assert env.fake.live == 0


def test_missed_call_closes_popup_shows_in_calls_tab_and_calls_back_with_video(friends):
    env = friends
    app = env.app
    env.bob.ctrl.start_call("alice", "video")
    pump(env, lambda: app.calls.popup is not None, what="popup")
    env.bob.ctrl.end_call()                                                       # bob gives up
    pump(env, lambda: app.calls.popup is None, what="popup closes when the caller hangs up")
    assert not app.calls.ringer.running
    assert any("Missed call from Bob Bello" in t for t in toast_texts(env))
    app.main.set_view("calls")                                                    # the tab loads and shows it by itself
    pump(env, lambda: any(r["kind"] == "call" for r in app.main.sidebar.list.rows), what="calls tab rows")
    row = next(r for r in app.main.sidebar.list.rows if r["kind"] == "call")
    assert (row["outcome"], row["missed"], row["media"], row["outgoing"]) == ("Missed", True, "video", False)
    assert row["name"] == "Bob Bello" and row["when"]
    env.bobs.incoming.clear()
    app.main.sidebar._action(row, "call_back")                                    # one-click call back ...
    pump(env, lambda: env.bobs.incoming, what="call back rings bob")
    assert env.bobs.incoming[-1]["media"] == "video", "calling back a video call must be a video call"
    pump(env, lambda: app.calls.window is not None, what="'Calling…' window")
    app.calls.hang_up()
    pump(env, lambda: not env.bob.ctrl.active_call, what="clean up")


def test_calls_tab_refreshes_itself_after_a_call_and_filters(friends):
    env = friends
    app = env.app
    app.main.set_view("calls")
    pump(env, lambda: [r["kind"] for r in app.main.sidebar.list.rows] == ["empty"], what="empty calls tab")
    env.bob.ctrl.start_call("alice", "audio")
    pump(env, lambda: app.calls.popup is not None, what="popup")
    app.calls.popup.accept_btn.command()
    wait_active(env)
    settle(env, 1.2)
    app.calls.hang_up()
    pump(env, lambda: any(r["kind"] == "call" for r in app.main.sidebar.list.rows), what="row appears without a manual refresh")
    row = next(r for r in app.main.sidebar.list.rows if r["kind"] == "call")
    assert (row["outcome"], row["outgoing"]) == ("Incoming", False) and 1 <= row["duration"] <= 4
    app.main.sidebar.search_var.set("zzz")
    app.main.sidebar.refresh()
    assert [r["kind"] for r in app.main.sidebar.list.rows] == ["empty"]
    app.main.sidebar.search_var.set("bello")
    app.main.sidebar.refresh()
    assert any(r["kind"] == "call" for r in app.main.sidebar.list.rows)


def test_video_call_window_corner_self_view_camera_off_and_back(friends, monkeypatch):
    env = friends
    app = env.app
    cam = install_fake_camera(monkeypatch)
    app.calls.ringer.stop()
    app.main.call_video()
    pump(env, lambda: env.bobs.incoming, what="rings")
    env.bob.ctrl.answer_call(env.bobs.incoming[-1]["call_id"], True)
    wait_active(env)
    win = app.calls.window
    pump(env, lambda: win._remote_photo is not None and win._self_photo is not None, what="both pictures drawn")
    assert win._video_visible and win.self_label.winfo_manager() == "place", "self-view floats in the corner"
    assert win.self_label.place_info()["anchor"] == "se"
    assert win.camera_btn.cget("text") == "Camera on" or win.camera_btn.cget("text") == "Camera off"
    win.camera_btn.command()                                                      # switch my camera off
    pump(env, lambda: env.bobs.video and env.bobs.video[-1] == b"", what="bob told my camera is off")
    assert win.self_label.winfo_manager() == "", "self-view disappears with the camera"
    pump(env, lambda: [c for c in cam.instances if c.index == 0][-1].released, what="camera released")
    # bob switches his camera off: alice's remote area says so instead of freezing on the last frame
    env.bobs.engine.set_camera(False)
    pump(env, lambda: "camera is off" in win.remote_label.cget("text"), what="remote 'camera is off' text")
    env.bobs.engine.set_camera(True)
    pump(env, lambda: win._remote_photo is not None and win.remote_label.cget("text") == "", what="remote picture again")
    win.end_btn.command()
    pump(env, lambda: not app.ctrl.active_call, what="ended")
    pump(env, lambda: all(c.released for c in cam.instances), what="all cameras released")


def test_voice_call_can_be_upgraded_to_video_from_the_window(friends, monkeypatch):
    env = friends
    app = env.app
    cam = install_fake_camera(monkeypatch)
    env.bob.ctrl.start_call("alice", "audio")
    pump(env, lambda: app.calls.popup is not None, what="popup")
    app.calls.popup.accept_btn.command()
    wait_active(env)
    win = app.calls.window
    assert not win._video_visible and win.camera_btn.cget("text") == "Start camera"
    assert cam.instances == [], "no camera is opened for a voice call"
    win.camera_btn.command()
    pump(env, lambda: win._video_visible and env.bobs.video and env.bobs.video[-1] != b"", what="bob sees alice")
    assert win.camera_btn.cget("text") == "Camera off"
    win.end_btn.command()
    pump(env, lambda: not app.ctrl.active_call, what="ended")


def test_no_microphone_ends_the_call_with_a_clear_message_instead_of_hanging(friends, monkeypatch):
    env = friends
    app = env.app
    env.bob.ctrl.start_call("alice", "audio")
    pump(env, lambda: app.calls.popup is not None, what="popup")
    monkeypatch.setitem(sys.modules, "sounddevice", None)                         # calling packages missing
    app.calls.popup.accept_btn.command()
    pump(env, lambda: any("requirements-media.txt" in t for t in toast_texts(env)), what="explanation")
    pump(env, lambda: not app.ctrl.active_call and not env.bob.ctrl.active_call, what="call closed on both sides")
    pump(env, lambda: app.calls.window is None or app.calls.window.state == "ended", what="window not stuck")
    assert app.calls.engine is None


def test_closing_the_window_or_logging_out_during_a_call_hangs_up_and_frees_devices(friends):
    env = friends
    app = env.app
    env.bob.ctrl.start_call("alice", "audio")
    pump(env, lambda: app.calls.popup is not None, what="popup")
    app.calls.popup.accept_btn.command()
    wait_active(env)
    settle(env, 0.5)
    app.logout()                                                                  # sign out in the middle of the call
    pump(env, lambda: "completed" in env.bobs.statuses or "connection_lost" in env.bobs.statuses, what="bob told")
    assert app.calls.window is None and app.calls.engine is None
    assert env.fake.live == 0
    assert app.login is not None


def test_the_engines_threads_never_touch_tk(friends, monkeypatch):
    """The media engine calls back from its own threads: those callbacks must only queue data."""
    env = friends
    app = env.app
    cam = install_fake_camera(monkeypatch)
    import threading
    main_thread = threading.get_ident()
    offenders = []
    from client import call_ui
    orig_remote, orig_self = call_ui.CallWindow.push_remote, call_ui.CallWindow.push_self

    def check(fn):
        def wrapper(self, data):
            if threading.get_ident() == main_thread:
                offenders.append("main")
            return fn(self, data)
        return wrapper
    # (push_* are called from the engine's threads; they must work without a running Tk loop and be thread-safe)
    monkeypatch.setattr(call_ui.CallWindow, "push_remote", check(orig_remote))
    monkeypatch.setattr(call_ui.CallWindow, "push_self", check(orig_self))
    app.calls.ringer.stop()
    app.main.call_video()
    pump(env, lambda: env.bobs.incoming, what="rings")
    env.bob.ctrl.answer_call(env.bobs.incoming[-1]["call_id"], True)
    wait_active(env)
    settle(env, 1.0)
    assert app.calls.window._self_photo is not None
    app.calls.hang_up()
    pump(env, lambda: not app.ctrl.active_call, what="ended")


def test_settings_device_pickers_are_wired_and_saved(friends):
    env = friends
    app = env.app
    st = app.main.settings
    assert len(st.device_combos) == 3
    assert "Default" in list(st.device_combos[0].cget("values")) and "Default" in list(st.device_combos[1].cget("values"))
    assert list(st.device_combos[2].cget("values")) == ["0", "1", "2", "3", "4"]
    st.camera_index_var.set("2")
    st.save_call_devices()
    assert app.prefs.call_devices("alice")["camera_index"] == 2
    assert "saved" in st.devices_msg.cget("text").lower()
