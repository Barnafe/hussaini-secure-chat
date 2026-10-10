"""Server configuration, read from settings.txt (see shared/settings.py)."""
from dataclasses import dataclass

from shared import settings


@dataclass
class Config:
    host: str
    port: int
    database_file: str
    bcrypt_rounds: int
    log_level: str


def _int(values, key, low, high):
    try:
        n = int(values[key])
    except ValueError:
        raise SystemExit(f"settings.txt: '{key}' must be a number (got '{values[key]}').")
    if not low <= n <= high:
        raise SystemExit(f"settings.txt: '{key}' must be between {low} and {high}.")
    return n


def load(path=None) -> Config:
    v = settings.load(path)
    return Config(
        host=v["server_listen_host"],
        port=_int(v, "server_port", 1, 65535),
        database_file=settings.resolve_path(v["database_file"]),
        bcrypt_rounds=_int(v, "bcrypt_rounds", 4, 16),
        log_level=v["log_level"].upper(),
    )
