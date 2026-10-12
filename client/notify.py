"""New-message alerts: a short sound and a flashing taskbar button while the window is in the background.

Everything here is best-effort - if the platform cannot do it, nothing happens (never an error).
"""
import ctypes
import sys

IS_WINDOWS = sys.platform.startswith("win")


def window_in_background(root) -> bool:
    """True if the window is minimised or does not currently have the keyboard focus."""
    try:
        if root.state() == "iconic":
            return True
        return root.focus_displayof() is None
    except Exception:
        return False


def beep(root):
    try:
        if IS_WINDOWS:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        else:
            root.bell()
    except Exception:
        pass


class _FlashInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]


FLASHW_STOP, FLASHW_ALL, FLASHW_TIMERNOFG = 0, 3, 12


def _flash(root, flags, count):
    if not IS_WINDOWS:
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        info = _FlashInfo(ctypes.sizeof(_FlashInfo), hwnd, flags, count, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def flash_taskbar(root):
    """Flash the taskbar button until the user brings the window to the front."""
    _flash(root, FLASHW_ALL | FLASHW_TIMERNOFG, 10)


def stop_flashing(root):
    _flash(root, FLASHW_STOP, 0)


def alert(root, sound=True):
    """Called when a message arrives while the window is in the background."""
    if sound:
        beep(root)
    flash_taskbar(root)
