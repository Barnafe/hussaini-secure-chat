"""settings.txt loader and server configuration."""
import os

import pytest

from server import config
from shared import settings


def test_parse_ignores_comments_and_blank_lines():
    text = "# comment\n\nserver_port = 9000   # inline\n  database_file=\"data/x.db\"\n; also a comment\nbroken line\n"
    assert settings.parse(text) == {"server_port": "9000", "database_file": "data/x.db"}


def test_defaults_when_file_is_missing(tmp_path):
    cfg = config.load(str(tmp_path / "nope.txt"))
    assert cfg.port == 8765 and cfg.host == "0.0.0.0" and cfg.bcrypt_rounds == 12


def test_file_values_and_relative_database_path(tmp_path):
    f = tmp_path / "settings.txt"
    f.write_text("server_port = 9123\nbcrypt_rounds = 6\ndatabase_file = mydata/chat.db\nlog_level = debug\n")
    cfg = config.load(str(f))
    assert cfg.port == 9123 and cfg.bcrypt_rounds == 6 and cfg.log_level == "DEBUG"
    assert os.path.isabs(cfg.database_file) and cfg.database_file.endswith(os.path.join("mydata", "chat.db"))


def test_environment_overrides_file(tmp_path):
    f = tmp_path / "settings.txt"
    f.write_text("server_port = 9123\n")
    os.environ["HUSSAINI_SERVER_PORT"] = "9555"
    try:
        assert config.load(str(f)).port == 9555
    finally:
        del os.environ["HUSSAINI_SERVER_PORT"]


@pytest.mark.parametrize("line", ["server_port = abc", "server_port = 70000", "bcrypt_rounds = 1"])
def test_invalid_values_give_a_clear_message(tmp_path, line):
    f = tmp_path / "settings.txt"
    f.write_text(line + "\n")
    with pytest.raises(SystemExit) as exc:
        config.load(str(f))
    assert "settings.txt" in str(exc.value)


def test_shipped_settings_file_is_valid():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = config.load(os.path.join(root, "settings.txt"))
    assert cfg.port == 8765
    assert settings.load(os.path.join(root, "settings.txt"))["server_address"] == "localhost:8765"
