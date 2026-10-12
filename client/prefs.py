"""Small per-computer preferences for the chat window (kept in ``client_prefs.json``).

Everything here is local to this computer and is never sent to the server:
which conversations are muted, where "Clear chat" cut the history, the wallpaper.
"""
import json
import os

from shared import settings

PREFS_NAME = "client_prefs.json"
WALLPAPERS = ("doodle", "plain", "ocean", "midnight")


class Prefs:
    def __init__(self, path=None):
        self.path = path or os.environ.get("HUSSAINI_PREFS_FILE") or os.path.join(settings.app_dir(), PREFS_NAME)
        self.data = {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            pass

    def _user(self, username):
        return self.data.setdefault("users", {}).setdefault(username, {})

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1)
        except OSError:
            pass                                  # read-only folder: preferences just last for this session

    # ---- mute -------------------------------------------------------------- #
    def is_muted(self, username, key):
        return key in self._user(username).get("muted", [])

    def set_muted(self, username, key, muted):
        u = self._user(username)
        keys = set(u.get("muted", []))
        keys.add(key) if muted else keys.discard(key)
        u["muted"] = sorted(keys)
        self._save()

    # ---- clear chat (hides everything up to a message id on this computer) -- #
    def cleared_upto(self, username, key):
        return int(self._user(username).get("cleared", {}).get(key, 0))

    def set_cleared(self, username, key, message_id):
        self._user(username).setdefault("cleared", {})[key] = int(message_id)
        self._save()

    # ---- local calling devices ------------------------------------------------ #
    def call_devices(self, username):
        value = self._user(username).get("call_devices", {})
        if not isinstance(value, dict):
            value = {}
        try:
            camera_index = max(0, min(4, int(value.get("camera_index", 0) or 0)))
        except (TypeError, ValueError):
            camera_index = 0
        def optional_index(key):
            item = value.get(key)
            try:
                return None if item in (None, "") else max(0, int(item))
            except (TypeError, ValueError):
                return None
        return {
            "audio_input": optional_index("audio_input"),
            "audio_output": optional_index("audio_output"),
            "camera_index": camera_index,
        }

    def set_call_devices(self, username, audio_input=None, audio_output=None, camera_index=0):
        def clean_index(value):
            if value in (None, "", "Default"):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        try:
            camera_index = max(0, min(4, int(camera_index)))
        except (TypeError, ValueError):
            camera_index = 0
        self._user(username)["call_devices"] = {
            "audio_input": clean_index(audio_input),
            "audio_output": clean_index(audio_output),
            "camera_index": camera_index,
        }
        self._save()

    # ---- wallpaper ------------------------------------------------------------ #
    def wallpaper(self, username):
        value = self._user(username).get("wallpaper", WALLPAPERS[0])
        return value if value in WALLPAPERS else WALLPAPERS[0]

    def set_wallpaper(self, username, name):
        self._user(username)["wallpaper"] = name if name in WALLPAPERS else WALLPAPERS[0]
        self._save()
