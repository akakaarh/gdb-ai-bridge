"""Debug Loop — AI-driven GDB debugging with serial monitoring."""

from .loop import DebugLoop
from .gdb_client import GDBClient
from .serial_monitor import SerialMonitor
from .ssh_config import SSHConfig
from .ssh_gdb_client import SSHGDBClient
from .ssh_serial_monitor import SSHSerialMonitor
from .actions import translate_action, validate_action
from .safety import SafetyChecker
from .evaluator import Evaluator


def create_debug_loop(goal, expected=None, transport="local",
                      serial_port=None, baudrate=115200,
                      gdb_host="localhost", gdb_port=9999,
                      ssh_config=None, remote_serial=None,
                      gdb_command="gdb", remote_elf=""):
    """Create a DebugLoop with the specified transport.

    Args:
        goal: description of what we're trying to achieve
        expected: dict defining success criteria
        transport: "local" or "ssh"
        serial_port: local serial port (for local transport)
        baudrate: serial baudrate (default 115200)
        gdb_host: GDB server host (local transport, default localhost)
        gdb_port: GDB server port (local transport, default 9999)
        ssh_config: SSHConfig instance (required for ssh transport)
        remote_serial: remote serial device path (required for ssh transport)
        gdb_command: remote gdb command name (ssh transport, default "gdb")
        remote_elf: remote ELF file path (ssh transport)

    Returns:
        DebugLoop instance with configured transport
    """
    if expected is None:
        expected = {}

    if transport == "local":
        serial = SerialMonitor(serial_port, baudrate)
        gdb = GDBClient(gdb_host, gdb_port)
    elif transport == "ssh":
        if ssh_config is None:
            raise ValueError("ssh_config is required for ssh transport")
        if remote_serial is None:
            raise ValueError("remote_serial is required for ssh transport")
        serial = SSHSerialMonitor(ssh_config, remote_serial, baudrate)
        gdb = SSHGDBClient(ssh_config, gdb_command, remote_file=remote_elf)
    else:
        raise ValueError(f"Unknown transport: {transport!r}. Use 'local' or 'ssh'.")

    serial.start()
    return DebugLoop(goal, expected, serial, gdb)