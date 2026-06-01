"""Tests for SSHConfig."""
import os
import sys
import pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from debug_loop.ssh_config import SSHConfig


class TestSSHConfig:
    def test_basic_config(self):
        cfg = SSHConfig(host="192.168.1.100")
        cmd = cfg.ssh_prefix()
        assert cmd[0] == "ssh"
        assert "-T" in cmd
        assert "192.168.1.100" in cmd

    def test_with_user(self):
        cfg = SSHConfig(host="myhost", user="dev")
        cmd = cfg.ssh_prefix()
        assert "-l" in cmd
        idx = cmd.index("-l")
        assert cmd[idx + 1] == "dev"

    def test_with_port(self):
        cfg = SSHConfig(host="myhost", port=2222)
        cmd = cfg.ssh_prefix()
        assert "-p" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "2222"

    def test_default_port_no_p_flag(self):
        cfg = SSHConfig(host="myhost", port=22)
        cmd = cfg.ssh_prefix()
        assert "-p" not in cmd

    def test_with_key_file(self):
        cfg = SSHConfig(host="myhost", key_file="/home/user/.ssh/id_rsa")
        cmd = cfg.ssh_prefix()
        assert "-i" in cmd
        idx = cmd.index("-i")
        assert cmd[idx + 1] == "/home/user/.ssh/id_rsa"

    def test_with_options(self):
        cfg = SSHConfig(host="myhost", options={"ProxyJump": "jump_host"})
        cmd = cfg.ssh_prefix()
        assert "ProxyJump=jump_host" in cmd

    def test_control_master_enabled(self):
        cfg = SSHConfig(host="myhost", control_master=True)
        cmd = cfg.ssh_prefix()
        assert "ControlMaster=auto" in cmd
        assert "ControlPersist=60" in cmd

    def test_control_master_disabled(self):
        cfg = SSHConfig(host="myhost", control_master=False)
        cmd = cfg.ssh_prefix()
        assert "ControlMaster=auto" not in cmd

    def test_control_path_auto_generated(self):
        cfg = SSHConfig(host="myhost")
        assert "cm-myhost-22" in cfg.control_path

    def test_connect_timeout(self):
        cfg = SSHConfig(host="myhost", connect_timeout=5)
        cmd = cfg.ssh_prefix()
        assert "ConnectTimeout=5" in cmd

    def test_empty_host_raises(self):
        try:
            SSHConfig(host="")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "must not be empty" in str(e)

    def test_all_features_combined(self):
        cfg = SSHConfig(
            host="10.0.0.1",
            user="root",
            port=2222,
            key_file="/root/.ssh/authorized_keys",
            options={"StrictHostKeyChecking": "no"},
            connect_timeout=3,
        )
        cmd = cfg.ssh_prefix()
        assert "10.0.0.1" in cmd
        assert "-l" in cmd
        assert "-p" in cmd
        assert "-i" in cmd
        assert "StrictHostKeyChecking=no" in cmd
        assert "ConnectTimeout=3" in cmd
        assert "-T" in cmd