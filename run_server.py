"""Start the Hussaini Secure Chat server.

    python run_server.py              (or double-click HussainiServer.exe)
    python run_server.py --show-db    print what the database contains, then exit
    python run_server.py --list-users                  list every account
    python run_server.py --backup [FILE]               copy the database to a backup file
    python run_server.py --reset-password USERNAME     set a new password for an account

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


def list_users(db):
    users = db.user_summaries()
    print("\nACCOUNTS (%d)" % len(users))
    print("  %-16s %-24s %-20s %7s %9s" % ("username", "full name", "created (UTC)", "friends", "messages"))
    for u in users:
        print("  %-16s %-24s %-20s %7s %9s" % (u["username"], str(u["fullname"])[:24], str(u["date_created"])[:19],
                                               u["friends"], u["messages_sent"]))
    print()


def backup_database(db, target=None):
    import datetime
    import os
    if not target or target is True:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = os.path.join(config.app_dir(), "backups", f"chat-{stamp}.db")
    db.backup_to(target)
    print("\nBackup saved to:\n  %s\n(Copy this file somewhere safe. To restore it, replace data\\chat.db with it.)\n"
          % os.path.abspath(target))
    return 0


def reset_password(db, username, new_password, rounds):
    import getpass
    from shared import protocol as P
    from shared.crypto_utils import hash_password
    username = P.normalize_username(username)
    if not db.get_user(username):
        print("\nThere is no account called '%s'. Use --list-users to see the accounts.\n" % username)
        return 1
    if new_password is None:
        new_password = getpass.getpass("New password for %s: " % username)
        confirm = getpass.getpass("Type it again: ")
    else:
        confirm = new_password
    error = P.validate_password(new_password, confirm)
    if error:
        print("\n%s Nothing was changed.\n" % error)
        return 1
    db.update_password(username, hash_password(new_password, rounds))
    print("\nPassword for '%s' has been changed.\n"
          "Their passphrase is NOT affected, so their old chats are still readable.\n"
          "(If they were locked out by too many wrong attempts, restart the server or wait a minute.)\n" % username)
    return 0


def run_admin_command(argv, cfg):
    """Server-owner tools. They work on the database file directly, so no internet is needed."""
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--show-db", action="store_true")
    ap.add_argument("--list-users", action="store_true")
    ap.add_argument("--backup", nargs="?", const=True, default=None)
    ap.add_argument("--reset-password", metavar="USERNAME", default=None)
    ap.add_argument("--new-password", default=None, help=argparse.SUPPRESS)
    args, _ = ap.parse_known_args(argv)
    if not (args.show_db or args.list_users or args.backup or args.reset_password):
        return None
    db = Database(sqlite_path=cfg.database_file)
    try:
        if args.show_db:
            show_database(db)
        if args.list_users:
            list_users(db)
        if args.backup:
            backup_database(db, args.backup)
        if args.reset_password:
            return reset_password(db, args.reset_password, args.new_password, cfg.bcrypt_rounds)
        return 0
    finally:
        db.close()


def main():
    cfg = config.load()
    admin = run_admin_command(sys.argv[1:], cfg)
    if admin is not None:
        return admin
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
