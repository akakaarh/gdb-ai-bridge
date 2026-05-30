"""GDB-AI Bridge — GDB Python extension entry point.

Usage in GDB:
    source gdb_bridge/gdb_bridge.py
    ai config arch arm target baremetal
    ai collect
    ai dump crash.json
    ai info
"""

from __future__ import annotations

import json
import sys
import os

# Add parent dir to path so we can import our modules
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

try:
    import gdb as _gdb
except ImportError:
    _gdb = None

from gdb_bridge.collector import Collector, DebugContext
from gdb_bridge.output import save_context, print_context


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_config = {
    "arch": None,
    "target": None,
    "elf_file": "",
}


def _get_adapter(arch_name, target_name):
    """Instantiate the correct adapters based on config."""
    from gdb_bridge.arch.arm import ArmAdapter
    from gdb_bridge.arch.arm64 import Arm64Adapter
    from gdb_bridge.target.baremetal import BaremetalAdapter
    from gdb_bridge.target.linux import LinuxAdapter

    arch_map = {
        "arm": ArmAdapter,
        "arm64": Arm64Adapter,
    }
    target_map = {
        "baremetal": BaremetalAdapter,
        "linux": LinuxAdapter,
    }

    arch_cls = arch_map.get(arch_name)
    target_cls = target_map.get(target_name)

    if arch_cls is None:
        raise ValueError(f"Unknown arch: {arch_name}. Available: {list(arch_map.keys())}")
    if target_cls is None:
        raise ValueError(f"Unknown target: {target_name}. Available: {list(target_map.keys())}")

    return arch_cls(), target_cls()


# ---------------------------------------------------------------------------
# GDB Commands
# ---------------------------------------------------------------------------

class AIPrefixCommand(_gdb.Command if _gdb else object):
    """GDB-AI Bridge prefix command."""

    def __init__(self):
        if _gdb:
            super().__init__("ai", _gdb.COMMAND_USER, prefix=True)

    def invoke(self, arg, from_tty):
        if _gdb:
            _gdb.write("GDB-AI Bridge. Use 'ai info', 'ai collect', 'ai config', 'ai dump'.\n")


class AICollectCommand(_gdb.Command if _gdb else object):
    """Collect debug context. Usage: ai collect [--full]"""

    def __init__(self):
        if _gdb:
            super().__init__("ai collect", _gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        if not _config["arch"] or not _config["target"]:
            _gdb.write("Error: Run 'ai config arch <arch> target <target>' first.\n")
            return

        full_dump = "--full" in arg

        try:
            arch, target = _get_adapter(_config["arch"], _config["target"])
        except ValueError as e:
            _gdb.write(f"Error: {e}\n")
            return

        collector = Collector(arch, target, config=_config)
        ctx = collector.collect(full_dump=full_dump)
        print_context(ctx)


class AIDumpCommand(_gdb.Command if _gdb else object):
    """Collect and save to file. Usage: ai dump <filepath> [--full]"""

    def __init__(self):
        if _gdb:
            super().__init__("ai dump", _gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        if not _config["arch"] or not _config["target"]:
            _gdb.write("Error: Run 'ai config arch <arch> target <target>' first.\n")
            return

        args = arg.split()
        if not args:
            _gdb.write("Error: Usage: ai dump <filepath> [--full]\n")
            return

        filepath = args[0]
        full_dump = "--full" in args

        try:
            arch, target = _get_adapter(_config["arch"], _config["target"])
        except ValueError as e:
            _gdb.write(f"Error: {e}\n")
            return

        collector = Collector(arch, target, config=_config)
        ctx = collector.collect(full_dump=full_dump)
        save_context(ctx, filepath)
        _gdb.write(f"Context saved to {filepath}\n")


class AIConfigCommand(_gdb.Command if _gdb else object):
    """Configure the bridge. Usage: ai config arch <arch> target <target>"""

    def __init__(self):
        if _gdb:
            super().__init__("ai config", _gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        parts = arg.split()
        i = 0
        while i < len(parts):
            if parts[i] == "arch" and i + 1 < len(parts):
                _config["arch"] = parts[i + 1]
                i += 2
            elif parts[i] == "target" and i + 1 < len(parts):
                _config["target"] = parts[i + 1]
                i += 2
            else:
                _gdb.write(f"Unknown config: {parts[i]}\n")
                i += 1

        _gdb.write(f"Config: arch={_config['arch']}, target={_config['target']}\n")


class AIInfoCommand(_gdb.Command if _gdb else object):
    """Show current configuration. Usage: ai info"""

    def __init__(self):
        if _gdb:
            super().__init__("ai info", _gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        _gdb.write("=== GDB-AI Bridge ===\n")
        _gdb.write(f"  Arch:   {_config['arch'] or '(not set)'}\n")
        _gdb.write(f"  Target: {_config['target'] or '(not set)'}\n")
        _gdb.write(f"  Python: {sys.version.split()[0]}\n")
        _gdb.write(f"  GDB:    {getattr(_gdb, 'VERSION', 'unknown')}\n")
        _gdb.write(f"  Auto:   {'ON' if _auto_mode['enabled'] else 'off'}\n")
        _gdb.write("====================\n")


# ---------------------------------------------------------------------------
# Auto mode — automatic collection on crash
# ---------------------------------------------------------------------------

_auto_mode = {
    "enabled": False,
    "output_dir": ".",
    "filter": "crash",  # "crash" or "all"
    "count": 0,
}


def _is_crash_stop(event):
    """Determine if a stop event is a crash (not user breakpoint/step)."""
    # If breakpoints triggered, it's a user stop
    if getattr(event, "breakpoints", None):
        return False

    stop_signal = getattr(event, "stop_signal", None)

    # Explicit crash signals
    if stop_signal in ("SIGSEGV", "SIGABRT", "SIGBUS", "SIGFPE", "SIGILL"):
        return True

    # SIGTRAP could be HardFault on Cortex-M — check CFSR
    if stop_signal == "SIGTRAP" or stop_signal is None:
        try:
            # Try reading HFSR (HardFault Status Register)
            frame = _gdb.selected_frame()
            hfsr = int(frame.read_memory(0xE000ED2C, 4).cast(_gdb.lookup_type("uint32_t")))
            if hfsr & 0xFFFFFFFF:  # Any bit set = fault occurred
                return True
        except Exception:
            pass

    return False


def _auto_stop_handler(event):
    """GDB stop event handler for auto-collect mode."""
    if not _auto_mode["enabled"]:
        return

    should_collect = _auto_mode["filter"] == "all"
    if _auto_mode["filter"] == "crash":
        should_collect = _is_crash_stop(event)

    if not should_collect:
        return

    if not _config["arch"] or not _config["target"]:
        return

    try:
        arch, target = _get_adapter(_config["arch"], _config["target"])
        collector = Collector(arch, target, config=_config)
        ctx = collector.collect()

        _auto_mode["count"] += 1
        filename = f"auto_{_auto_mode['count']:04d}.json"
        filepath = os.path.join(_auto_mode["output_dir"], filename)
        save_context(ctx, filepath)
        _gdb.write(f"[AI Auto] Crash detected. Context saved to {filepath}\n")
    except Exception as e:
        _gdb.write(f"[AI Auto] Error during collection: {e}\n")


class AIAutoCommand(_gdb.Command if _gdb else object):
    """Auto-collect on crash. Usage: ai auto on|off|status [--dir <path>] [--filter crash|all]"""

    def __init__(self):
        if _gdb:
            super().__init__("ai auto", _gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        parts = arg.split()
        if not parts:
            _gdb.write("Usage: ai auto on|off|status [--dir <path>] [--filter crash|all]\n")
            return

        action = parts[0]

        # Parse options
        i = 1
        while i < len(parts):
            if parts[i] == "--dir" and i + 1 < len(parts):
                _auto_mode["output_dir"] = parts[i + 1]
                i += 2
            elif parts[i] == "--filter" and i + 1 < len(parts):
                _auto_mode["filter"] = parts[i + 1]
                i += 2
            else:
                i += 1

        if action == "on":
            _auto_mode["enabled"] = True
            _auto_mode["count"] = 0
            try:
                _gdb.events.stop.connect(_auto_stop_handler)
            except Exception:
                pass  # Already connected
            _gdb.write(f"[AI Auto] Enabled. filter={_auto_mode['filter']}, "
                        f"dir={_auto_mode['output_dir']}\n")

        elif action == "off":
            _auto_mode["enabled"] = False
            try:
                _gdb.events.stop.disconnect(_auto_stop_handler)
            except Exception:
                pass  # Not connected
            _gdb.write("[AI Auto] Disabled.\n")

        elif action == "status":
            _gdb.write(f"[AI Auto] {'ON' if _auto_mode['enabled'] else 'off'}\n")
            _gdb.write(f"  filter: {_auto_mode['filter']}\n")
            _gdb.write(f"  dir:    {_auto_mode['output_dir']}\n")
            _gdb.write(f"  count:  {_auto_mode['count']}\n")

        else:
            _gdb.write(f"Unknown action: {action}. Use on|off|status.\n")


# ---------------------------------------------------------------------------
# Register commands
# ---------------------------------------------------------------------------

if _gdb:
    AIPrefixCommand()
    AICollectCommand()
    AIDumpCommand()
    AIConfigCommand()
    AIInfoCommand()
    AIAutoCommand()
    _gdb.write("GDB-AI Bridge loaded. Use 'ai info' to get started.\n")
