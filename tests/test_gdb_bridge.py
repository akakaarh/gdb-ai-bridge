"""Tests for gdb_bridge/gdb_bridge.py — GDB command parsing and auto mode.

These tests mock the gdb module so they can run outside a real GDB session.
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: inject a fake gdb module so gdb_bridge.gdb_bridge can import
# ---------------------------------------------------------------------------

_mock_gdb = types.ModuleType("gdb")
_mock_gdb.Command = type("Command", (), {"__init__": lambda self, *a, **kw: None})
_mock_gdb.COMMAND_USER = 0
_mock_gdb.write = MagicMock()
_mock_gdb.execute = MagicMock()
_mock_gdb.selected_frame = MagicMock()
_mock_gdb.parse_and_eval = MagicMock()
_mock_gdb.events = MagicMock()
_mock_gdb.VERSION = "14.0-test"
sys.modules["gdb"] = _mock_gdb

# Force re-import so it picks up our mock
if "gdb_bridge.gdb_bridge" in sys.modules:
    del sys.modules["gdb_bridge.gdb_bridge"]

import gdb_bridge.gdb_bridge as mod

# Ensure the module uses OUR mock
mod._gdb = _mock_gdb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before each test."""
    mod._gdb = _mock_gdb
    mod._config["arch"] = "arm"
    mod._config["target"] = "baremetal"
    mod._config["elf_file"] = ""
    mod._config["svd_file"] = ""
    mod._auto_mode["enabled"] = False
    mod._auto_mode["output_dir"] = "."
    mod._auto_mode["filter"] = "crash"
    mod._auto_mode["count"] = 0
    mod._auto_mode["coredump"] = False
    _mock_gdb.write.reset_mock()
    yield
    _mock_gdb.write.reset_mock()


@pytest.fixture
def mock_collector():
    """Patch _get_adapter and Collector so no real GDB is needed."""
    mock_arch = MagicMock()
    mock_arch.name = "arm"
    mock_target = MagicMock()
    mock_target.name = "baremetal"

    with patch.object(mod, "_get_adapter", return_value=(mock_arch, mock_target)):
        with patch("gdb_bridge.gdb_bridge.Collector") as MockCollector:
            instance = MockCollector.return_value
            instance.collect.return_value = MagicMock(to_dict=lambda: {
                "config": {"arch": "arm", "target": "baremetal"},
                "layer0": {}, "layer1": {},
            })
            instance._collect_layer2.return_value = {
                "status": "ok", "file": "test.core", "regions": 1,
            }
            instance.load_safe_regions_from_elf = MagicMock()
            yield instance


# ---------------------------------------------------------------------------
# AICoredumpCommand
# ---------------------------------------------------------------------------

class TestAICoredumpCommand:
    """Tests for 'ai coredump' command."""

    def test_no_args_shows_usage(self, mock_collector):
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke("", False)

        writes = [str(c) for c in _mock_gdb.write.call_args_list]
        assert any("Usage" in w for w in writes)

    def test_basic_coredump(self, mock_collector, tmp_path):
        outfile = str(tmp_path / "crash.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(outfile, False)

        mock_collector._collect_layer2.assert_called_once_with(
            outfile, dump_all=False, max_size=64 * 1024 * 1024,
        )
        writes = [str(c) for c in _mock_gdb.write.call_args_list]
        assert any("Saved" in w for w in writes)

    def test_all_flag(self, mock_collector, tmp_path):
        outfile = str(tmp_path / "all.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(f"{outfile} --all", False)

        mock_collector._collect_layer2.assert_called_once_with(
            outfile, dump_all=True, max_size=64 * 1024 * 1024,
        )

    def test_max_size_flag(self, mock_collector, tmp_path):
        outfile = str(tmp_path / "limited.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(f"{outfile} --all --max-size 1048576", False)

        mock_collector._collect_layer2.assert_called_once_with(
            outfile, dump_all=True, max_size=1048576,
        )

    def test_invalid_max_size(self, mock_collector, tmp_path):
        outfile = str(tmp_path / "bad.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(f"{outfile} --max-size notanumber", False)

        writes = [str(c) for c in _mock_gdb.write.call_args_list]
        assert any("Invalid max-size" in w for w in writes)
        mock_collector._collect_layer2.assert_not_called()

    def test_max_size_over_64mb_with_all_rejected(self, mock_collector, tmp_path):
        outfile = str(tmp_path / "toobig.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(f"{outfile} --all --max-size 134217728", False)  # 128MB

        writes = [str(c) for c in _mock_gdb.write.call_args_list]
        assert any("cannot exceed 64MB" in w for w in writes)
        mock_collector._collect_layer2.assert_not_called()

    def test_no_arch_configured(self, mock_collector):
        mod._config["arch"] = None
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke("test.core", False)

        writes = [str(c) for c in _mock_gdb.write.call_args_list]
        assert any("Error" in w and "config" in w.lower() for w in writes)

    def test_loads_safe_regions_from_elf(self, mock_collector, tmp_path):
        mod._config["elf_file"] = "firmware.elf"
        outfile = str(tmp_path / "test.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(outfile, False)

        mock_collector.load_safe_regions_from_elf.assert_called_once_with("firmware.elf")

    def test_collector_error_reported(self, mock_collector, tmp_path):
        mock_collector._collect_layer2.return_value = {
            "status": "error", "reason": "Total size 100000000 exceeds max 67108864",
        }
        outfile = str(tmp_path / "fail.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(outfile, False)

        writes = [str(c) for c in _mock_gdb.write.call_args_list]
        assert any("Error" in w for w in writes)

    def test_default_max_size_64mb(self, mock_collector, tmp_path):
        """Default max_size should be 64MB when not specified."""
        outfile = str(tmp_path / "default.core")
        cmd = mod.AICoredumpCommand.__new__(mod.AICoredumpCommand)
        cmd.invoke(outfile, False)

        _, kwargs = mock_collector._collect_layer2.call_args
        assert kwargs["max_size"] == 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# AIAutoCommand — coredump flag
# ---------------------------------------------------------------------------

class TestAIAutoCoredump:
    """Tests for 'ai auto --coredump' flag."""

    def test_coredump_flag_parsed(self):
        cmd = mod.AIAutoCommand.__new__(mod.AIAutoCommand)
        cmd.invoke("on --coredump", False)

        assert mod._auto_mode["coredump"] is True

    def test_coredump_flag_with_dir(self):
        cmd = mod.AIAutoCommand.__new__(mod.AIAutoCommand)
        cmd.invoke("on --dir /tmp/logs --coredump", False)

        assert mod._auto_mode["coredump"] is True
        assert mod._auto_mode["output_dir"] == "/tmp/logs"

    def test_coredump_shown_in_status(self):
        mod._auto_mode["coredump"] = True
        cmd = mod.AIAutoCommand.__new__(mod.AIAutoCommand)
        cmd.invoke("status", False)

        writes = [str(c) for c in _mock_gdb.write.call_args_list]
        assert any("coredump" in w.lower() and "ON" in w for w in writes)

    def test_coredump_off_by_default(self):
        cmd = mod.AIAutoCommand.__new__(mod.AIAutoCommand)
        cmd.invoke("status", False)

        assert mod._auto_mode["coredump"] is False


# ---------------------------------------------------------------------------
# _auto_stop_handler — coredump integration
# ---------------------------------------------------------------------------

class TestAutoStopHandlerCoredump:
    """Tests for auto-stop handler with coredump enabled."""

    def _setup_handler_test(self, tmp_path, coredump_enabled=True):
        """Common setup for handler tests."""
        mod._auto_mode["enabled"] = True
        mod._auto_mode["output_dir"] = str(tmp_path)
        mod._auto_mode["filter"] = "all"
        mod._auto_mode["count"] = 0
        mod._auto_mode["coredump"] = coredump_enabled

        mock_arch = MagicMock()
        mock_arch.name = "arm"
        mock_target = MagicMock()

        mock_ctx = MagicMock()
        mock_ctx.to_dict.return_value = {
            "config": {"arch": "arm", "target": "baremetal"},
            "layer0": {}, "layer1": {},
        }

        return mock_arch, mock_target, mock_ctx

    def test_coredump_generated_on_crash(self, tmp_path):
        """When --coredump is on, a .core file should be generated."""
        mock_arch, mock_target, mock_ctx = self._setup_handler_test(tmp_path)

        with patch.object(mod, "_get_adapter", return_value=(mock_arch, mock_target)):
            with patch("gdb_bridge.gdb_bridge.Collector") as MockCollector:
                instance = MockCollector.return_value
                instance.collect.return_value = mock_ctx
                instance._collect_layer2.return_value = {
                    "status": "ok", "file": "test.core", "regions": 1,
                }
                instance.load_safe_regions_from_elf = MagicMock()

                event = MagicMock()
                event.breakpoints = None
                event.stop_signal = "SIGSEGV"
                mod._auto_stop_handler(event)

        # Check .core file was generated
        instance._collect_layer2.assert_called_once()
        core_path = instance._collect_layer2.call_args[0][0]
        assert core_path.endswith(".core")
        assert "auto_0001_" in core_path

    def test_coredump_not_generated_when_disabled(self, tmp_path):
        """When --coredump is off, no .core file should be generated."""
        mock_arch, mock_target, mock_ctx = self._setup_handler_test(
            tmp_path, coredump_enabled=False,
        )

        with patch.object(mod, "_get_adapter", return_value=(mock_arch, mock_target)):
            with patch("gdb_bridge.gdb_bridge.Collector") as MockCollector:
                instance = MockCollector.return_value
                instance.collect.return_value = mock_ctx
                instance._collect_layer2.return_value = {
                    "status": "ok", "file": "test.core", "regions": 1,
                }

                event = MagicMock()
                event.breakpoints = None
                event.stop_signal = "SIGSEGV"
                mod._auto_stop_handler(event)

        instance._collect_layer2.assert_not_called()

    def test_json_file_uses_timestamp_format(self, tmp_path):
        """JSON filename should follow auto_{count}_{timestamp}.json format."""
        mock_arch, mock_target, mock_ctx = self._setup_handler_test(
            tmp_path, coredump_enabled=False,
        )

        with patch.object(mod, "_get_adapter", return_value=(mock_arch, mock_target)):
            with patch("gdb_bridge.gdb_bridge.Collector") as MockCollector:
                instance = MockCollector.return_value
                instance.collect.return_value = mock_ctx

                event = MagicMock()
                event.breakpoints = None
                event.stop_signal = "SIGSEGV"
                mod._auto_stop_handler(event)

        # Check that JSON files exist with timestamp format
        files = os.listdir(str(tmp_path))
        json_files = [f for f in files if f.endswith(".json")]
        assert len(json_files) == 1
        assert json_files[0].startswith("auto_0001_")
        assert json_files[0].endswith(".json")

    def test_core_file_matches_json_timestamp(self, tmp_path):
        """Core dump filename timestamp should match JSON filename timestamp."""
        mock_arch, mock_target, mock_ctx = self._setup_handler_test(
            tmp_path, coredump_enabled=True,
        )

        with patch.object(mod, "_get_adapter", return_value=(mock_arch, mock_target)):
            with patch("gdb_bridge.gdb_bridge.Collector") as MockCollector:
                instance = MockCollector.return_value
                instance.collect.return_value = mock_ctx
                instance._collect_layer2.return_value = {
                    "status": "ok", "file": "test.core", "regions": 1,
                }
                instance.load_safe_regions_from_elf = MagicMock()

                event = MagicMock()
                event.breakpoints = None
                event.stop_signal = "SIGSEGV"
                mod._auto_stop_handler(event)

        core_path = instance._collect_layer2.call_args[0][0]
        core_name = os.path.basename(core_path)
        # Extract timestamp from core filename: auto_0001_YYYYMMDD_HHMMSS.core
        core_ts = core_name.replace("auto_0001_", "").replace(".core", "")

        files = os.listdir(str(tmp_path))
        json_files = [f for f in files if f.endswith(".json")]
        json_ts = json_files[0].replace("auto_0001_", "").replace(".json", "")

        assert core_ts == json_ts

    def test_output_directory_created(self, tmp_path):
        """Auto mode should create the output directory if it doesn't exist."""
        out_dir = str(tmp_path / "subdir" / "logs")
        mod._auto_mode["enabled"] = True
        mod._auto_mode["output_dir"] = out_dir
        mod._auto_mode["filter"] = "all"
        mod._auto_mode["count"] = 0
        mod._auto_mode["coredump"] = False

        mock_arch = MagicMock()
        mock_target = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.to_dict.return_value = {
            "config": {"arch": "arm", "target": "baremetal"},
            "layer0": {}, "layer1": {},
        }

        with patch.object(mod, "_get_adapter", return_value=(mock_arch, mock_target)):
            with patch("gdb_bridge.gdb_bridge.Collector") as MockCollector:
                instance = MockCollector.return_value
                instance.collect.return_value = mock_ctx

                event = MagicMock()
                event.breakpoints = None
                event.stop_signal = "SIGSEGV"
                mod._auto_stop_handler(event)

        assert os.path.isdir(out_dir)
