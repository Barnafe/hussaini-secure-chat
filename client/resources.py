"""Find bundled files (the icon) both when running from source and from the PyInstaller .exe."""
import os
import sys


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", None)                    # set by PyInstaller in a packaged program
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    else:
        base = os.path.join(base, "client")
    return os.path.join(base, *parts)
