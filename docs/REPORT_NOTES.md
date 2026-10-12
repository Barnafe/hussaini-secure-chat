# Notes for the report (what to say so it matches the finished system)

These are the places where the finished program differs from, or adds to, Chapters 1-3.
Tell your supervisor about them in Chapter 4 or Chapter 5 - they are improvements, not mistakes.

| Chapter 3 says | The finished system | Suggested wording |
|---|---|---|
| 3.10 Software tools: **PyCryptodome** for AES | The **`cryptography`** library (AES-256-GCM, X25519, HKDF) | "AES-256 in GCM mode was implemented with Python's `cryptography` library." Change the 3.10 table row. |
| AES encryption of messages | AES-256-GCM, with a key agreed between two users from their personal passphrases (X25519 + HKDF) | "Each user receives a personal passphrase; two users derive a shared AES-256 key without the server ever seeing it." |
| (not in the document) delivery ticks, typing indicator, chat-list previews, alerts, safety numbers, change password, group rename / admin hand-over, server-owner tools, installer | all implemented as extras | "Additional features beyond the original scope" |
| One-to-one chat between authenticated users | Also: friend requests with a searchable "Find people" list, online list, group chat, offline delivery, persistent history | "Additional features beyond the original scope." |
| TCP sockets, multithreaded server, SQLite, Tkinter, bcrypt | Exactly as stated | - |
| Local-network deployment | Packaged as `HussainiServer.exe` + `HussainiChat.exe` (PyInstaller), built by GitHub Actions | "Packaging for easy installation on Windows." |

## Limitations worth listing in Chapter 5
* The account password is sent to the server over plain TCP (no TLS); messages themselves are always encrypted.
* The server can see metadata: who talks to whom, and when (also delivery and typing notices).
* Safety numbers protect against a swapped key only if two friends actually compare them; the check is manual.
* Any signed-in user can list every registered user's name and username (needed for the "Find people" list); there is no privacy setting to hide an account.
* There is no password reset (the server stores only a bcrypt hash and nothing identifies the owner), and a lost passphrase cannot be recovered.
* No passphrase recovery; no forward secrecy; no check that a user's public key is genuine (no safety numbers).
* One thread per client is fine for a class-sized group, not for thousands of users.

## Evidence you can use in Chapter 4
* `pytest` output (all test files) and `tests/results/performance.md` (latency, throughput).
* `HussainiServer.exe --show-db` prints the database: bcrypt hashes for passwords and unreadable ciphertext for
  messages - good for a screenshot proving the server cannot read chats.
* Screenshots: login, registration with passphrase window, friends list, chat, group chat.
