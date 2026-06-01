"""SSH configuration — generates ssh command prefix for remote connections."""

import dataclasses
import os
import pathlib
import shutil


@dataclasses.dataclass
class SSHConfig:
    """Configuration for SSH connections to remote hosts.

    Generates ssh command prefixes compatible with subprocess.run().
    Supports ControlMaster connection reuse and all standard ssh options.
    """

    host: str
    user: str = ""
    port: int = 22
    key_file: str = ""
    options: dict = dataclasses.field(default_factory=dict)
    connect_timeout: int = 10
    control_master: bool = True
    control_path: str = ""

    def __post_init__(self):
        if not self.host:
            raise ValueError("SSHConfig.host must not be empty")
        if not self.control_path and self.control_master:
            user_part = self.user or os.getenv("USER", "default")
            self.control_path = (
                f"{pathlib.Path.home()}/.ssh/cm-{self.host}-{self.port}-{user_part}"
            )

    def ssh_prefix(self) -> list[str]:
        """Generate ssh command prefix for subprocess.run()."""
        cmd = ["ssh"]

        # Connection timeout
        cmd.extend(["-o", f"ConnectTimeout={self.connect_timeout}"])

        # ControlMaster
        if self.control_master:
            cmd.extend([
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={self.control_path}",
                "-o", "ControlPersist=60",
            ])

        # Additional options
        for key, value in self.options.items():
            cmd.extend(["-o", f"{key}={value}"])

        # Identity file
        if self.key_file:
            cmd.extend(["-i", self.key_file])

        # Port (skip if default 22)
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])

        # User
        if self.user:
            cmd.extend(["-l", self.user])

        # Disable stdin to prevent ssh from consuming input
        cmd.extend(["-T"])

        # Target host
        cmd.append(self.host)

        return cmd

    @staticmethod
    def check_ssh_available():
        """Check if ssh command is available on this system."""
        if shutil.which("ssh") is None:
            raise EnvironmentError(
                "ssh command not found. Please install OpenSSH or set up SSH access."
            )