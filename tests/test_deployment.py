"""Phase 6 - deployment tests: start the real entry point (run_server.py) the same way Render does."""
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.helpers import TClient, PASSWORD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("TEST_DATABASE_URL") else [])


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.parametrize("backend", BACKENDS)
def test_run_server_entry_point(tmp_path, backend):
    port = free_port()
    env = dict(os.environ, PORT=str(port), HOST="127.0.0.1", BCRYPT_ROUNDS="4",
               SQLITE_PATH=str(tmp_path / "deploy.db"), PYTHONUNBUFFERED="1")
    env.pop("DATABASE_URL", None)
    if backend == "postgres":
        env["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]       # exactly how Render passes Neon's string
    proc = subprocess.Popen([sys.executable, "run_server.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    assert r.status == 200
                break
            except OSError:
                assert proc.poll() is None, proc.stdout.read()
                assert time.time() < deadline, "server did not start"
                time.sleep(0.2)
        name = f"dep_{backend[:2]}_{int(time.time()) % 10**7}"   # <= 20 chars
        c = TClient(f"ws://127.0.0.1:{port}")
        c.send(type="register", username=name, password=PASSWORD, confirm=PASSWORD, fullname="Deploy Test")
        assert c.expect("register_result")["ok"]
        c.send(type="login", username=name, password=PASSWORD)
        assert c.expect("login_result")["ok"]
        c.close()
    finally:
        proc.send_signal(signal.SIGTERM)                            # Render stops services with SIGTERM
        try:
            code = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
    out = proc.stdout.read()
    assert code == 0, out
    assert f"Database back-end: {'postgresql' if backend == 'postgres' else 'sqlite'}" in out
    assert "Shutting down" in out


def test_config_files_are_consistent():
    req = open(os.path.join(ROOT, "requirements.txt")).read().lower()
    for pkg in ("websockets", "psycopg", "psycopg-pool", "bcrypt", "pycryptodome"):
        assert pkg in req
    render = open(os.path.join(ROOT, "render.yaml")).read()
    assert "startCommand: python run_server.py" in render
    assert "healthCheckPath: /health" in render
    assert "DATABASE_URL" in render
    gitignore = open(os.path.join(ROOT, ".gitignore")).read()
    assert ".env" in gitignore
