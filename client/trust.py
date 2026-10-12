"""Remembers which friends you have verified (by comparing safety numbers) - stored on THIS computer only."""
import json
import os


def default_path():
    return os.environ.get("HUSSAINI_TRUST_FILE") or os.path.join(os.path.expanduser("~"), ".hussaini_trust.json")


class TrustStore:
    def __init__(self, path=None):
        self.path = path or default_path()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def verified_number(self, me, peer):
        return self._load().get(me, {}).get(peer)

    def mark(self, me, peer, number):
        data = self._load()
        data.setdefault(me, {})[peer] = number
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass

    def forget(self, me, peer):
        data = self._load()
        if data.get(me, {}).pop(peer, None) is not None:
            try:
                with open(self.path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
            except OSError:
                pass
