# Voice and video calls - what exists and how it was checked

## Protocol (client <-> server, JSON frames)
| Message | From | Meaning |
|---|---|---|
| `call_start {to, media}` | caller | ring a friend (`audio` or `video`) |
| `call_result {status, ...}` | server | `ringing` (with the call) / `busy` / `offline` |
| `call_incoming {...}` | server | someone is ringing you |
| `call_accept` / `call_decline {call_id}` | callee | answer or refuse |
| `call_accepted {call_id, peer, media}` | server | both sides: the call is running |
| `call_end {call_id}` | either | hang up (caller before answer = cancelled, callee = declined) |
| `call_ended {call_id, status}` | server | `completed` `declined` `cancelled` `no_answer` `connection_lost` |
| `call_media {call_id, kind, seq, payload}` | either | encrypted audio / video packet, relayed unchanged |
| `call_history` | client | list my last calls (both directions, newest first) |

Server rules: friends only; one call per person at a time; unanswered after 30 s -> `no_answer`; a dropped
connection, a second login, unfriending or a server restart ends the call and closes its log entry; talk time
is counted from the answer, never from the first ring; relayed media is rate-limited (200 packets/s) and
ringing is rate-limited like messages.

## Encryption
Key = HKDF-SHA256(X25519 pair secret, salt = call id). Packet = nonce(12) + AES-256-GCM(data) with the call id,
the **sender's name**, the kind and the sequence number as authenticated data. Receivers drop anything whose
sequence number is not higher than the last accepted one. An empty video packet means "camera off".

## Files
`client/media_engine.py` (crypto, audio, camera), `client/call_ui.py` (popup, window, ringtone, manager),
`client/controller.py` (call state, history rows), `server/app.py` + `server/database.py` (signalling, log).

## Tests (`pytest`, the GUI tests need a display: `xvfb-run -a pytest`)
* `tests/test_calls_server.py` - real server, real sockets: every signalling path, log, flood limits, re-login, unfriend, restart.
* `tests/test_calls_client.py` - two real controllers + the real engine + real JPEG/AES; only microphone, speaker
  and webcam are simulated (a tone per device, so each side proves it heard the *other* person).
* `tests/test_calls_gui.py` - the real Tk window: popup, ringtone, window, Calls tab, camera upgrade, shutdown.

## Not verified here
Real microphones, speakers and webcams, Windows permission prompts and real network conditions. Try a call
between two computers before the demonstration.
