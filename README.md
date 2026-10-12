# Hussaini Secure Chat

A secure desktop chat application: **Python client (Tkinter) + raw-TCP server (one thread per client)**,
with every message encrypted on the sender's computer using **AES-256-GCM** before it leaves the machine.
The server and its **SQLite** database only ever see ciphertext. Built from Chapters 1-3 of the project
report (`ATBU_CHAPTERS_TEACHER.docx`).

## Features

| Area | What it does |
|---|---|
| Accounts | Register (full name, username, password, confirm) and login. Passwords are hashed with **bcrypt**. Failed-login lockout after 5 tries. One active session per account. |
| **Personal passphrase** | Created **automatically at registration** and shown once (copy / save to file). It is needed at every login and never leaves the computer. |
| **Profile** | Each user has a profile picture (any JPG/PNG/GIF/BMP/WebP, cropped to a square on the user's computer), an editable full name, a short status line and a change-password form (Settings). Friends see changes live. |
| **ChatSphere look** | Dark-blue interface: sidebar (Chats / Contacts / Groups / Calls / Settings, search, chat list with previews and unread badges), conversation with bubbles, and a contact-info panel (shared links, mute, wallpaper, clear chat, block). |
| Friends | **Add friend** opens a "Find people" list showing every user straight away (online/offline marker, relationship status) with a **search bar** to filter by name or username. Send a request, the other person accepts or declines; remove friends any time. |
| History | Log out and log in again: your chats are still there (kept on the server as ciphertext, decrypted on your computer with your passphrase). Each chat reloads its latest 200 messages. |
| Private chat | One-to-one chat between friends, real-time. |
| Offline delivery | Messages to an offline friend are stored (encrypted) and delivered at their next login. |
| Group chat | Groups made from your friends. Creator is admin (add / remove); anyone can leave. New members only see messages sent after they joined. |
| History | Loaded from the database and decrypted on your machine. |
| Encryption | AES-256-GCM (confidentiality **and** tamper detection). Keys come from X25519 key agreement between the two users' passphrase-derived key pairs. |
| Reliability | Keep-alive pings, auto-reconnect + automatic re-login, flood control, idle clean-up. |
| Database | **SQLite** only (`data/chat.db`). |
| **Delivery ticks** | One tick = stored by the server, two ticks = delivered to your friend (also when they log in later). |
| **Chat list** | Newest conversation on top, with a one-line preview of the last message (decrypted on your computer). |
| **Typing indicator** | "Bob is typing..." appears in the chat (relayed by the server, never stored). |
| **Chat polish** | "Today / Yesterday" date dividers and a different colour for each sender's name in groups. |
| **Alerts** | Sound + flashing taskbar button when a message arrives while the window is in the background (switch with the *Sound* box); the title shows the unread count. |
| **Safety numbers** | *Verify Security* (chat menu / contact panel) in a private chat shows a 30-digit number both friends can compare to be sure nobody swapped their keys; verified friends get a badge, and you are warned if a key changes. |
| **Change password** | In the app (*Change password* button): asks for the current password first; your passphrase and old chats are not affected. |
| **Groups** | The admin can also **rename** the group and **hand over** the admin role. |
| **About box and icon** | Version, credits and technology (*About*); the programs have their own icon. |
| **Server-owner tools** | `HussainiServer.exe --list-users`, `--backup`, `--reset-password USER`, `--show-db` (or `server-tools.bat`). No internet needed. |
| Packaging | `HussainiChat.exe` + `HussainiServer.exe` (PyInstaller), plus an optional **installer** (`HussainiChat-Setup.exe`, desktop shortcut), all built by GitHub Actions. |

## 1. Folder structure

```
Hussaini/
├── run_server.py            start the server
├── run_client.py            start the desktop client
├── settings.txt             port, database file, default server address
├── start-demo-from-source.bat   one click (developers): venv + server + 2 clients
├── requirements*.txt        runtime / dev / build dependencies
├── shared/
│   ├── protocol.py          length-prefixed JSON framing, host:port parsing, validation
│   ├── crypto_utils.py      AES-256-GCM, PBKDF2, bcrypt
│   ├── keys.py              passphrase generation, X25519 identity, DM + group envelopes
│   └── settings.py          settings.txt loader
├── server/
│   ├── app.py               TCP listener, thread per client, sessions, auth, friends, groups
│   ├── database.py          SQLite data layer
│   └── config.py            server settings
├── client/
│   ├── network.py           background socket thread, pings, auto-reconnect
│   ├── controller.py        all client logic (keys, encrypt/decrypt, state, profiles) - no GUI code
│   ├── gui.py               login screen, dialogs, settings/profile page, main window
│   ├── panels.py            sidebar, conversation and contact-info panels
│   ├── widgets.py           rounded buttons, entries, chat list, message view (Pillow + Tk)
│   ├── theme.py             colours (from the design mock-up), fonts, image cache
│   ├── icons.py             vector icons drawn with Pillow (no image files to ship)
│   ├── images.py            profile-picture processing, avatars, bubbles, wallpapers
│   ├── prefs.py             per-computer preferences (muted chats, wallpaper) -> client_prefs.json
│   └── main.py              entry point (+ --selftest)
├── packaging/               PyInstaller specs, release assembler, build smoke test, Windows files
├── .github/workflows/build-windows.yml   builds + tests + packages the Windows zip
├── tests/                   pytest suite (section 5)
└── docs/ARCHITECTURE.md     design notes, protocol table, DB schema
```

## 2. Run it from source (VS Code)

Requirements: **Python 3.10+** (Tkinter comes with the python.org Windows installer;
on Ubuntu/Debian: `sudo apt install python3-tk`).

```powershell
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run_server.py              # terminal 1
python run_client.py              # terminal 2 (and a third terminal for a second user)
```

Or double-click `start-demo-from-source.bat` on Windows.

In each client: **Server** `localhost:8765` → *Register* → copy/save the passphrase shown → *Login*
(username, password, passphrase) → *Online* tab → send a friend request → the other user accepts in *Friends*.

Change the port or database location in `settings.txt` (or with `HUSSAINI_SERVER_PORT`, `HUSSAINI_DATABASE_FILE`, …).
A different computer connects with `<server-ip>:8765` (the server prints its address when it starts).

## 3. Build the Windows .exe files

**On GitHub (recommended):** push the project; the build starts by itself (or use
**Actions → Build Windows package → Run workflow**). It runs the tests, builds both `.exe` files with
PyInstaller, smoke-tests them, and publishes `HussainiChat-Windows` on the run page. Download it,
**extract it into a folder**, then double-click `start-demo.bat`. Pushing a tag such as `v1.0` also creates a
GitHub Release with the zip attached. Full step-by-step: `docs/GITHUB_AND_DELIVERY_GUIDE.md`.

**Locally on Windows:**

```powershell
pip install -r requirements-build.txt -r requirements-media.txt     # media = voice/video calls
pyinstaller --noconfirm --clean packaging/HussainiServer.spec
pyinstaller --noconfirm --clean packaging/HussainiChat.spec
python packaging/smoke_test.py dist/HussainiServer.exe
dist\HussainiChat.exe --selftest-calls     # exit code 0 = the calling packages are inside the .exe
python packaging/assemble.py          # -> release/HussainiChat-Windows(.zip)
```

## 4. How the passphrase and encryption work

1. **Register:** the client invents a random passphrase (`K7QM-X2NP-H9TD-R4WB-C6VF`, 100 bits) and shows it
   once. PBKDF2-HMAC-SHA256 (600,000 rounds, salted with the username) turns it into a private X25519 key.
   Only the **public key** is sent to the server.
2. **Login:** username + password (bcrypt on the server) + passphrase. The client sends its public key
   so the server can confirm the passphrase belongs to the account (it never sees the passphrase).
3. **Direct messages:** `X25519(my private, friend's public)` → HKDF → one AES-256-GCM key shared by the two
   friends. The sender and conversation are bound into the tag (AAD).
4. **Groups:** each message gets a random key; that key is wrapped for every current member with their pairwise key.
5. The server stores `base64(nonce | tag | ciphertext)` and public keys, nothing else.

## 5. Run the tests

```bash
pip install -r requirements-dev.txt
pytest                                  # everything
pytest tests/test_security.py -v        # one phase
```

| File | What it proves |
|---|---|
| `test_crypto.py` | AES-256-GCM round-trip, tamper detection, wrong key, replay binding, bcrypt |
| `test_keys.py` | passphrase format, key agreement, DM + group envelopes, outsiders cannot read |
| `test_protocol.py` | framing over real sockets (split/partial/oversized frames), `host:port` parsing |
| `test_settings.py` | settings.txt loading, validation messages, environment overrides |
| `test_database.py` | users, public keys, friends, offline queue, groups, thread safety, upgrade of old DBs |
| `test_server.py` | registration/login rules, passphrase check, presence, friends, DM, offline delivery, groups |
| `test_client.py` | real clients end to end: auto-generated passphrase, encryption, offline, reconnect |
| `test_gui_smoke.py` | drives the real Tkinter window: register, passphrase window, friends, chat, unread, groups, profile photo / name / password (skipped without a display) |
| `test_profile.py` | profile name, status line, picture upload/fetch/remove, password change, chat-list previews (server + database) |
| `test_client_profile.py` | the controller side of the same features, plus mute / clear chat / shared links / list ordering |
| `test_images.py` | every icon renders, profile pictures are cropped and shrunk to fit the server limit, bad files are refused |
| `test_security.py` | wire sniffing shows only ciphertext, DB holds only ciphertext/hashes/public keys, forgery, SQL injection, garbage frames |
| `test_performance.py` | latency, 20 concurrent clients, crypto cost → writes `tests/results/performance.md` |
| `test_entrypoints.py` | `run_server.py` really starts/stops, packaging files and workflow are consistent, release assembly |

On a headless Linux box run the GUI test with `xvfb-run pytest tests/test_gui_smoke.py`.

## 5b. Profile pictures and details

Open **Settings** (left menu, or the menu icon at the top of the sidebar). *Change photo* accepts common image
files; the picture is cropped to a centred square, shrunk to 256 x 256 and sent as a small JPEG (about 10-25 KB).
Friends only receive a short version tag in their friend list and download the picture once when they first need
it (`avatar_get`), so a long friend list never bloats a message. Name and status changes reach everyone who is
online immediately; others see them at their next login.

Local-only settings (muted chats, *Clear chat* cut-offs, chat wallpaper) are stored next to the program in
`client_prefs.json` and never leave the computer. *Clear chat* hides the messages on this computer only.

Not built yet (the buttons show a short notice): sending photos or files. The *Media & Files* and *Shared
Documents* boxes stay empty until file sending exists; *Shared Links* is filled from links in the chat.
Voice and video calls are built: see *Voice and video calls* at the end of this file and `CALLS.md`.

## 6. Security notes and known limitations (good material for the report)

* **End-to-end:** encryption/decryption only happen in `client/controller.py` (via `shared/keys.py`).
* **Integrity:** AES-GCM fails on any change; ciphertexts cannot be replayed into another chat or group.
* **Passwords:** bcrypt, constant-ish timing for unknown users, 5-failure lockout.
* **Plain TCP, no TLS:** messages are end-to-end encrypted, but the account **password** is sent unencrypted
  and the server sees metadata (who talks to whom, when). Next step: wrap the socket in TLS.
* **Key directory trust:** the server hands out public keys; friends can compare a safety number (*Verify Security* in the chat menu or the contact panel) to detect a swapped key, but the program cannot check it for you.
* **No forward secrecy:** whoever learns a passphrase can read that user's past messages.
* **No passphrase recovery** (by design). A lost passphrase means a new account.
* One thread per client is simple and fine for a class-sized deployment, not for thousands of users.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: tkinter` | Windows: reinstall Python from python.org (tick *tcl/tk*). Linux: `sudo apt install python3-tk`. |
| Client says **Connection failed.** | Server not running, wrong `host:port`, or a firewall is blocking the port. |
| **That passphrase does not belong to this account.** | Use the passphrase shown when the account was registered. |
| Server: *port already in use* | Another server is running, or change `server_port` in `settings.txt`. |
| Window looks plain / `ModuleNotFoundError: PIL` | Run `pip install -r requirements.txt` (the new look needs Pillow). |
| Old `data/chat.db` from version 3 | Accounts made before passphrases have no key and are refused at login. Delete `data/chat.db` and register again. |

## Voice and video calls

One-to-one calls between friends, from the phone / video icons in the chat header and the contact panel, or
by one click on a row of the **Calls** tab (calling back a video call starts a video call).

* **Install the call packages** (once): `pip install -r requirements-media.txt` (numpy, sounddevice, opencv).
  Without them chat works as before and the call window explains what to install.
* **What you get:** ringing with a ringtone and an incoming-call popup (Accept / Decline), a "Calling..."
  window with Cancel, busy / offline / declined / no answer after 30 s, a call window with timer, mute, camera on/off
  and a small self-view in the corner, and a **Calls** tab with incoming, outgoing and missed calls and their duration.
  A voice call can be upgraded to video with *Start camera*.
* **How it travels:** audio is 20 ms mono 16 kHz PCM, video is 320x240 JPEG at about 12 frames per second. Both are
  encrypted on your computer with AES-256-GCM using a fresh key for every call (derived from the secret you already
  share with your friend plus the call id); the server only relays ciphertext. Every packet is bound to its call, its
  sender and its sequence number, so packets cannot be replayed, reordered, edited or reflected back.
* **A call is refused** (with a clear message) if the person is not your friend, if their key is missing, or if
  you had verified their safety number and it has since changed - compare safety numbers again first.
* **Devices:** *Settings > Calling devices* picks microphone, speaker and camera. If a chosen device is unplugged the
  system default is used. On Windows allow microphone/camera access under *Settings > Privacy*.
* **Limits (be honest in the report):** voice is uncompressed PCM (about 45 KB/s each way), there is no congestion
  control, calls need the server to be reachable by both people, and group calls do not exist. The call encryption
  has not been independently audited.
