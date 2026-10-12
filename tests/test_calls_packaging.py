"""The calling feature must actually ship: dependencies, build recipe, CI and the release folder."""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_media_requirements_and_ci_install_the_calling_packages():
    media = read("requirements-media.txt")
    for pkg in ("numpy", "sounddevice", "opencv-python-headless"):
        assert pkg in media
    assert "requirements-media.txt" in read(".github", "workflows", "build-windows.yml")


def test_pyinstaller_recipe_bundles_the_calling_packages():
    spec = read("packaging", "HussainiChat.spec")
    for name in ("numpy", "sounddevice", "cv2", "client.media_engine", "client.call_ui"):
        assert f"'{name}'" in spec
    assert "--selftest-calls" in read(".github", "workflows", "build-windows.yml")


def test_selftest_calls_reports_missing_packages(monkeypatch):
    import sys
    from client.main import main
    for name in ("numpy", "sounddevice", "cv2"):
        try:
            __import__(name)
        except Exception:                       # not installed, or no PortAudio library on this machine
            pytest.skip(f"{name} is not usable here")
    assert main(["--selftest-calls"]) == 0
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    assert main(["--selftest-calls"]) == 2


def test_documentation_matches_the_program():
    from shared import version
    readme = read("README.md")
    assert "Not built yet (the buttons show a short notice): voice/video calls" not in readme
    assert "## Voice and video calls" in readme and os.path.exists(os.path.join(ROOT, "CALLS.md"))
    assert re.search(r'AppVersion "%s"' % re.escape(version.VERSION), read("packaging", "windows", "installer.iss"))
    for stale in ("CALLS_V9_STATUS.md", "CALLS_V10_STATUS.md", "CALLS_V11_STATUS.md"):
        assert not os.path.exists(os.path.join(ROOT, stale)), f"{stale} claimed more than was tested"
