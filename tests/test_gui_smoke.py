"""Phase 3 - GUI smoke test: drives the real Tkinter window (needs a display; use xvfb-run on a headless box)."""
import gc
import os
import time
import types

import pytest

tk = pytest.importorskip("tkinter")

from client.controller import ChatController, dm_key, group_key  # noqa: E402
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


def chat_texts(app):
    """Texts of the messages currently drawn in the conversation view."""
    return [it["text"] for it in app.main.chat.view.items if it["type"] == "msg"]


def list_kinds(app):
    return [r["kind"] for r in app.main.sidebar.list.rows]


def test_full_gui_flow(env, tmp_path):
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
    assert login.l_user.get() == "alice" and login.tab == 0     # switched to the login tab, prefilled
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
    assert main.view == "chats" and app.ctrl.me["fullname"] == "Alice Adamu"
    assert list_kinds(app) == ["empty"]                         # no conversations yet

    # ---- a second user (controller only) befriends alice ---------------------------------
    bob = Peer(srv)
    assert bob.register("bob", "Bob Bello")["ok"]
    bob.login("bob")
    pump(app, lambda: "bob" in app.ctrl.online_users, what="bob shows as online")
    main.set_view("contacts")                                   # the Contacts tab lists everyone online
    assert "online" in list_kinds(app)
    row = next(r for r in main.sidebar.list.rows if r["kind"] == "online")
    assert row["name"] == "Bob Bello"
    main.sidebar._action(row, "request")                        # alice -> bob via the "add" button
    bob.wait(lambda: bob.ctrl.requests_in, what="bob gets request")
    bob.ctrl.respond_request("alice", True)
    pump(app, lambda: "friend" in list_kinds(app), what="friend appears")
    friend = next(r for r in main.sidebar.list.rows if r["kind"] == "friend")
    assert friend["name"] == "Bob Bello" and friend["online"] is True

    # ---- chat ----------------------------------------------------------------------------
    main.sidebar._select(friend)
    assert main.view == "contacts" and app.ctrl.current == "dm:bob"
    main.set_view("chats")
    assert "chat" in list_kinds(app)
    main.entry.insert(0, "Hello from the GUI")
    main.chat.send()
    pump(app, lambda: "Hello from the GUI" in chat_texts(app), what="own message rendered")
    bob.ctrl.open_conversation(dm_key("alice"))
    bob.wait(lambda: "Hello from the GUI" in bob.texts(dm_key("alice")), what="bob decrypts")
    bob.ctrl.send_text("Reply from Bob https://example.com/page")
    pump(app, lambda: "Reply from Bob https://example.com/page" in chat_texts(app), what="reply rendered")
    assert app.ctrl.shared_links("dm:bob") == [("https://example.com/page", "example.com")]
    chat_row = next(r for r in main.sidebar.list.rows if r["kind"] == "chat")
    assert chat_row["last"]["text"].startswith("Reply from Bob")        # preview in the chat list

    # ---- unread badge when the conversation is not open ------------------------------------
    main.close_conversation()
    assert app.ctrl.current is None and chat_texts(app) == []
    bob.ctrl.send_text("Are you there?")
    pump(app, lambda: main.total_unread() == 1, what="unread count")
    pump(app, lambda: next(r for r in main.sidebar.list.rows if r["kind"] == "chat")["unread"] == 1,
         what="unread badge in the list")
    app.ctrl.set_muted("dm:bob", True)                           # muted chats do not count
    assert main.total_unread() == 0
    app.ctrl.set_muted("dm:bob", False)

    # ---- groups through the dialogs -------------------------------------------------------
    from client.gui import CreateGroupDialog, ManageGroupDialog
    dlg = CreateGroupDialog(main, app.ctrl)
    dlg.name.set("Study Circle")
    dlg.lb.selection_set(0)
    dlg.create()
    pump(app, lambda: app.ctrl.groups, what="group created")
    gkey = group_key(next(iter(app.ctrl.groups)))
    main.open_conversation(gkey)
    main.entry.insert(0, "Welcome to the group")
    main.chat.send()
    pump(app, lambda: "Welcome to the group" in chat_texts(app), what="group message rendered")
    manage = ManageGroupDialog(main, app.ctrl, next(iter(app.ctrl.groups)))
    assert "(admin)" in manage.lb.get(0) and "(you)" in manage.lb.get(0)
    manage.destroy()
    main.set_view("groups")
    assert "group" in list_kinds(app)

    # ---- "Add friend" = Find people: everyone listed, search narrows, request from the list ---
    carol = Peer(srv)
    assert carol.register("carol", "Carol Musa")["ok"]
    from client.gui import AddFriendDialog
    main.add_friend()
    find = app.find_dialog
    assert isinstance(find, AddFriendDialog)
    pump(app, lambda: find.rows == ["bob", "carol"], what="everyone listed")
    assert "Friends" in find.lb.get(0) and str(find.btn._state) == "disabled"     # bob is already a friend
    find.query.set("mus")
    pump(app, lambda: find.rows == ["carol"], what="search narrows the list")
    find.lb.selection_set(0)
    find._update_button()
    assert str(find.btn._state) == "normal"
    find.send()
    pump(app, lambda: app.ctrl.relationship("carol") == "request_sent", what="request sent from the list")
    pump(app, lambda: "Request sent" in find.lb.get(0), what="row shows the new status")
    find.destroy()

    # ---- my profile: details, picture, password -------------------------------------------
    main.set_view("settings")
    page = main.settings
    assert page.name_var.get() == "Alice Adamu" and page.username_var.get() == "@alice"
    page.name_var.set("Alice A. Adamu")
    page.bio_var.set("Good vibes.")
    page.save_btn.command()
    pump(app, lambda: "Saved." in page.profile_msg.cget("text"), what="profile saved")
    bob.wait(lambda: bob.ctrl.friends["alice"]["fullname"] == "Alice A. Adamu", what="bob sees the new name")
    assert bob.ctrl.bio_of("alice") == "Good vibes."
    from PIL import Image
    pic = tmp_path / "me.png"
    Image.new("RGBA", (640, 400), (20, 120, 220, 255)).save(pic)
    assert page.upload_avatar(str(pic))
    pump(app, lambda: "Photo updated." in page.photo_msg.cget("text"), what="photo uploaded")
    assert app.ctrl.avatar_version("alice") and str(page.remove_btn.cget("state")) == "normal"
    bob.wait(lambda: bob.ctrl.avatar_version("alice") != "", what="bob sees a picture exists")
    bad = tmp_path / "notes.txt"
    bad.write_text("not a picture")
    assert page.upload_avatar(str(bad)) is False and "could not be opened" in page.photo_msg.cget("text")
    page.remove_avatar()
    pump(app, lambda: app.ctrl.avatar_version("alice") == "", what="photo removed")
    page.old_var.set(PW)
    page.new_var.set("BrandNew123")
    page.conf_var.set("BrandNew123")
    page.change_password()
    pump(app, lambda: "Password changed." in page.pw_msg.cget("text"), what="password changed")

    # ---- logout returns to the login window ----------------------------------------------
    app.logout()
    pump(app, lambda: app.login is not None and app.main is None, what="back to login")


def test_selftest_entry_point_opens_and_closes_the_window(env):
    env.app.close()
    env.app = types.SimpleNamespace(close=lambda: None)         # already closed; keep the fixture teardown quiet
    from client.main import main
    assert main(["--selftest"]) == 0
