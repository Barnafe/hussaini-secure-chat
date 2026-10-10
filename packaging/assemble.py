"""Assemble the release folder (and zip) that is handed to users.

    python packaging/assemble.py --dist dist --out release

Takes HussainiServer.exe and HussainiChat.exe from --dist and adds start-demo.bat, show-database.bat,
settings.txt, README.txt, PRESENTATION_GUIDE.txt and an empty data folder.
Result:  <out>/HussainiChat-Windows/   and   <out>/HussainiChat-Windows.zip
"""
import argparse
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NAME = "HussainiChat-Windows"
EXES = ("HussainiServer.exe", "HussainiChat.exe")
TEXT_FILES = [  # (source, name in the release)
    (os.path.join(HERE, "windows", "start-demo.bat"), "start-demo.bat"),
    (os.path.join(HERE, "windows", "show-database.bat"), "show-database.bat"),
    (os.path.join(HERE, "windows", "README.txt"), "README.txt"),
    (os.path.join(HERE, "windows", "PRESENTATION_GUIDE.txt"), "PRESENTATION_GUIDE.txt"),
    (os.path.join(ROOT, "settings.txt"), "settings.txt"),
]


def write_crlf(src, dst):
    """Windows Notepad and cmd.exe like CRLF line endings."""
    with open(src, "rb") as fh:
        data = fh.read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with open(dst, "wb") as fh:
        fh.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--out", default=os.path.join(ROOT, "release"))
    args = ap.parse_args()

    missing = [e for e in EXES if not os.path.isfile(os.path.join(args.dist, e))]
    if missing:
        sys.exit("ERROR: cannot find " + ", ".join(missing) + " in " + args.dist)

    folder = os.path.join(args.out, NAME)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "data"))
    for exe in EXES:
        shutil.copy2(os.path.join(args.dist, exe), os.path.join(folder, exe))
    for src, name in TEXT_FILES:
        write_crlf(src, os.path.join(folder, name))
    with open(os.path.join(folder, "data", "put-your-database-here.txt"), "w") as fh:
        fh.write("The server creates chat.db in this folder the first time it runs.\r\n")

    zip_path = os.path.join(args.out, NAME + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(folder):
            for f in files:
                full = os.path.join(base, f)
                zf.write(full, os.path.join(NAME, os.path.relpath(full, folder)))
    print("Release folder:", folder)
    print("Release zip:   ", zip_path)


if __name__ == "__main__":
    main()
