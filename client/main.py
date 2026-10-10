"""Start the desktop chat client.

    python run_client.py            (or double-click HussainiChat.exe)
    HussainiChat.exe --selftest     (used by the build pipeline: opens and closes the window, exit code 0 = OK)
"""
import os
import sys
import traceback

from shared import settings


def _log_crash(text):
    """A windowed .exe has no console, so write errors to a file next to it."""
    try:
        with open(os.path.join(settings.app_dir(), "hussaini_error.log"), "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def selftest():
    """Build the real window, let it process events once, and close it."""
    from client.gui import App
    app = App()
    app.update_idletasks()
    app.update()
    assert app.login is not None, "login screen was not created"
    app.close()
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if "--selftest" in argv:
            return selftest()
        from client.gui import App
        App().mainloop()
        return 0
    except Exception:
        text = traceback.format_exc()
        _log_crash(text)
        try:
            from tkinter import messagebox
            messagebox.showerror("Hussaini Chat", "The program hit an unexpected error.\n\n" + text[-800:])
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
