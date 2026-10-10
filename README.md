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
| Friends | Friend request by username, accept / decline, remove. A list of **everyone online** helps you find people. |
| Private chat | One-to-one chat between friends, real-time. |
| Offline delivery | Messages to an offline friend are stored (encrypted) and delivered at their next login. |
| Group chat | Groups made from your friends. Creator is admin (add / remove); anyone can leave. New members only see messages sent after they joined. |
| History | Loaded from the database and decrypted on your machine. |
| Encryption | AES-256-GCM (confidentiality **and** tamper detection). Keys come from X25519 key agreement between the two users' passphrase-derived key pairs. |
| Reliability | Keep-alive pings, auto-reconnect + automatic re-login, flood control, idle clean-up. |
| Database | **SQLite** only (`data/chat.db`). |
| Packaging | `HussainiChat.exe` + `HussainiServer.exe` (PyInstaller), built by GitHub Actions. |

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
│   ├── controller.py        all client logic (keys, encrypt/decrypt, state) - no GUI code
│   ├── gui.py               Tkinter windows
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
pip install -r requirements-build.txt
pyinstaller --noconfirm --clean packaging/HussainiServer.spec
pyinstaller --noconfirm --clean packaging/HussainiChat.spec
python packaging/smoke_test.py dist/HussainiServer.exe
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
| `test_gui_smoke.py` | drives the real Tkinter window incl. the passphrase window (skipped without a display) |
| `test_security.py` | wire sniffing shows only ciphertext, DB holds only ciphertext/hashes/public keys, forgery, SQL injection, garbage frames |
| `test_performance.py` | latency, 20 concurrent clients, crypto cost → writes `tests/results/performance.md` |
| `test_entrypoints.py` | `run_server.py` really starts/stops, packaging files and workflow are consistent, release assembly |

On a headless Linux box run the GUI test with `xvfb-run pytest tests/test_gui_smoke.py`.

## 6. Security notes and known limitations (good material for the report)

* **End-to-end:** encryption/decryption only happen in `client/controller.py` (via `shared/keys.py`).
* **Integrity:** AES-GCM fails on any change; ciphertexts cannot be replayed into another chat or group.
* **Passwords:** bcrypt, constant-ish timing for unknown users, 5-failure lockout.
* **Plain TCP, no TLS:** messages are end-to-end encrypted, but the account **password** is sent unencrypted
  and the server sees metadata (who talks to whom, when). Next step: wrap the socket in TLS.
* **Key directory trust:** the server hands out public keys; there is no fingerprint / safety-number check yet.
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
| Old `data/chat.db` from version 3 | Accounts made before passphrases have no key and are refused at login. Delete `data/chat.db` and register again. |
