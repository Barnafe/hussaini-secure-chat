"""Reads ``settings.txt`` (simple ``key = value`` lines) - shared by the server and the client.

Lookup order for every setting:  environment variable HUSSAINI_<KEY>  >  settings.txt  >  default.
settings.txt sits next to the .exe files (or in the project folder when run from source).
"""
import os
import sys

SETTINGS_NAME = "settings.txt"

DEFAULTS = {
    "server_listen_host": "0.0.0.0",
    "server_port": "8765",
    "database_file": "data/chat.db",
    "bcrypt_rounds": "12",
    "log_level": "INFO",
    "server_address": "localhost:8765",
}


def app_dir() -> str:
    """Folder that holds settings.txt and the data folder."""
    if getattr(sys, "frozen", False):                      # PyInstaller .exe
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        out[key.strip().lower()] = value
    return out


def load(path: str = None) -> dict:
    """Return every setting (defaults filled in, env and file applied)."""
    path = path or os.path.join(app_dir(), SETTINGS_NAME)
    values = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            values.update({k: v for k, v in parse(fh.read()).items() if v != ""})
    except OSError:
        pass
    for key in list(values):
        env = os.environ.get("HUSSAINI_" + key.upper())
        if env:
            values[key] = env.strip()
    return values


def resolve_path(value: str) -> str:
    """Relative paths are relative to the app folder, so the .exe works from anywhere."""
    return value if os.path.isabs(value) else os.path.join(app_dir(), value)
