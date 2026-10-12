"""Server-owner tools (--list-users, --backup, --reset-password), the installer and the icon."""
import os
import re
import sqlite3
import subprocess
import sys

import pytest

from server.database import Database
from shared import version
from shared.crypto_utils import verify_password

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts, mode="r"):
    with open(os.path.join(ROOT, *parts), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


def admin(db_file, *args):
    env = dict(os.environ, HUSSAINI_DATABASE_FILE=str(db_file))
    return subprocess.run([sys.executable, "run_server.py", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def populated(tmp_path):
    path = tmp_path / "admin.db"
    db = Database(sqlite_path=str(path))
    db.create_user("alice", "$2b$12$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "Alice Adamu", "PK1")
    db.create_user("bob", "$2b$12$bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "Bob Bello", "PK2")
    db.make_friends("alice", "bob")
    db.save_message("alice", "bob", "CIPHERTEXT")
    db.close()
    return path


def test_list_users_command(populated):
    out = admin(populated, "--list-users")
    assert out.returncode == 0, out.stderr
    assert "ACCOUNTS (2)" in out.stdout and "alice" in out.stdout and "Bob Bello" in out.stdout
    assert "$2b$" not in out.stdout                              # never prints password hashes


def test_backup_command_makes_a_restorable_copy(populated, tmp_path):
    target = tmp_path / "saved" / "copy.db"
    out = admin(populated, "--backup", str(target))
    assert out.returncode == 0, out.stderr
    assert str(target) in out.stdout.replace("\n", "")
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert conn.execute("SELECT encrypted_message FROM messages").fetchone()[0] == "CIPHERTEXT"


def test_reset_password_command(populated):
    short = admin(populated, "--reset-password", "alice", "--new-password", "short")
    assert short.returncode == 1 and "at least 8" in short.stdout
    ghost = admin(populated, "--reset-password", "nobody", "--new-password", "LongEnough1")
    assert ghost.returncode == 1 and "no account" in ghost.stdout
    ok = admin(populated, "--reset-password", "ALICE", "--new-password", "LongEnough1")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "passphrase is NOT affected" in ok.stdout
    db = Database(sqlite_path=str(populated))
    assert verify_password("LongEnough1", db.get_user("alice")["password"])
    assert db.get_user("bob")["password"].startswith("$2b$12$bbb")      # nobody else was touched
    assert len(db.undelivered_messages("bob")) == 1                      # chats are untouched
    db.close()


def test_server_tools_menu_offers_every_command():
    bat = read("packaging", "windows", "server-tools.bat")
    for flag in ("--list-users", "--backup", "--reset-password", "--show-db"):
        assert flag in bat


# --------------------------------------------------------------------------- #
# icon, version, installer
# --------------------------------------------------------------------------- #
def test_icon_files_are_real_images():
    assert read("client", "assets", "icon.png", mode="rb")[:8] == b"\x89PNG\r\n\x1a\n"
    ico = read("client", "assets", "icon.ico", mode="rb")
    assert ico[:4] == b"\x00\x00\x01\x00" and ico[4] >= 4              # an .ico holding several sizes
    for spec in ("HussainiChat.spec", "HussainiServer.spec"):
        assert "icon.ico" in read("packaging", spec)
    assert "icon.png" in read("packaging", "HussainiChat.spec")        # window icon is bundled in the exe


def test_version_and_credits_are_defined():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.VERSION)
    assert version.APP_NAME and version.AUTHOR and version.INSTITUTION
    assert f'#define AppVersion "{version.VERSION}"' in read("packaging", "windows", "installer.iss")


def test_installer_script_installs_everything_the_release_contains():
    iss = read("packaging", "windows", "installer.iss")
    installed = set(re.findall(r'Source: "\{#SourceDir\}\\([^"]+)"', iss))
    assemble = read("packaging", "assemble.py")
    for name in installed:
        assert name in assemble, f"{name} is installed but assemble.py does not produce it"
    assert {"HussainiChat.exe", "HussainiServer.exe", "settings.txt", "server-tools.bat"} <= installed
    assert "PrivilegesRequired=lowest" in iss                          # no admin rights; data folder stays writable
    assert 'Name: "desktopicon"' in iss and "{autodesktop}" in iss      # desktop shortcut
    assert "OutputBaseFilename=HussainiChat-Setup" in iss
    assert "{app}\\data" in iss


def test_workflow_builds_tests_and_publishes_the_installer():
    wf = read(".github", "workflows", "build-windows.yml")
    assert "installer.iss" in wf and "HussainiChat-Setup.exe" in wf and "HussainiChat-Installer" in wf
    assert wf.count("continue-on-error: true") >= 4                    # optional steps never block the build
    assert "actions/upload-artifact@v7" in wf and "actions/checkout@v7" in wf
