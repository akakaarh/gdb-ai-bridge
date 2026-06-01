"""SSH serial monitor — reads remote serial port output over SSH."""

import collections
import subprocess
import threading
import time

from .ssh_config import SSHConfig


class SSHSerialMonitor:
    """Monitor a remote serial port over SSH.

    Uses a persistent SSH subprocess running cat on the remote serial device.
    Lines are collected in a background thread into a ring buffer, matching
    the SerialMonitor duck-type interface.
    """

    def __init__(self, ssh_config: SSHConfig, port: str, baudrate: int = 115200,
                 buffer_size: int = 1000):
        self.ssh_config = ssh_config
        self.port = port
        self.baudrate = baudrate
        self._buffer = collections.deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._process: subprocess.Popen | None = None

        SSHConfig.check_ssh_available()

    def start(self):
        """Start monitoring the remote serial port in a background thread.

        Opens a persistent SSH connection running:
            stty -F <port> <baudrate> raw -echo && cat <port>

        The output is read line-by-line in a background thread.
        """
        # Configure serial device on remote host
        setup_cmd = f"stty -F {self.port} {self.baudrate} raw -echo 2>/dev/null; cat {self.port}"
        ssh_prefix = self.ssh_config.ssh_prefix()
        full_cmd = ssh_prefix + ["bash", "-c", setup_cmd]

        self._process = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop monitoring and clean up."""
        self._running = False
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def read_new_lines(self) -> str:
        """Return any new lines accumulated since last call (non-blocking)."""
        with self._lock:
            lines = list(self._buffer)
            self._buffer.clear()
        return "\n".join(lines)

    def read_output(self, timeout: float = 3) -> str:
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

    def write(self, data):
        """Send data to the remote serial port via SSH.

        Uses a separate SSH command since the monitor holds the read side.
        """
        if isinstance(data, str):
            data = data.encode("utf-8")

        escaped = data.decode("utf-8", errors="replace")
        escaped = escaped.replace("'", "'\\''")
        cmd = f"printf '%s' '{escaped}' > {self.port}"
        ssh_prefix = self.ssh_config.ssh_prefix()
        full_cmd = ssh_prefix + ["bash", "-c", cmd]

        try:
            subprocess.run(
                full_cmd,
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    def _read_loop(self):
        """Background thread: read output from the SSH cat process line by line."""
        if not self._process or not self._process.stdout:
            return

        raw = b""
        while self._running:
            try:
                byte = self._process.stdout.read(1)
                if not byte:
                    # Process ended
                    break
                raw += byte
                if byte in (b"\n", b"\r"):
                    text = raw.decode("utf-8", errors="replace").strip("\r\n\t ")
                    if text:
                        with self._lock:
                            self._buffer.append(text)
                    raw = b""
            except Exception:
                break