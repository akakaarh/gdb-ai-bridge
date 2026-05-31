"""Tests for SerialMonitor with mock serial."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch
import collections


class TestSerialMonitor:
    def _make_monitor(self):
        """Create a SerialMonitor with mocked serial module."""
        with patch.dict(sys.modules, {"serial": MagicMock()}):
            from debug_loop.serial_monitor import SerialMonitor
            monitor = SerialMonitor(port="COM3", baudrate=115200)
            return monitor

    def test_init(self):
        monitor = self._make_monitor()
        assert monitor.port == "COM3"
        assert monitor.baudrate == 115200
        assert isinstance(monitor.buffer, collections.deque)

    def test_buffer_maxlen(self):
        monitor = self._make_monitor()
        assert monitor.buffer.maxlen == 1000

    def test_read_new_lines_empty(self):
        monitor = self._make_monitor()
        assert monitor.read_new_lines() == ""

    def test_read_new_lines_returns_buffered(self):
        monitor = self._make_monitor()
        monitor.buffer.append("line1")
        monitor.buffer.append("line2")
        result = monitor.read_new_lines()
        assert "line1" in result
        assert "line2" in result

    def test_read_new_lines_clears_buffer(self):
        monitor = self._make_monitor()
        monitor.buffer.append("line1")
        monitor.read_new_lines()
        assert len(monitor.buffer) == 0

    def test_read_output_empty(self):
        monitor = self._make_monitor()
        result = monitor.read_output(timeout=0.1)
        assert result == ""

    def test_ring_buffer_drops_old(self):
        monitor = self._make_monitor()
        monitor.buffer = collections.deque(maxlen=3)
        monitor.buffer.append("a")
        monitor.buffer.append("b")
        monitor.buffer.append("c")
        monitor.buffer.append("d")  # "a" dropped
        assert list(monitor.buffer) == ["b", "c", "d"]

    def test_start_stop(self):
        with patch.dict(sys.modules, {"serial": MagicMock()}):
            from debug_loop.serial_monitor import SerialMonitor
            monitor = SerialMonitor(port="COM3")
            monitor.start()
            assert monitor._running is True
            assert monitor._ser is not None
            monitor.stop()
            assert monitor._running is False
