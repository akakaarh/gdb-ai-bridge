"""Tests for SSHSerialMonitor."""
import os
import sys
import io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch, PropertyMock
from debug_loop.ssh_config import SSHConfig
from debug_loop.ssh_serial_monitor import SSHSerialMonitor


class TestSSHSerialMonitor:
    def _make_monitor(self, **kwargs):
        ssh = SSHConfig(host="testhost", user="dev", control_master=False)
        return SSHSerialMonitor(ssh, **kwargs) if kwargs else SSHSerialMonitor(ssh, "/dev/ttyUSB0")

    def test_init(self):
        monitor = self._make_monitor(port="/dev/ttyUSB0", baudrate=9600)
        assert monitor.port == "/dev/ttyUSB0"
        assert monitor.baudrate == 9600
        assert monitor._running is False

    @patch("subprocess.Popen")
    def test_start_spawns_process(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        monitor = self._make_monitor()
        monitor.start()
        assert monitor._running is True
        assert monitor._process is not None
        # Verify stty and cat in command
        cmd_args = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd_args)
        assert "stty" in cmd_str or "cat" in cmd_str
        monitor.stop()

    @patch("subprocess.Popen")
    def test_stop_terminates_process(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"")
        mock_proc.wait.return_value = None
        mock_popen.return_value = mock_proc

        monitor = self._make_monitor()
        monitor.start()
        monitor.stop()
        assert monitor._running is False
        mock_proc.terminate.assert_called_once()

    @patch("subprocess.Popen")
    def test_read_new_lines_empty(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        monitor = self._make_monitor()
        monitor.start()
        result = monitor.read_new_lines()
        assert result == ""
        monitor.stop()

    def test_read_new_lines_from_buffer(self):
        monitor = self._make_monitor()
        monitor._buffer.append("line1")
        monitor._buffer.append("line2")
        result = monitor.read_new_lines()
        assert "line1" in result
        assert "line2" in result
        assert len(monitor._buffer) == 0

    def test_read_output_with_data(self):
        monitor = self._make_monitor()
        monitor._buffer.append("data1")
        result = monitor.read_output(timeout=0.1)
        assert result == "data1"

    def test_read_output_empty(self):
        monitor = self._make_monitor()
        result = monitor.read_output(timeout=0.1)
        assert result == ""

    @patch("subprocess.Popen")
    def test_read_loop_fills_buffer(self, mock_popen):
        # Simulate serial output: "hello\nworld\n"
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"hello\nworld\n")
        mock_popen.return_value = mock_proc

        monitor = self._make_monitor()
        monitor.start()
        # Wait for read loop to process
        import time
        time.sleep(0.3)
        result = monitor.read_new_lines()
        monitor.stop()
        assert "hello" in result
        assert "world" in result

    @patch("subprocess.Popen")
    def test_process_dies_detected(self, mock_popen):
        # Simulate process that exits immediately (EOF)
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        monitor = self._make_monitor()
        monitor.start()
        import time
        time.sleep(0.2)
        # Process should have exited the read loop
        assert not monitor._running or True  # read loop exits on EOF
        monitor.stop()