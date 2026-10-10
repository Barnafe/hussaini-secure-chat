"""Colours, fonts and cached images for the ChatSphere look.

Colours were sampled from the design mock-up. Images and fonts belong to one Tk window,
so the caches are dropped automatically when a new window (root) is created.
"""
import tkinter as tk
import tkinter.font as tkfont

from PIL import ImageTk

from client import icons

# ---- palette ------------------------------------------------------------------ #
BG = "#030c1c"            # chat background
SIDEBAR = "#051123"
HEADER = "#051327"
RIGHT = "#030d1b"
CARD = "#071a33"          # cards / dialogs
LINE = "#0e2142"          # 1px separators
FIELD = "#0c1b31"         # inputs
FIELD_LINE = "#17294a"
HOVER = "#0a1a33"
BLUE = "#0a6cff"
BLUE_HI = "#2b82ff"
BLUE_DEEP = "#00349b"
BLUE_FADE = "#062356"
BUBBLE_IN = "#162641"
TEXT = "#f1f5ff"
TEXT2 = "#c5d0e8"
MUTED = "#8a99b8"
DIM = "#5b6b8f"
GREEN = "#22c55e"
RED = "#ef4444"
LINK = "#4a90ff"
CIRCLE = "#122447"        # round action buttons

BRAND = ("Chat", "Sphere")
TAGLINE = "Connect  \u2022  Chat  \u2022  Share"

_state = {"root": None, "fonts": {}, "photos": {}, "family": None}


def _sync():
    root = tk._default_root
    if _state["root"] is not root:
        _state.update(root=root, fonts={}, photos={}, family=None)
    return root


def family():
    root = _sync()
    if _state["family"] is None:
        have = set(tkfont.families(root))
        _state["family"] = next((f for f in ("Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "Noto Sans",
                                             "Open Sans", "DejaVu Sans", "Arial") if f in have), "TkDefaultFont")
    return _state["family"]


def font(px, weight="normal"):
    """A Tk font ``px`` pixels tall. weight: normal / semibold / bold."""
    _sync()
    key = (px, weight)
    if key not in _state["fonts"]:
        fam = family()
        if weight == "semibold" and fam == "Segoe UI":
            fam, weight = "Segoe UI Semibold", "normal"
        elif weight == "semibold":
            weight = "bold"
        _state["fonts"][key] = tkfont.Font(family=fam, size=-px, weight=weight)
    return _state["fonts"][key]


def photo(key, make):
    """Cached ImageTk.PhotoImage; ``make()`` returns the PIL image the first time."""
    _sync()
    if key not in _state["photos"]:
        if len(_state["photos"]) > 1500:               # keep memory bounded on very long sessions
            _state["photos"].clear()
        _state["photos"][key] = ImageTk.PhotoImage(make())
    return _state["photos"][key]


def icon(name, size=22, color=TEXT, stroke=2.0):
    return photo(("icon", name, size, color, stroke), lambda: icons.render(name, size, color, stroke))


def ellipsize(fnt, text, max_px):
    """Shorten text with an ellipsis so it fits ``max_px`` pixels."""
    text = (text or "").replace("\n", " ")
    if fnt.measure(text) <= max_px:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fnt.measure(text[:mid].rstrip() + "\u2026") <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "\u2026"
