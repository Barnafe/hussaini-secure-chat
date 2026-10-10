"""Start the Hussaini Secure Chat server.

    python run_server.py              (or double-click HussainiServer.exe)
    python run_server.py --show-db    print what the database contains, then exit

Settings (port, database file, ...) come from settings.txt.
"""
import logging
import signal
import socket
import sys
import threading

from server import config
from server.app import ChatServer, start_server
from server.database import Database


def lan_address():
    """Best guess of this computer's address on the local network (sends no data)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def pause_if_exe():
    """A double-clicked .exe closes its window on exit - keep it open so the error can be read."""
    if getattr(sys, "frozen", False):
        try:
            input("\nPress Enter to close this window...")
        except EOFError:
            pass


def show_database(db):
    """Print what the database really contains - proof that the server cannot read the chats."""
    def cut(text, n):
        text = str(text)
        return text if len(text) <= n else text[:n] + "..."

    users = db.raw_dump("users")
    print("\nUSERS (%d)  - passwords are bcrypt hashes, never plain text" % len(users))
    for u in users:
        print("  %-14s %-22s password=%s  public_key=%s" % (
            u["username"], cut(u["fullname"], 22), cut(u["password"], 24), cut(u["public_key"], 16)))
    for table in ("messages", "group_messages"):
        rows = db.raw_dump(table)
        print("\n%s (%d)  - the text is AES-256-GCM ciphertext (showing the last 15)" % (table.upper(), len(rows)))
        for r in rows[-15:]:
            print("  #%-4s %-12s %s" % (r["message_id"], r["sender"], cut(r["encrypted_message"], 80)))
    print()


def main():
    cfg = config.load()
    if "--show-db" in sys.argv[1:]:
        db = Database(sqlite_path=cfg.database_file)
        show_database(db)
        db.close()
        return 0
    logging.basicConfig(level=cfg.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("chat.server")
    db = Database(sqlite_path=cfg.database_file)
    log.info("Database: SQLite file %s", db.path)
    chat = ChatServer(db, bcrypt_rounds=cfg.bcrypt_rounds)
    try:
        server = start_server(chat, cfg.host, cfg.port)
    except OSError as exc:
        db.close()
        print(f"\nCould not start the server on port {cfg.port}: {exc}\n"
              "Is another copy of the server already running? Close it, or change "
              "'server_port' in settings.txt.")
        pause_if_exe()
        return 1
    log.info("Secure chat server listening on %s:%s", cfg.host, server.port)
    print("\nClients on THIS computer connect to:   localhost:%d" % server.port)
    lan = lan_address()
    if lan:
        print("Clients on OTHER computers connect to: %s:%d" % (lan, server.port))
    print("Keep this window open while people are chatting. Press Ctrl+C to stop.\n")

    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, lambda *_: stop.set())
            except (ValueError, OSError):
                pass
    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")
        server.stop()
        chat.close_all()
        db.close()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:                       # bad value in settings.txt
        print(exc)
        pause_if_exe()
        code = 1
    except Exception:
        import traceback
        traceback.print_exc()
        pause_if_exe()
        code = 1
    sys.exit(code)
