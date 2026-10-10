"""Client network layer.

Runs a plain TCP connection on a background thread so the Tkinter window never
freezes. Everything the server sends (and connection status changes) is put on
a thread-safe queue; the GUI drains it from its own thread.

Synthetic events created here (they start with an underscore):
    _connected                      connection established
    _disconnected {reason}          lost the connection; will retry
    _connect_failed {reason}        could not connect at all (no retry)

While connected, a small ``ping`` is sent every PING_EVERY seconds; if the server
stays silent for DEAD_AFTER seconds the connection is treated as lost.
"""
import json
import queue
import socket
import threading
import time

from shared import protocol as P

PING_EVERY = 15
DEAD_AFTER = 50
SEND_TIMEOUT = 10


def normalize_address(text: str) -> str:
    """'localhost' -> 'localhost:8765'. Raises ValueError for an invalid address."""
    return P.normalize_address(text)


def describe_error(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return P.format_address_error()
    if isinstance(exc, socket.gaierror):
        return "Server address not found. Check the host name or IP address."
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Connection timed out. Check the address and that the server is running."
    return P.MSG_CONN_FAILED


class NetworkClient:
    def __init__(self, address, connect_timeout=10, max_backoff=15):
        self.address = normalize_address(address)
        self.connect_timeout = connect_timeout
        self.max_backoff = max_backoff
        self.events = queue.Queue()
        self.retry = False                  # becomes True after a successful login
        self._thread = None
        self._sock = None
        self._send_lock = threading.Lock()
        self._stop = False

    # ------------------------------------------------------------------ #
    def start(self):
        self._thread = threading.Thread(target=self._run, name="network", daemon=True)
        self._thread.start()

    def _run(self):
        delay = 1
        while not self._stop:
            reason = P.MSG_CONN_FAILED
            sock = None
            try:
                host, port = P.parse_address(self.address)
                sock = socket.create_connection((host, port), timeout=self.connect_timeout)
                sock.settimeout(SEND_TIMEOUT)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except OSError:
                    pass
                self._sock = sock
                delay = 1
                self.events.put({"type": "_connected"})
                reason = self._read_loop(sock)
            except Exception as exc:                       # DNS, refused, timeout, bad address...
                reason = describe_error(exc)
            finally:
                self._sock = None
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self._stop:
                break
            if not self.retry:
                self.events.put({"type": "_connect_failed", "reason": reason})
                break
            self.events.put({"type": "_disconnected", "reason": reason})
            end = time.monotonic() + delay                 # sleep, but stay stoppable
            while not self._stop and time.monotonic() < end:
                time.sleep(0.1)
            delay = min(delay * 2, self.max_backoff)

    def _read_loop(self, sock):
        """Receive until the connection ends; returns the reason it ended."""
        reader = P.FrameReader(sock)
        last_rx = last_ping = time.monotonic()
        while not self._stop:
            try:
                raw = reader.read(idle=1.0)
            except P.IdleTimeout:
                now = time.monotonic()
                if now - last_rx > DEAD_AFTER:
                    return "No answer from the server."
                if now - last_ping >= PING_EVERY:
                    last_ping = now
                    self.send({"type": "ping"}, quiet=True)
                continue
            except P.FrameTooLarge:
                return "The server sent invalid data."
            except (P.ConnectionClosed, OSError):
                return "Connection closed by the server."
            if raw is None:
                return "Connection closed by the server."
            last_rx = time.monotonic()
            try:
                msg = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "kicked":
                self._stop = True                          # do not reconnect and kick the other session
            self.events.put(msg)
        return "Disconnected."

    # ------------------------------------------------------------------ #
    def send(self, obj: dict, quiet=False) -> bool:
        """Thread-safe. Returns False (and queues an error event) if offline."""
        sock = self._sock
        if sock is None or self._stop:
            if not quiet:
                self.events.put({"type": "error", "message": P.MSG_NETWORK})
            return False
        try:
            data = P.encode_frame(obj)
            with self._send_lock:
                sock.sendall(data)
            return True
        except (OSError, P.FrameTooLarge):
            if not quiet:
                self.events.put({"type": "error", "message": P.MSG_NETWORK})
            return False

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def stop(self):
        self._stop = True
        self.retry = False
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
