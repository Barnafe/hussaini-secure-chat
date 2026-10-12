"""Encrypted media engine for Hussaini 1-to-1 calls (voice and video).

Needs the optional packages in ``requirements-media.txt`` (sounddevice, numpy, opencv).

What goes over the wire
-----------------------
* Voice:  microphone -> 20 ms mono 16 kHz PCM chunks -> AES-256-GCM -> server relay -> speaker.
* Video:  camera -> centre-cropped 4:3 -> 320x240 JPEG (about 12 per second) -> AES-256-GCM -> relay.
  An *empty* video packet means "the other side switched their camera off".
* The call key is derived (HKDF-SHA256) from the X25519 secret the two friends already share plus the
  unique call id. The server relays ciphertext it cannot read.

Every packet is sealed with AES-GCM, and what is authenticated alongside it (call id, who sent it,
kind and sequence number) means a packet cannot be edited, replayed, reordered backwards, moved to
another call, or reflected back at its own sender.

Threads
-------
Audio callbacks never touch the network: they only drop bytes into a bounded queue. A sender thread
encrypts and sends. The callbacks ``on_video``, ``on_local_video`` and ``on_error`` may be invoked
from ANY thread, so the window must hand them over to the Tk thread safely (the app does).
"""
from __future__ import annotations

import base64
import os
import queue
import threading
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

AUDIO_RATE = 16000
AUDIO_SAMPLES = 320                 # 20 ms at 16 kHz
VIDEO_SIZE = (320, 240)
VIDEO_FPS = 12
VIDEO_QUALITY = 55
MAX_JPEG_BYTES = 45_000             # a bigger frame is dropped, never sent (base64 + JSON must fit MAX_FRAME)
MAX_MEDIA_TEXT = 78_000             # the server refuses longer payloads
PREFILL_FRAMES = 2                  # play audio only after this many 20 ms chunks have arrived (jitter buffer)
MAX_PLAYBACK_FRAMES = 10            # never let more than 200 ms of audio pile up (keeps latency low)
NONCE_SIZE = 12
TAG_SIZE = 16
MIN_PACKET = NONCE_SIZE + TAG_SIZE  # the smallest valid packet is an empty message (the "camera off" marker)


def calling_support():
    """(ok, missing): are the optional calling packages importable? Never opens a device."""
    missing = []
    for name in ("numpy", "sounddevice", "cv2"):
        try:
            __import__(name)
        except Exception:                                # ImportError, or a missing PortAudio library
            missing.append(name)
    return not missing, missing


class MediaUnavailable(RuntimeError):
    """A friendly, user-readable reason why voice/video cannot start."""


def derive_call_key(identity, peer_public, peer, call_id):
    """The 32-byte key of one call - identical on both sides, different for every call."""
    if identity is None or not peer_public:
        raise MediaUnavailable("The friend's encryption key is not available. Reopen the friend list and try again.")
    base = identity.pair_key(peer_public, peer)
    names = "|".join(sorted((identity.username, peer))).encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=call_id.encode("utf-8"),
                info=b"hussaini-call-media-v1|" + names).derive(base)


def packet_aad(call_id, sender, kind, seq):
    return f"hussaini-call-v2|{call_id}|{sender}|{kind}|{seq}".encode("utf-8")


class EncryptedMediaEngine:
    def __init__(self, controller, call_id, peer, media, on_video=None, on_local_video=None, on_error=None,
                 audio_input=None, audio_output=None, camera_index=0):
        self.ctrl, self.call_id, self.peer, self.media = controller, call_id, peer, media
        self.on_video, self.on_local_video, self.on_error = on_video, on_local_video, on_error
        self.audio_input, self.audio_output = audio_input, audio_output
        try:
            self.camera_index = max(0, min(4, int(camera_index)))
        except (TypeError, ValueError):
            self.camera_index = 0
        self.me = controller.identity.username if controller.identity is not None else ""
        self._key = derive_call_key(controller.identity, controller.peer_keys.get(peer), peer, call_id)
        self._aes = AESGCM(self._key)
        self._stop = threading.Event()
        self._audio_in = queue.Queue(maxsize=MAX_PLAYBACK_FRAMES)
        self._media_out = queue.Queue(maxsize=40)
        self._play_buf = bytearray()
        self._primed = False
        self._counter = 0
        self._counter_lock = threading.Lock()
        self._last_seq = {"audio": 0, "video": 0}      # replay protection (TCP keeps order, so seq only grows)
        self._streams = []
        self._out_channels = 1
        self._video_thread = None
        self._sender_thread = None
        self._camera_lock = threading.Lock()
        self.muted = False
        self.camera_enabled = media == "video"
        self.started = False
        self.notes = []                                # friendly warnings, e.g. "default speaker used instead"
        self.stats = {"audio_sent": 0, "audio_played": 0, "video_sent": 0, "video_received": 0,
                      "rejected": 0, "dropped_out": 0}

    # ------------------------------------------------------------------ #
    # start / stop
    # ------------------------------------------------------------------ #
    def start(self):
        try:
            import numpy  # noqa: F401
            import sounddevice as sd
        except ImportError as exc:
            raise MediaUnavailable("Voice and video need the optional calling packages. "
                                   "Run:  pip install -r requirements-media.txt") from exc
        try:
            self._open_streams(sd)
        except MediaUnavailable:
            self.stop()
            raise
        self.started = True
        self._sender_thread = threading.Thread(target=self._send_loop, name="call-send", daemon=True)
        self._sender_thread.start()
        self._video_thread = threading.Thread(target=self._video_loop, name="call-video", daemon=True)
        self._video_thread.start()                      # idles until the camera is switched on
        return self

    def _open_streams(self, sd):
        """Open the chosen devices; if one is unplugged/busy, keep the other choice and use the default."""
        attempts = [(self.audio_input, self.audio_output)]
        if self.audio_input is not None:
            attempts.append((None, self.audio_output))
        if self.audio_output is not None:
            attempts.append((self.audio_input, None))
        if self.audio_input is not None and self.audio_output is not None:
            attempts.append((None, None))
        errors = []
        for number, (dev_in, dev_out) in enumerate(dict.fromkeys(attempts)):
            try:
                self._open_pair(sd, dev_in, dev_out)
                if number:
                    self.notes.append("Your chosen microphone/speaker could not be opened, so the system "
                                      "default was used instead.")
                return
            except Exception as exc:                    # PortAudioError, ValueError, ...
                errors.append(exc)
                self._close_streams()
        raise MediaUnavailable("The microphone or speaker could not be opened. Check that one is plugged in and "
                               "that this app may use the microphone (Windows: Settings > Privacy > Microphone). "
                               f"Details: {errors[0] if errors else 'no audio device'}")

    def _open_pair(self, sd, dev_in, dev_out):
        inp = sd.InputStream(device=dev_in, samplerate=AUDIO_RATE, channels=1, dtype="int16",
                             blocksize=AUDIO_SAMPLES, callback=self._audio_capture)
        self._streams.append(inp)
        out = None
        for channels in (1, 2):                          # some speakers only accept stereo
            try:
                out = sd.OutputStream(device=dev_out, samplerate=AUDIO_RATE, channels=channels, dtype="int16",
                                      blocksize=AUDIO_SAMPLES, callback=self._audio_playback)
                self._out_channels = channels
                break
            except Exception:
                if channels == 2:
                    raise
        self._streams.append(out)
        inp.start()
        out.start()

    def _close_streams(self):
        for stream in self._streams:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._streams.clear()

    def stop(self):
        self._stop.set()
        self._close_streams()
        for t in (self._video_thread, self._sender_thread):
            if t is not None and t is not threading.current_thread():
                t.join(timeout=1.5)                      # the camera thread releases the camera itself
        self.started = False

    # ------------------------------------------------------------------ #
    # audio callbacks (real-time: no sockets, no locks that can block, no exceptions)
    # ------------------------------------------------------------------ #
    def _audio_capture(self, indata, frames, timing, status):
        if self._stop.is_set() or self.muted:
            return
        self._enqueue("audio", bytes(indata))

    def _audio_playback(self, outdata, frames, timing, status):
        import numpy as np
        try:
            needed = frames * 2
            buf = self._play_buf
            if not self._primed and self._audio_in.qsize() >= PREFILL_FRAMES:
                self._primed = True
            if self._primed:
                while len(buf) < needed:
                    try:
                        buf += self._audio_in.get_nowait()
                    except queue.Empty:
                        break
                if not buf:
                    self._primed = False                 # ran dry: wait for the buffer to refill
            chunk = bytes(buf[:needed]).ljust(needed, b"\x00")
            del buf[:needed]
            samples = np.frombuffer(chunk, dtype=np.int16).reshape(-1, 1)
            if self._out_channels == 2:
                samples = np.repeat(samples, 2, axis=1)
            outdata[:] = samples
            if chunk.strip(b"\x00"):
                self.stats["audio_played"] += 1
        except Exception:
            try:
                outdata.fill(0)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # sending
    # ------------------------------------------------------------------ #
    def _enqueue(self, kind, data):
        """Queue a packet for the sender thread. Full queue: the oldest packet is dropped."""
        item = (kind, bytes(data))
        try:
            self._media_out.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            self._media_out.get_nowait()
            self.stats["dropped_out"] += 1
        except queue.Empty:
            pass
        try:
            self._media_out.put_nowait(item)
        except queue.Full:
            pass

    _send_packet = _enqueue                              # name used by older tests

    def seal(self, kind, data):
        """Encrypt one packet. Returns (seq, base64 text)."""
        with self._counter_lock:
            self._counter += 1
            seq = self._counter
        nonce = os.urandom(NONCE_SIZE)
        sealed = self._aes.encrypt(nonce, data, packet_aad(self.call_id, self.me, kind, seq))
        return seq, base64.b64encode(nonce + sealed).decode("ascii")

    def _send_loop(self):
        while not self._stop.is_set():
            try:
                kind, data = self._media_out.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                seq, text = self.seal(kind, data)
                if len(text) > MAX_MEDIA_TEXT:
                    continue
                if self.ctrl.send_call_media(self.call_id, kind, seq, text):
                    self.stats[kind + "_sent"] = self.stats.get(kind + "_sent", 0) + 1
            except Exception as exc:                     # one bad packet must never kill the call
                self._report(exc)

    # ------------------------------------------------------------------ #
    # receiving
    # ------------------------------------------------------------------ #
    def receive(self, ev):
        """Handle one ``call_media`` event from the server. Anything not genuine is silently dropped."""
        try:
            kind, seq, text = ev.get("kind"), ev.get("seq"), ev.get("payload", "")
            if (kind not in ("audio", "video") or not isinstance(seq, int) or isinstance(seq, bool)
                    or not isinstance(text, str) or len(text) > MAX_MEDIA_TEXT):
                return self._reject()
            if ev.get("from") not in (None, self.peer) or seq <= self._last_seq[kind]:
                return self._reject()                    # not from my friend / replayed / out of order
            raw = base64.b64decode(text.encode("ascii"), validate=True)
            if len(raw) < MIN_PACKET:
                return self._reject()
            data = self._aes.decrypt(raw[:NONCE_SIZE], raw[NONCE_SIZE:],
                                     packet_aad(self.call_id, self.peer, kind, seq))
        except Exception:
            return self._reject()
        self._last_seq[kind] = seq                       # only after the packet proved genuine
        if kind == "audio":
            try:
                self._audio_in.put_nowait(data)
            except queue.Full:
                try:
                    self._audio_in.get_nowait()          # drop the oldest: speak "now", not 2 seconds ago
                    self._audio_in.put_nowait(data)
                except (queue.Empty, queue.Full):
                    pass
        else:
            self.stats["video_received"] += 1
            if self.on_video:
                try:
                    self.on_video(data)                  # b"" = the friend switched their camera off
                except Exception:
                    pass

    def _reject(self):
        self.stats["rejected"] += 1

    # ------------------------------------------------------------------ #
    # video
    # ------------------------------------------------------------------ #
    def _video_loop(self):
        """Idles until the camera is on; opens it, streams frames, releases it when switched off."""
        camera, cv2 = None, None
        try:
            while not self._stop.is_set():
                if not self.camera_enabled:
                    if camera is not None:                # really let go of the camera (its light goes out)
                        camera.release()
                        camera = None
                    time.sleep(0.1)
                    continue
                if cv2 is None:
                    try:
                        import cv2
                    except ImportError:
                        self._camera_failed(MediaUnavailable("Video needs the optional calling packages. "
                                                             "Run:  pip install -r requirements-media.txt"))
                        continue
                if camera is None:
                    camera = cv2.VideoCapture(self.camera_index)
                    if not camera.isOpened():
                        camera.release()
                        camera = None
                        self._camera_failed(MediaUnavailable(
                            f"Camera {self.camera_index} could not be opened. Close other programs that use it, "
                            "pick another camera in Settings, and check that this app may use the camera "
                            "(Windows: Settings > Privacy > Camera)."))
                        continue
                started = time.monotonic()
                ok, frame = camera.read()
                if ok and self.camera_enabled:           # (it may have been switched off while we were reading)
                    jpg = self.encode_frame(cv2, frame)
                    if jpg is not None:
                        self._enqueue("video", jpg)
                        if self.on_local_video:
                            try:
                                self.on_local_video(jpg)
                            except Exception:
                                pass
                pause = 1.0 / VIDEO_FPS - (time.monotonic() - started)
                time.sleep(max(0.005, pause))
        except Exception as exc:
            self._camera_failed(exc)
        finally:
            if camera is not None:
                try:
                    camera.release()
                except Exception:
                    pass

    @staticmethod
    def encode_frame(cv2, frame):
        """Centre-crop to 4:3, shrink to 320x240 and JPEG-compress. None if it is too big to send."""
        h, w = frame.shape[:2]
        if w * 3 > h * 4:                                # wider than 4:3: trim the sides
            new_w = h * 4 // 3
            x = (w - new_w) // 2
            frame = frame[:, x:x + new_w]
        elif w * 3 < h * 4:                              # taller than 4:3: trim top and bottom
            new_h = w * 3 // 4
            y = (h - new_h) // 2
            frame = frame[y:y + new_h, :]
        frame = cv2.resize(frame, VIDEO_SIZE, interpolation=cv2.INTER_AREA)
        good, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), VIDEO_QUALITY])
        data = jpg.tobytes() if good else None
        return data if data is not None and len(data) <= MAX_JPEG_BYTES else None

    def _camera_failed(self, exc):
        self.camera_enabled = False
        self._report(exc)

    def set_muted(self, muted):
        self.muted = bool(muted)

    def set_camera(self, enabled):
        """Switch the camera on (this also upgrades a voice call to video) or off."""
        enabled = bool(enabled)
        if enabled == self.camera_enabled:
            return
        self.camera_enabled = enabled
        if not enabled:
            self._enqueue("video", b"")                  # tell the friend: stop showing my last frame

    def _report(self, exc):
        if self.on_error:
            try:
                self.on_error(str(exc))
            except Exception:
                pass
