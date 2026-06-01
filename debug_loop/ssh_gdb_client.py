"""SSH GDB client — executes GDB commands on a remote host via SSH."""

import shlex
import subprocess
import tempfile
import os

from .ssh_config import SSHConfig


class SSHGDBClient:
    """GDB client that executes commands on a remote host via SSH.

    Phase 1: per-command mode — each execute() call spawns a new SSH connection.
    Uses ControlMaster for connection reuse across calls.
    Phase 2 (future): persistent SSH session with stdin/stdout multiplexing.
    """

    def __init__(self, ssh_config: SSHConfig, gdb_command: str = "gdb",
                 gdb_args: list[str] | None = None, remote_file: str = "",
                 timeout: int = 30):
        self.ssh_config = ssh_config
        self.gdb_command = gdb_command
        self.gdb_args = gdb_args or []
        self.remote_file = remote_file
        self.timeout = timeout
        self.last_error: str | None = None
        self._temp_script: str | None = None

        SSHConfig.check_ssh_available()

    def _build_remote_gdb_cmd(self, gdb_commands: list[str]) -> str:
        """Build the remote gdb command string.

        For per-command mode, we create a temporary GDB script file on the
        remote host, then run gdb -batch -x <script> <file>.
        This avoids shell escaping issues with complex GDB commands.
        """
        parts = [self.gdb_command, "-batch"]

        # Add user-provided GDB args
        for arg in self.gdb_args:
            parts.append(shlex.quote(arg))

        # Create remote script with all commands
        if gdb_commands:
            script_content = "\\n".join(gdb_commands)
            # Write script to a temp file and use -x
            # We use a here-document approach: write via printf and run
            parts.extend([
                "-x", f"<(printf '%s' '{script_content}')"
            ])

        # Add remote ELF file
        if self.remote_file:
            parts.append(shlex.quote(self.remote_file))

        return " ".join(parts)

    def _execute_remote(self, remote_cmd: str) -> tuple[str | None, int | None]:
        """Execute a command on the remote host via SSH.

        Returns (stdout, returncode) tuple.
        Uses subprocess.run with shell=False for security.
        """
        self.last_error = None
        ssh_prefix = self.ssh_config.ssh_prefix()
        full_cmd = ssh_prefix + ["bash", "-c", remote_cmd]

        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result.returncode != 0:
                self.last_error = result.stderr.strip() or f"exit code {result.returncode}"
                return None, result.returncode
            return result.stdout.strip(), result.returncode
        except FileNotFoundError:
            self.last_error = "ssh command not found"
            return None, None
        except subprocess.TimeoutExpired:
            self.last_error = f"Command timed out after {self.timeout}s"
            return None, None
        except Exception as e:
            self.last_error = str(e)
            return None, None

    def health(self) -> dict:
        """Check SSH connectivity to remote host."""
        output, code = self._execute_remote("echo ok")
        if output and "ok" in output:
            return {"ok": True}
        return {"ok": False, "error": self.last_error}

    def execute(self, command: str) -> str | None:
        """Execute a single GDB command on the remote host.

        Returns the GDB output string, or None on failure.
        Compatible with GDBClient.execute() interface.
        """
        gdb_cmd = f"{self.gdb_command} -batch -ex {shlex.quote(command)}"
        if self.remote_file:
            gdb_cmd += f" {shlex.quote(self.remote_file)}"

        output, _ = self._execute_remote(gdb_cmd)
        return output

    def get_state(self) -> dict:
        """Get current GDB/target state.

        Executes multiple GDB commands in a single SSH call to minimize latency.
        Returns a dict with registers, backtrace, and signal info.
        Phase 1 returns raw text values; Phase 2 may return structured data.
        """
        gdb_commands = [
            "info registers",
            "backtrace 10",
            "info signal",
        ]
        gdb_script = "\\n".join(f"echo === {cmd.replace(' ', '_')} ===\\n{cmd}" for cmd in gdb_commands)
        gdb_cmd = f"{self.gdb_command} -batch -ex {shlex.quote(gdb_script)}"
        if self.remote_file:
            gdb_cmd += f" {shlex.quote(self.remote_file)}"

        output, _ = self._execute_remote(gdb_cmd)
        state: dict = {"raw_output": output}

        if output:
            # Parse signal info if present
            for line in output.splitlines():
                line_lower = line.lower()
                if "program received signal" in line_lower:
                    for sig in ("SIGSEGV", "SIGABRT", "SIGBUS", "SIGFPE", "SIGILL"):
                        if sig in line:
                            state["signal"] = sig
                            break
        return state

    # Convenience methods — identical interface to GDBClient

    def read_register(self, name: str) -> str | None:
        output = self.execute(f"print ${name}")
        if output:
            return output.strip()
        return None

    def read_all_registers(self) -> str | None:
        return self.execute("info registers")

    def read_variable(self, name: str) -> str | None:
        output = self.execute(f"print {name}")
        if output:
            return output.strip()
        return None

    def read_memory(self, addr: str, count: int = 1) -> str | None:
        return self.execute(f"x/{count}wx {addr}")

    def set_breakpoint(self, location: str) -> str | None:
        return self.execute(f"break {location}")

    def delete_breakpoint(self, number: int) -> str | None:
        return self.execute(f"delete {number}")

    def step(self) -> str | None:
        return self.execute("step")

    def next(self) -> str | None:
        return self.execute("next")

    def continue_exec(self) -> str | None:
        return self.execute("continue")

    def backtrace(self) -> str | None:
        return self.execute("backtrace")

    def info_locals(self) -> str | None:
        return self.execute("info locals")

    def finish(self) -> str | None:
        return self.execute("finish")