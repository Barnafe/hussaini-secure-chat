"""Phase 3 - GUI smoke test: drives the real Tkinter window (needs a display; use xvfb-run on a headless box)."""
import gc
import time
import types

import pytest

tk = pytest.importorskip("tkinter")

from client.controller import ChatController, dm_key  # noqa: E402
from server.database import Database  # noqa: E402
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


@pytest.fixture
def env(tmp_path):
    from client.gui import App
    db = Database(sqlite_path=str(tmp_path / "g.db"))
    srv = ServerThread(db).start()
    holder = types.SimpleNamespace(app=App(ChatController(kdf_iterations=1000)), srv=srv)
    yield holder
    try:
        holder.app.close()
    except tk.TclError:
        pass
    holder.app = None
    # Tk variables must be garbage-collected on THIS (main) thread; if they were collected on the
    # server thread later, Tcl would block waiting for the main loop and stall other tests.
    gc.collect()
    srv.stop()
    db.close()


def pump(app, cond, timeout=8.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def widget_text(app):
    return app.main.text.get("1.0", "end")


def test_full_gui_flow(env):
    app, srv = env.app, env.srv
    login = app.login
    # ---- register through the form -------------------------------------------------
    login.server.set(srv.address)
    login.r_name.set("Alice Adamu")
    login.r_user.set("alice")
    login.r_pass.set(PW)
    login.r_conf.set("different")
    login.do_register()
    pump(app, lambda: "do not match" in login.status.get(), what="password mismatch message")
    login.r_conf.set(PW)
    login.do_register()
    pump(app, lambda: "Registration completed successfully." in login.status.get(), what="registration message")
    assert login.l_user.get() == "alice"                       # switched to the login tab, prefilled
    # ---- the automatically created passphrase is shown to the user ----------------------
    phrase = login.new_passphrase
    assert phrase and len(phrase.split("-")) == 5
    dlg = login.passphrase_dialog
    assert dlg is not None and dlg.winfo_exists() and dlg.passphrase == phrase
    assert str(dlg.go_btn.cget("state")) == "disabled"         # must confirm that it was saved first
    dlg.saved.set(True)
    dlg._toggle()
    assert str(dlg.go_btn.cget("state")) == "normal"
    dlg.destroy()
    assert login.l_phrase.get() == phrase                      # and pre-filled for the first login
    # ---- wrong passphrase, wrong password, then log in --------------------------------------
    login.l_phrase.set("AAAA-BBBB-CCCC-DDDD-EEEE")
    login.do_login()
    pump(app, lambda: "passphrase" in login.status.get().lower(), what="wrong passphrase message")
    login.l_phrase.set(phrase)
    login.l_pass.set("WrongPass9")
    login.do_login()
    pump(app, lambda: "Invalid username or password." in login.status.get(), what="login error")
    login.l_pass.set(PW)
    login.do_login()
    pump(app, lambda: app.main is not None, what="main window")
    main = app.main
    assert "Alice Adamu" in main.me_label.cget("text")

    # ---- a second user (controller only) befriends alice ---------------------------------
    bob = Peer(srv)
    assert bob.register("bob", "Bob Bello")["ok"]
    bob.login("bob")
    pump(app, lambda: "bob" in app.ctrl.online_users, what="bob shows as online")
    assert main.online_lb.get(0).startswith("Bob Bello")
    main.online_lb.selection_set(0)
    main.request_selected_online()                              # alice -> bob via the Online tab
    bob.wait(lambda: bob.ctrl.requests_in, what="bob gets request")
    bob.ctrl.respond_request("alice", True)
    pump(app, lambda: main.friend_lb.size() == 1, what="friend appears")
    assert "Bob Bello" in main.friend_lb.get(0) and main.friend_lb.get(0).startswith("\u25cf")   # online marker

    # ---- chat ----------------------------------------------------------------------------
    main.friend_lb.selection_set(0)
    main.pick_friend()
    assert "Bob Bello" in main.title_var.get()
    main.entry.insert(0, "Hello from the GUI")
    main.send()
    pump(app, lambda: "Hello from the GUI" in widget_text(app), what="own message rendered")
    bob.ctrl.open_conversation(dm_key("alice"))
    bob.wait(lambda: "Hello from the GUI" in bob.texts(dm_key("alice")), what="bob decrypts")
    bob.ctrl.send_text("Reply from Bob")
    pump(app, lambda: "Reply from Bob" in widget_text(app), what="reply rendered")

    # ---- unread badge when another conversation is not open --------------------------------
    app.ctrl.current = None
    main.render_conversation()
    bob.ctrl.send_text("Are you there?")
    pump(app, lambda: "1 UNREAD" in main.friend_lb.get(0), what="unread marker in the list")
    pump(app, lambda: "1 UNREAD MESSAGE" in main.inbox_badge.cget("text"), what="unread badge in the header")

    # ---- groups through the dialogs -------------------------------------------------------
    from client.gui import CreateGroupDialog, ManageGroupDialog
    dlg = CreateGroupDialog(main, app.ctrl)
    dlg.name.set("Study Circle")
    dlg.lb.selection_set(0)
    dlg.create()
    pump(app, lambda: main.group_lb.size() == 1, what="group listed")
    assert "Study Circle" in main.group_lb.get(0)
    main.group_lb.selection_set(0)
    main.pick_group()
    main.entry.insert(0, "Welcome to the group")
    main.send()
    pump(app, lambda: "Welcome to the group" in widget_text(app), what="group message rendered")
    manage = ManageGroupDialog(main, app.ctrl, next(iter(app.ctrl.groups)))
    assert "(admin)" in manage.lb.get(0) and "(you)" in manage.lb.get(0)
    manage.destroy()

    # ---- logout returns to the login window ----------------------------------------------
    app.logout()
    pump(app, lambda: app.login is not None and app.main is None, what="back to login")


def test_selftest_entry_point_opens_and_closes_the_window(env):
    env.app.close()
    env.app = types.SimpleNamespace(close=lambda: None)         # already closed; keep the fixture teardown quiet
    from client.main import main
    assert main(["--selftest"]) == 0


def gui_login(app, srv, username, fullname):
    """Register and sign in through the real forms; returns the passphrase."""
    login = app.login
    login.server.set(srv.address)
    login.r_name.set(fullname)
    login.r_user.set(username)
    login.r_pass.set(PW)
    login.r_conf.set(PW)
    login.do_register()
    pump(app, lambda: login.passphrase_dialog is not None, what="passphrase dialog")
    dlg = login.passphrase_dialog
    dlg.saved.set(True)
    dlg._toggle()
    dlg.destroy()
    login.l_pass.set(PW)
    login.do_login()
    pump(app, lambda: app.main is not None, what="main window")


def test_add_friend_dialog_lists_everyone_and_has_a_search_bar(env):
    app, srv = env.app, env.srv
    others = []
    for name, full in (("bob", "Bob Bello"), ("carol", "Carol Musa"), ("dave", "Dave Danjuma")):
        p = Peer(srv)
        assert p.register(name, full)["ok"]
        others.append(p)
    others[0].login("bob")                                    # bob online, carol and dave offline
    gui_login(app, srv, "alice", "Alice Adamu")
    main = app.main
    main.add_friend()                                         # the "Add friend..." button
    dlg = app.find_dialog
    # everybody is listed straight away - no searching needed
    pump(app, lambda: dlg.lb.size() == 3, what="all users listed")
    rows = [dlg.lb.get(i) for i in range(3)]
    assert "Bob Bello" in rows[0] and "Carol Musa" in rows[1] and "Dave Danjuma" in rows[2]
    assert rows[0].lstrip().startswith("\u25cf") and rows[1].lstrip().startswith("\u25cb")   # online / offline
    assert "3 people" in dlg.count.cget("text")
    assert str(dlg.btn.cget("state")) == "disabled"            # nothing selected yet
    # the search bar narrows the list
    dlg.query.set("mus")
    dlg._search()
    pump(app, lambda: dlg.lb.size() == 1, what="filtered list")
    assert "Carol Musa" in dlg.lb.get(0) and 'matching "mus"' in dlg.count.cget("text")
    dlg.query.set("zzz")
    dlg._search()
    pump(app, lambda: dlg.lb.size() == 0, what="no match")
    assert "No one matches" in dlg.count.cget("text")
    dlg.query.set("")
    dlg._search()
    pump(app, lambda: dlg.lb.size() == 3, what="full list again")
    # pick Bob and send the request from the list
    dlg.lb.selection_set(0)
    dlg._update_button()
    assert str(dlg.btn.cget("state")) == "normal"
    dlg.send()
    others[0].wait(lambda: others[0].ctrl.requests_in, what="bob receives the request")
    pump(app, lambda: "Request sent" in dlg.lb.get(0), what="row shows Request sent")
    assert str(dlg.btn.cget("state")) == "disabled"            # cannot send twice
    others[0].ctrl.respond_request("alice", True)
    pump(app, lambda: "Friends" in dlg.lb.get(0), what="row shows Friends")
    dlg.destroy()
