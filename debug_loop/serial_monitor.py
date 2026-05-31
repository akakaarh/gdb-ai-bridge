"""Serial port monitor for reading UART output from the target board."""

import threading
import collections
import time


class SerialMonitor:
    def __init__(self, port, baudrate=115200, buffer_size=1000):
        self.port = port
        self.baudrate = baudrate
        self._buffer = collections.deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._ser = None

    def start(self):
        """Start monitoring the serial port in a background thread."""
        import serial
        self._ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._ser and self._ser.is_open:
            self._ser.close()

    def read_output(self, timeout=3):
        """Read all available lines, waiting up to timeout seconds for new data."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._buffer:
                    break
            time.sleep(0.1)
        with self._lock:
            lines = list(self._buffer)
            self._buffer.clear()
        return "\n".join(lines)

    def read_new_lines(self):
        """Return any new lines accumulated since last call (non-blocking)."""
        with self._lock:
            lines = list(self._buffer)
            self._buffer.clear()
        return "\n".join(lines)

    def write(self, data):
        """Send data to the serial port."""
        if self._ser and self._ser.is_open:
            if isinstance(data, str):
                data = data.encode("utf-8")
            self._ser.write(data)

    def _read_loop(self):
        """Background thread: read bytes one at a time, split on newline."""
        raw = b""
        while self._running:
            try:
                byte = self._ser.read(1)
                if byte:
                    raw += byte
                    if byte == b"\n":
                        text = raw.decode("utf-8", errors="replace").strip("\r\n\t ")
                        if text:
                            with self._lock:
                                self._buffer.append(text)
                        raw = b""
            except Exception:
                pass
