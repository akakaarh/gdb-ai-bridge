"""Tests for gdb.target — baremetal and linux adapters.

The adapters access the GDB Python API via ``sys.modules["gdb"]`` at call
time (not import time).  We create a fresh mock module with the expected
interface and inject it into ``sys.modules`` for each test.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Import adapters — these work without real gdb since they use _get_gdb()
# at call time, not import time.
# ---------------------------------------------------------------------------

from gdb_bridge.target.baremetal import BaremetalAdapter
from gdb_bridge.target.linux import LinuxAdapter


# ---------------------------------------------------------------------------
# Fixture — inject a FRESH mock gdb module before each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_gdb(monkeypatch):
    """Inject a fresh mock ``gdb`` module into sys.modules for every test."""
    m = MagicMock(name="gdb")
    # gdb.error must be a real exception class so ``except gdb.error`` works.
    m.error = type("gdb_error", (Exception,), {})
    monkeypatch.setitem(sys.modules, "gdb", m)
    return m


# ---------------------------------------------------------------------------
# Helpers — frame / symbol / block factories
# ---------------------------------------------------------------------------

def _make_frame(name="main", pc=0x08001234, filename="main.c", line=42, older=None):
    """Create a mock gdb.Frame."""
    frame = MagicMock(name=f"frame({name})")
    frame.name.return_value = name
    frame.pc.return_value = pc

    sal = MagicMock()
    symtab = MagicMock()
    symtab.filename = filename
    sal.symtab = symtab
    sal.line = line
    frame.find_sal.return_value = sal

    frame.older.return_value = older
    return frame


def _make_symbol(name, value_str="42", type_str="int", is_argument=False):
    """Create a mock gdb.Symbol."""
    sym = MagicMock()
    sym.name = name
    sym.is_variable = not is_argument
    sym.is_argument = is_argument
    sym.type = type_str

    val = MagicMock()
    val.__str__ = lambda self: value_str
    type_mock = MagicMock()
    type_mock.__str__ = lambda self: type_str
    val.type = type_mock
    sym.value.return_value = val
    return sym


# ---------------------------------------------------------------------------
# BaremetalAdapter — stack trace
# ---------------------------------------------------------------------------

class TestBaremetalStackTrace:
    def test_single_frame(self, mock_gdb):
        frame = _make_frame("main", 0x08001234, "main.c", 42)
        mock_gdb.selected_frame.return_value = frame

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 1
        assert result[0]["function"] == "main"
        assert result[0]["address"] == "0x08001234"
        assert result[0]["file"] == "main.c"
        assert result[0]["line"] == 42
        assert result[0]["confidence"] == "high"

    def test_multiple_frames(self, mock_gdb):
        inner = _make_frame("irq_handler", 0x08002000, "irq.c", 10)
        outer = _make_frame("main", 0x08001234, "main.c", 42, older=inner)
        mock_gdb.selected_frame.return_value = outer

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 2
        assert result[0]["function"] == "main"
        assert result[1]["function"] == "irq_handler"

    def test_max_frames_limit(self, mock_gdb):
        # Build a chain of 25 frames — should stop at 20.
        chain = None
        for i in range(25, 0, -1):
            chain = _make_frame(f"func_{i}", 0x08000000 + i, f"f{i}.c", i, older=chain)
        mock_gdb.selected_frame.return_value = chain

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 20

    def test_no_frames_when_no_selected_frame(self, mock_gdb):
        mock_gdb.selected_frame.side_effect = mock_gdb.error("no frame")

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert result == []

    def test_frame_without_name(self, mock_gdb):
        frame = MagicMock()
        frame.name.side_effect = mock_gdb.error("no name")
        frame.pc.return_value = 0x08009999
        sal = MagicMock()
        symtab = MagicMock()
        symtab.filename = ""
        sal.symtab = symtab
        sal.line = 0
        frame.find_sal.return_value = sal
        frame.older.return_value = None
        mock_gdb.selected_frame.return_value = frame

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 1
        assert result[0]["function"] == "<unknown>"
        assert result[0]["confidence"] == "medium"


# ---------------------------------------------------------------------------
# BaremetalAdapter — symbol resolution
# ---------------------------------------------------------------------------

class TestBaremetalSymbolResolution:
    def test_resolves_known_symbol(self, mock_gdb):
        mock_gdb.execute.return_value = "main + 4 in section .text"

        adapter = BaremetalAdapter()
        result = adapter.resolve_symbol("0x08001234")

        assert result == "main + 4 in section .text"
        mock_gdb.execute.assert_called_once_with("info symbol 0x08001234", to_string=True)

    def test_returns_none_for_unknown(self, mock_gdb):
        mock_gdb.execute.return_value = "No symbol matches"

        adapter = BaremetalAdapter()
        result = adapter.resolve_symbol("0xDEADBEEF")

        assert result is None

    def test_returns_none_on_error(self, mock_gdb):
        mock_gdb.execute.side_effect = mock_gdb.error("bad addr")

        adapter = BaremetalAdapter()
        result = adapter.resolve_symbol("invalid")

        assert result is None

    def test_returns_none_for_empty_output(self, mock_gdb):
        mock_gdb.execute.return_value = "  "

        adapter = BaremetalAdapter()
        result = adapter.resolve_symbol("0x0")

        assert result is None


# ---------------------------------------------------------------------------
# BaremetalAdapter — local variables
# ---------------------------------------------------------------------------

class TestBaremetalLocalVariables:
    def test_reads_locals(self, mock_gdb):
        sym_x = _make_symbol("x", "42", "int")
        sym_y = _make_symbol("y", "3.14", "double", is_argument=True)
        block = MagicMock()
        block.__iter__ = MagicMock(return_value=iter([sym_x, sym_y]))

        frame = MagicMock()
        frame.block.return_value = block
        mock_gdb.selected_frame.return_value = frame

        adapter = BaremetalAdapter()
        result = adapter.get_local_variables()

        assert "x" in result
        assert result["x"]["type"] == "int"
        assert result["x"]["value"] == "42"
        assert result["x"]["is_param"] is False

        assert "y" in result
        assert result["y"]["is_param"] is True

    def test_empty_when_no_frame(self, mock_gdb):
        mock_gdb.selected_frame.side_effect = mock_gdb.error("no frame")

        adapter = BaremetalAdapter()
        result = adapter.get_local_variables()

        assert result == {}


# ---------------------------------------------------------------------------
# LinuxAdapter — bt output parsing
# ---------------------------------------------------------------------------

class TestLinuxBtParsing:
    BT_OUTPUT = (
        "#0  0xffffffff81001234 in panic () at kernel/panic.c:280\n"
        "#1  0xffffffff81005678 in die () at arch/x86/kernel/dumpstack.c:340\n"
        "#2  0xffffffff8100abcd in do_trap () at arch/x86/kernel/traps.c:200\n"
    )

    def test_parses_bt_output(self, mock_gdb):
        mock_gdb.execute.return_value = self.BT_OUTPUT

        adapter = LinuxAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 3
        assert result[0]["function"] == "panic"
        assert result[0]["address"] == "0xffffffff81001234"
        assert result[0]["file"] == "kernel/panic.c"
        assert result[0]["line"] == 280
        assert result[0]["confidence"] == "high"

        assert result[1]["function"] == "die"
        assert result[2]["function"] == "do_trap"

    def test_bt_without_file_info(self, mock_gdb):
        bt = "#0  0xffffffff81001234 in start_kernel\n"
        mock_gdb.execute.return_value = bt

        adapter = LinuxAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 1
        assert result[0]["function"] == "start_kernel"
        assert result[0]["file"] == ""
        assert result[0]["line"] == 0

    def test_fallback_to_frame_walk_on_parse_failure(self, mock_gdb):
        # bt returns something unparseable — _parse_bt returns [], falls through
        mock_gdb.execute.return_value = "not a bt output"

        frame = _make_frame("start_kernel", 0xFFFFFFFF81000000, "init/main.c", 1)
        mock_gdb.selected_frame.return_value = frame

        adapter = LinuxAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 1
        assert result[0]["function"] == "start_kernel"

    def test_fallback_on_gdb_error(self, mock_gdb):
        def execute_side_effect(cmd, to_string=False):
            if cmd == "bt":
                raise mock_gdb.error("no stack")
            return ""

        mock_gdb.execute.side_effect = execute_side_effect

        frame = _make_frame("main", 0x08001234, "main.c", 1)
        mock_gdb.selected_frame.return_value = frame

        adapter = LinuxAdapter()
        result = adapter.get_stack_trace()

        assert len(result) == 1
        assert result[0]["function"] == "main"


# ---------------------------------------------------------------------------
# LinuxAdapter — metadata
# ---------------------------------------------------------------------------

class TestLinuxMetadata:
    def test_reads_linux_banner(self, mock_gdb):
        banner_val = MagicMock()
        banner_val.__str__ = lambda self: '"Linux version 6.1.0 (gcc 12.2)"'
        mock_gdb.parse_and_eval.return_value = banner_val

        adapter = LinuxAdapter()
        meta = adapter.get_metadata()

        assert "linux_banner" in meta
        assert "Linux version 6.1.0" in meta["linux_banner"]

    def test_metadata_empty_when_symbol_missing(self, mock_gdb):
        mock_gdb.parse_and_eval.side_effect = mock_gdb.error("no symbol")

        adapter = LinuxAdapter()
        meta = adapter.get_metadata()

        assert meta == {}


# ---------------------------------------------------------------------------
# Confidence annotation
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_high_when_name_present(self, mock_gdb):
        frame = _make_frame("my_func", 0x08001234, "a.c", 1)
        mock_gdb.selected_frame.return_value = frame

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert result[0]["confidence"] == "high"

    def test_medium_when_only_address(self, mock_gdb):
        frame = MagicMock()
        frame.name.side_effect = mock_gdb.error("no name")
        frame.pc.return_value = 0x08009999
        sal = MagicMock()
        symtab = MagicMock()
        symtab.filename = ""
        sal.symtab = symtab
        sal.line = 0
        frame.find_sal.return_value = sal
        frame.older.return_value = None
        mock_gdb.selected_frame.return_value = frame

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert result[0]["confidence"] == "medium"

    def test_low_when_nothing_available(self, mock_gdb):
        frame = MagicMock()
        frame.name.side_effect = mock_gdb.error("no name")
        frame.pc.side_effect = mock_gdb.error("no pc")
        frame.find_sal.side_effect = mock_gdb.error("no sal")
        frame.older.return_value = None
        mock_gdb.selected_frame.return_value = frame

        adapter = BaremetalAdapter()
        result = adapter.get_stack_trace()

        assert result[0]["confidence"] == "low"


# ---------------------------------------------------------------------------
# Adapter name attributes
# ---------------------------------------------------------------------------

class TestAdapterNames:
    def test_baremetal_name(self):
        assert BaremetalAdapter().name == "baremetal"

    def test_linux_name(self):
        assert LinuxAdapter().name == "linux"


# ---------------------------------------------------------------------------
# Base class defaults
# ---------------------------------------------------------------------------

class TestBaseDefaults:
    def test_base_returns_empty(self):
        from gdb_bridge.target.base import TargetAdapter

        base = TargetAdapter()
        assert base.get_stack_trace() == []
        assert base.resolve_symbol("0x0") is None
        assert base.get_local_variables() == {}
        assert base.get_metadata() == {}


# ---------------------------------------------------------------------------
# Graceful degradation — no gdb available
# ---------------------------------------------------------------------------

class TestNoGdbDegradation:
    """Verify that adapter methods raise RuntimeError when gdb is absent."""

    def test_baremetal_raises_without_gdb(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "gdb", raising=False)

        adapter = BaremetalAdapter()
        with pytest.raises(RuntimeError, match="GDB Python API not available"):
            adapter.get_stack_trace()

    def test_linux_raises_without_gdb(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "gdb", raising=False)

        adapter = LinuxAdapter()
        with pytest.raises(RuntimeError, match="GDB Python API not available"):
            adapter.get_stack_trace()
