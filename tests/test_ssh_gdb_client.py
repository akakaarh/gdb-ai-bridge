"""Tests for SSHGDBClient."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch, call
from debug_loop.ssh_config import SSHConfig
from debug_loop.ssh_gdb_client import SSHGDBClient


class TestSSHGDBClient:
    def _make_client(self, **kwargs):
        ssh = SSHConfig(host="testhost", user="dev", control_master=False)
        return SSHGDBClient(ssh, **kwargs)

    @patch("subprocess.run")
    def test_execute_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="$1 = 42\n", stderr="")
        client = self._make_client()
        result = client.execute("print x")
        assert result is not None
        assert "$1 = 42" in result

    @patch("subprocess.run")
    def test_execute_returns_none_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        client = self._make_client()
        result = client.execute("bad command")
        assert result is None
        assert client.last_error is not None

    @patch("subprocess.run")
    def test_execute_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=30)
        client = self._make_client(timeout=30)
        result = client.execute("info registers")
        assert result is None
        assert "timed out" in client.last_error

    @patch("subprocess.run")
    def test_execute_contains_remote_file(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="bt output", stderr="")
        client = self._make_client(remote_file="/home/dev/fw.elf")
        client.execute("backtrace")
        cmd_args = mock_run.call_args[0][0]
        assert "/home/dev/fw.elf" in " ".join(cmd_args)

    @patch("subprocess.run")
    def test_read_register(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="$r0 = 16\n", stderr="")
        client = self._make_client()
        result = client.read_register("r0")
        assert result == "$r0 = 16"

    @patch("subprocess.run")
    def test_read_all_registers(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="r0 0x00000001\nr1 0x00000002\n", stderr=""
        )
        client = self._make_client()
        result = client.read_all_registers()
        assert result is not None
        assert "r0" in result

    @patch("subprocess.run")
    def test_backtrace(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="#0 main () at main.c:10\n", stderr=""
        )
        client = self._make_client()
        result = client.backtrace()
        assert result is not None
        assert "#0" in result

    @patch("subprocess.run")
    def test_set_breakpoint(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Breakpoint 1 at main.c:10\n", stderr="")
        client = self._make_client()
        result = client.set_breakpoint("main")
        assert result is not None
        # Verify gdb command includes "break main"
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "break main" in cmd_str

    @patch("subprocess.run")
    def test_get_state_returns_dict(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="r0 0x1\nProgram received signal SIGSEGV\n#0 crash\n", stderr=""
        )
        client = self._make_client()
        state = client.get_state()
        assert isinstance(state, dict)
        assert "raw_output" in state
        assert state.get("signal") == "SIGSEGV"

    @patch("subprocess.run")
    def test_get_state_no_crash(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="r0 0x1\n#0 main\n", stderr=""
        )
        client = self._make_client()
        state = client.get_state()
        assert "signal" not in state

    @patch("subprocess.run")
    def test_gdb_command_override(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client = self._make_client(gdb_command="gdb-multiarch")
        client.execute("info registers")
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "gdb-multiarch" in cmd_str

    @patch("subprocess.run")
    def test_step_and_next(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="next line\n", stderr="")
        client = self._make_client()
        client.step()
        client.next()
        assert mock_run.call_count == 2