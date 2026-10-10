"""Phase 6 - the real entry points (run_server.py) and the project/packaging files."""
import os
import signal
import socket
import subprocess
import sys
import time

import yaml

from shared import protocol as P
from tests.helpers import PASSWORD, TClient, new_identity

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_run_server_entry_point_serves_and_stops(tmp_path):
    port = free_port()
    env = dict(os.environ, HUSSAINI_SERVER_PORT=str(port), HUSSAINI_SERVER_LISTEN_HOST="127.0.0.1",
               HUSSAINI_BCRYPT_ROUNDS="4", HUSSAINI_DATABASE_FILE=str(tmp_path / "e.db"),
               PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "run_server.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert proc.poll() is None, proc.stdout.read()
                assert time.time() < deadline, "server did not start"
                time.sleep(0.2)
        passphrase, ident = new_identity("dep_user")
        c = TClient(f"127.0.0.1:{port}")
        c.send(type="register", username="dep_user", password=PASSWORD, confirm=PASSWORD,
               fullname="Deploy Test", public_key=ident.public_key_b64)
        assert c.expect("register_result")["ok"]
        c.send(type="login", username="dep_user", password=PASSWORD, public_key=ident.public_key_b64)
        assert c.expect("login_result")["ok"]
        c.close()
    finally:
        if os.name == "nt":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGINT)
    out = proc.communicate(timeout=20)[0]
    if os.name != "nt":                                   # Windows has no graceful signal for a child process
        assert proc.returncode == 0, out
        assert "Shutting down" in out
    assert "SQLite" in out and "listening" in out and f"localhost:{port}" in out
    assert (tmp_path / "e.db").exists()


def test_server_reports_a_busy_port_clearly(tmp_path):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    env = dict(os.environ, HUSSAINI_SERVER_PORT=str(port), HUSSAINI_SERVER_LISTEN_HOST="127.0.0.1",
               HUSSAINI_DATABASE_FILE=str(tmp_path / "b.db"))
    try:
        proc = subprocess.run([sys.executable, "run_server.py"], cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=30)
    finally:
        blocker.close()
    assert proc.returncode == 1 and "already running" in proc.stdout


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_requirements_are_tcp_and_sqlite_only():
    req = read("requirements.txt").lower()
    for pkg in ("bcrypt", "cryptography"):
        assert pkg in req
    for gone in ("websockets", "psycopg", "pycryptodome", "dotenv"):
        assert gone not in req
    assert "pytest" in read("requirements-dev.txt").lower()
    assert "pyinstaller" in read("requirements-build.txt").lower()


def test_no_leftovers_from_the_websocket_and_cloud_versions():
    for gone in ("render.yaml", ".env.example", ".pytest_cache", os.path.join("tests", "test_deployment.py")):
        assert not os.path.exists(os.path.join(ROOT, gone)), gone
    banned = ("websockets", "psycopg", "DATABASE_URL", "asyncio", "wss://", "ws://", "onrender", "neon")
    for folder in ("client", "server", "shared"):
        for name in os.listdir(os.path.join(ROOT, folder)):
            if name.endswith(".py"):
                text = read(folder, name)
                for word in banned:
                    assert word.lower() not in text.lower(), f"{folder}/{name} still mentions {word}"
    for name in ("run_server.py", "run_client.py"):
        text = read(name).lower()
        assert "websocket" not in text and "asyncio" not in text


def test_gitignore_keeps_secrets_caches_and_build_output_out():
    ignore = read(".gitignore")
    for entry in (".pytest_cache", "__pycache__", "data/*.db", "build/", "dist/"):
        assert entry in ignore


def test_pyinstaller_specs_exist_and_name_the_right_executables():
    server, client = read("packaging", "HussainiServer.spec"), read("packaging", "HussainiChat.spec")
    assert "run_server.py" in server and "name='HussainiServer'" in server and "console=True" in server
    assert "run_client.py" in client and "name='HussainiChat'" in client and "console=False" in client
    for spec in (server, client):
        compile(spec, "spec", "exec")                     # at least valid Python


def test_github_actions_workflow_builds_and_uploads_the_windows_zip():
    wf = yaml.safe_load(read(".github", "workflows", "build-windows.yml"))
    triggers = wf.get("on", wf.get(True))
    assert "push" in triggers and "workflow_dispatch" in triggers
    job = wf["jobs"]["build"]
    assert job["runs-on"] == "windows-latest"
    text = read(".github", "workflows", "build-windows.yml")
    for needle in ("pytest", "HussainiServer.spec", "HussainiChat.spec", "HussainiChat-Windows",
                   "upload-artifact", "--selftest", "packaging/assemble.py"):
        assert needle in text, needle


def test_windows_launcher_starts_the_exe_files():
    bat = read("packaging", "windows", "start-demo.bat")
    assert "HussainiServer.exe" in bat and bat.count("HussainiChat.exe") >= 3


def test_handover_documents_exist():
    for name in ("README.txt", "PRESENTATION_GUIDE.txt"):
        assert len(read("packaging", "windows", name)) > 1500
    assert os.path.isfile(os.path.join(ROOT, "settings.txt"))


def test_assemble_script_builds_the_release_folder(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "HussainiServer.exe").write_bytes(b"MZ-server")
    (dist / "HussainiChat.exe").write_bytes(b"MZ-chat")
    out = tmp_path / "out"
    subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "assemble.py"),
                    "--dist", str(dist), "--out", str(out)], check=True, cwd=ROOT)
    folder = out / "HussainiChat-Windows"
    for name in ("HussainiServer.exe", "HussainiChat.exe", "start-demo.bat", "show-database.bat",
                 "settings.txt", "README.txt", "PRESENTATION_GUIDE.txt"):
        assert (folder / name).is_file(), name
    assert (folder / "data").is_dir()
    assert (folder / "start-demo.bat").read_bytes().count(b"\r\n") > 5      # Windows line endings
    missing = tmp_path / "empty"
    missing.mkdir()
    bad = subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "assemble.py"),
                          "--dist", str(missing), "--out", str(tmp_path / "o2")], cwd=ROOT,
                         capture_output=True, text=True)
    assert bad.returncode != 0 and "HussainiServer.exe" in (bad.stdout + bad.stderr)


def test_show_db_prints_hashes_and_ciphertext_only(tmp_path):
    """run_server.py --show-db is the proof, for the demo, that the server cannot read chats."""
    import subprocess
    from server.database import Database
    db_file = str(tmp_path / "show.db")
    db = Database(sqlite_path=db_file)
    db.create_user("alice", "$2b$12$abcdefghijklmnopqrstuvwxyz", "Alice A", "PUBLICKEY1")
    db.create_user("bob", "$2b$12$zyxwvutsrqponmlkjihgfedcba", "Bob B", "PUBLICKEY2")
    db.save_message("alice", "bob", "Q0lQSEVSVEVYVEFCQw==")
    db.close()
    env = dict(os.environ, HUSSAINI_DATABASE_FILE=db_file)
    out = subprocess.run([sys.executable, "run_server.py", "--show-db"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "USERS (2)" in out.stdout and "$2b$12$" in out.stdout
    assert "Q0lQSEVSVEVYVEFCQw==" in out.stdout and "MESSAGES (1)" in out.stdout


def test_show_database_launcher_is_packaged():
    assert "--show-db" in read("packaging", "windows", "show-database.bat")
    assert "show-database.bat" in read("packaging", "assemble.py")
