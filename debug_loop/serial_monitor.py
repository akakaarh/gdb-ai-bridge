import threading
import collections


class SerialMonitor:
    def __init__(self, port, baudrate=115200, buffer_size=1000):
        self.port = port
        self.baudrate = baudrate
        self.buffer = collections.deque(maxlen=buffer_size)  # ring buffer
        self._thread = None
        self._running = False
        self._ser = None

    def start(self):
        """开始监听串口"""
        import serial
        self._ser = serial.Serial(self.port, self.baudrate, timeout=1)
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监听"""
        self._running = False
        if self._ser:
            self._ser.close()

    def read_output(self, timeout=3):
        """读取所有缓存的串口输出"""
        import time
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.buffer:
                lines.append(self.buffer.popleft())
            else:
                time.sleep(0.1)
        return "\n".join(lines)

    def read_new_lines(self):
        """读取新增的行（不等待）"""
        lines = []
        while self.buffer:
            lines.append(self.buffer.popleft())
        return "\n".join(lines)

    def _read_loop(self):
        while self._running:
            try:
                line = self._ser.readline()
                if line:
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        self.buffer.append(text)
            except Exception:
                pass
