"""Simulated microphone, speaker and camera for the call tests.

Real code under test: the media engine, the controller, the TCP server and the encryption. Only the
hardware is simulated:

* a fake ``sounddevice`` whose input devices play a sine tone (each device has its own pitch) and whose
  output devices record everything they are asked to play;
* a fake camera (``cv2.VideoCapture``) that returns coloured frames. JPEG encoding is the real cv2.
"""
import sys
import threading
import time
import types

import numpy as np

RATE = 16000


class PortAudioError(Exception):
    pass


class FakeSoundDevice:
    """Install with ``FakeSoundDevice().install(monkeypatch)``."""

    def __init__(self, tones=None, bad_devices=(), stereo_only=()):
        self.tones = dict(tones or {})                 # input device index -> frequency (Hz)
        self.bad_devices = set(bad_devices)            # devices that cannot be opened
        self.stereo_only = set(stereo_only)            # output devices that refuse mono
        self.speakers = {}                             # output device -> list of int16 mono blocks played
        self.opened = []                               # (kind, device, channels)
        self.live = 0                                  # streams currently running
        self.default_tone = 300
        self._lock = threading.Lock()

    # ---- the module the engine imports --------------------------------- #
    def install(self, monkeypatch):
        mod = types.ModuleType("sounddevice")
        fake = self

        class _Stream:
            kind = "?"

            def __init__(self, device=None, samplerate=None, channels=1, dtype="int16", blocksize=0, callback=None):
                if device in fake.bad_devices:
                    raise PortAudioError(f"Error opening {self.kind}Stream: Invalid device {device}")
                if self.kind == "Output" and channels == 1 and device in fake.stereo_only:
                    raise PortAudioError("Invalid number of channels")
                assert samplerate == RATE and dtype == "int16"
                self.device, self.channels, self.callback = device, channels, callback
                self.blocksize = blocksize or 320
                self._run = False
                self._thread = None
                fake.opened.append((self.kind, device, channels))

            def start(self):
                self._run = True
                with fake._lock:
                    fake.live += 1
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

            def stop(self):
                if self._run:
                    self._run = False
                    with fake._lock:
                        fake.live -= 1

            def close(self):
                self.stop()

        class InputStream(_Stream):
            kind = "Input"

            def _loop(self):
                freq = fake.tones.get(self.device, fake.default_tone)
                n = 0
                nxt = time.monotonic()
                while self._run:
                    t = (np.arange(self.blocksize) + n) / RATE
                    block = (np.sin(2 * np.pi * freq * t) * 12000).astype(np.int16).reshape(-1, 1)
                    n += self.blocksize
                    self.callback(block, self.blocksize, None, None)
                    nxt += self.blocksize / RATE
                    time.sleep(max(0, nxt - time.monotonic()))

        class OutputStream(_Stream):
            kind = "Output"

            def _loop(self):
                nxt = time.monotonic()
                while self._run:
                    out = np.zeros((self.blocksize, self.channels), dtype=np.int16)
                    self.callback(out, self.blocksize, None, None)
                    with fake._lock:
                        fake.speakers.setdefault(self.device, []).append(out[:, 0].copy())
                    nxt += self.blocksize / RATE
                    time.sleep(max(0, nxt - time.monotonic()))

        mod.InputStream, mod.OutputStream = InputStream, OutputStream
        mod.PortAudioError = PortAudioError
        mod.query_devices = lambda: [{"name": "Default mic", "max_input_channels": 1, "max_output_channels": 0},
                                     {"name": "Default speaker", "max_input_channels": 0, "max_output_channels": 2}]
        monkeypatch.setitem(sys.modules, "sounddevice", mod)
        return self

    # ---- what a speaker heard ------------------------------------------- #
    def heard(self, device, last_seconds=1.0):
        with self._lock:
            blocks = list(self.speakers.get(device, []))
        n = int(last_seconds * RATE / 320)
        return np.concatenate(blocks[-n:]) if blocks else np.zeros(0, dtype=np.int16)

    def dominant_frequency(self, device, last_seconds=1.0):
        samples = self.heard(device, last_seconds).astype(np.float64)
        if len(samples) < RATE // 4 or not samples.any():
            return 0.0
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        return float(np.argmax(spectrum) * RATE / len(samples))

    def loudness(self, device, last_seconds=0.5):
        samples = self.heard(device, last_seconds).astype(np.float64)
        return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


class FakeCamera:
    """cv2.VideoCapture replacement: 640x480 frames whose colour is set per camera index."""
    instances = []
    colours = {0: (0, 0, 255), 1: (0, 255, 0), 2: (255, 0, 0)}     # BGR
    unavailable = set()

    def __init__(self, index=0, *a):
        self.index = index
        self.open = index not in FakeCamera.unavailable
        self.released = False
        self.reads = 0
        FakeCamera.instances.append(self)

    def isOpened(self):
        return self.open and not self.released

    def read(self):
        if self.released:
            return False, None
        self.reads += 1
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:, :] = FakeCamera.colours.get(self.index, (128, 128, 128))
        return True, frame

    def release(self):
        self.released = True


def install_fake_camera(monkeypatch):
    import cv2
    FakeCamera.instances = []
    FakeCamera.unavailable = set()
    monkeypatch.setattr(cv2, "VideoCapture", FakeCamera)
    return FakeCamera
