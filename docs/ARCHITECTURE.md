# Architecture notes

## Components

```
 Tkinter GUI  ->  ChatController  ->  NetworkClient  ===== TCP =====>  TCPChatServer  ->  ChatServer  ->  Database
 (client/gui)     (keys, encrypt/      (thread, pings,   (length-prefixed (accept loop,     (one thread       (SQLite)
                   decrypt, state)      auto-reconnect)   JSON frames)     thread/client)    per client)
```

| Module | Responsibility |
|---|---|
| `shared/crypto_utils.py` | AES-256-GCM encrypt/decrypt, PBKDF2, bcrypt hash/verify |
| `shared/keys.py` | passphrase generation, X25519 identity, pairwise keys, DM and group envelopes |
| `shared/protocol.py` | framing, `host:port` parsing, message types, validation rules, limits, AAD builders |
| `shared/settings.py` | `settings.txt` loader (environment > file > defaults) |
| `server/database.py` | tables and queries (SQLite, one lock-protected connection) |
| `server/app.py` | TCP listener, thread per client, sessions, login/register, presence, friends, groups, routing, offline queue |
| `client/network.py` | plain TCP socket on a background thread, keep-alive pings, event queue, reconnect with back-off |
| `client/controller.py` | all client logic: encryption, decryption, unread counts, history merge |
| `client/gui.py` | login/registration (with the passphrase window), chat window, Add-friend (find people) and group dialogs |
| `run_server.py` | starts the server; `--show-db` prints the stored password hashes and ciphertext |

## Database schema

| Table | Columns |
|---|---|
| `users` | user_id, username (unique), password (bcrypt hash), fullname, public_key (X25519, base64), date_created |
| `messages` | message_id, sender, receiver, encrypted_message, timestamp, delivered |
| `friend_requests` | request_id, from_user, to_user, created |
| `friendships` | user_a, user_b (alphabetical pair), created |
| `chat_groups` | group_id, name, admin, created |
| `group_members` | group_id, username, first_visible_id, last_delivered_id, joined |
| `group_messages` | message_id, group_id, sender, encrypted_message, timestamp |

`users` and `messages` are the two tables of the report (Tables 3.1 and 3.2); the other tables support friends and groups.
`first_visible_id` hides messages that were sent before a member joined; `last_delivered_id` is the
per-member cursor used to deliver group messages that arrived while the member was offline.

## Wire format

A TCP stream of frames: `[4-byte big-endian length][UTF-8 JSON object]` (max 128 KB per frame). The JSON object always has a `type`.

## Protocol

Client → server

| type | fields |
|---|---|
| `register` | username, password, confirm, fullname, public_key |
| `login` / `logout` | username, password, public_key / – |
| `ping` | – (answered with `pong`; sent every 15 s) |
| `user_search` | query (empty = list everybody) |
| `friend_request` | to |
| `friend_respond` | from, accept |
| `friend_remove` | user |
| `send_dm` | to, payload (ciphertext) |
| `send_group` | group_id, payload (JSON envelope: sealed text + per-member wrapped key) |
| `history` | kind (`dm`/`group`), with / group_id, limit |
| `group_create` | name, members[] |
| `group_add` / `group_remove` | group_id, username |
| `group_leave` | group_id |

Server → client

| type | meaning |
|---|---|
| `register_result`, `login_result`, `pong` | `ok` + message |
| `state` | friends (with online flag), requests, groups, everyone online |
| `presence` | a user came online / went offline |
| `user_list` | users (username, fullname, online, relationship), more |
| `friend_request_in`, `request_sent`, `request_cleared`, `friend_added`, `friend_removed` | friend changes |
| `message`, `group_message` | ciphertext + sender + id + timestamp (`queued` = delivered from the offline queue) |
| `history` | ordered ciphertexts for one conversation |
| `group_update`, `group_removed` | group membership changes |
| `notice`, `error`, `kicked`, `logged_out` | feedback |

## Passphrase, keys and message flow

1. **Register.** The client creates a random passphrase, derives an X25519 key pair from it
   (PBKDF2-HMAC-SHA256, 600,000 rounds, salt = app salt + username) and sends only the public key.
   The passphrase is shown to the user once.
2. **Login.** The client re-derives the key pair from the typed passphrase and sends the public key with the
   login; the server (after the bcrypt password check) compares it with the stored key.
   A mismatch answers *"That passphrase does not belong to this account."*
3. **One-to-one.** Alice's controller computes `key = HKDF(X25519(alice_private, bob_public))`, builds
   `AAD = "dm|alice|bob"` and sends `AES-256-GCM(key, text, AAD)`. The server checks session, friendship, size and
   rate, stores the payload (delivered = 0), forwards it if Bob is online and echoes it to Alice.
4. **Offline.** The row stays queued; at Bob's next login it is sent (`queued: true`) and marked delivered.
5. **Group.** The sender encrypts the text with a random 256-bit message key, then wraps that key for each current
   member with the pairwise key. The server stores the opaque envelope. Members who join later never receive a wrap
   for older messages (and the server also hides them from history).
6. Any decryption failure shows `[Unable to decrypt this message]`.

## Mapping to the report

| Report section | Where |
|---|---|
| 3.5 Proposed system / 3.6 System architecture | `server/`, `client/`, `shared/` |
| 3.7 System flowchart / 3.8 Use case diagram | `tests/test_server.py`, `tests/test_client.py` (every use case is exercised) |
| 3.9 Database design (Tables 3.1 and 3.2) | `server/database.py` |
| 3.12 Input design | `shared/protocol.py` (`validate_registration`), `client/gui.py` forms |
| 3.13 Output design (messages) | `shared/protocol.py` (`MSG_*`) |
| 3.14 Security considerations | `shared/crypto_utils.py`, `server/app.py`, `tests/test_security.py` |
| Chapter 4 material | `tests/results/performance.md`, test output, screenshots |

## Deliberate deviations from the report text

| Report | Implementation | Why |
|---|---|---|
| Shared chat passphrase (report limitation) | Personal auto-generated passphrase + X25519 key agreement | each user gets their own secret; the server holds only public keys |
| One-to-one chat | plus friends (searchable "Find people" list), online list, groups, offline delivery, persistent history | requested extensions |
| PyCryptodome for AES (Table in 3.10) | `cryptography` library (AES-256-GCM, X25519, HKDF) | one library covers AES-GCM and the X25519 key agreement; update the 3.10 table in the report |
