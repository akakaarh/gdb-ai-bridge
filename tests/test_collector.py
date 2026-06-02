"""Tests for gdb.collector — layered collection with graceful degradation."""
import os
import pytest
import struct
import tempfile
from unittest.mock import MagicMock, patch, mock_open

from gdb_bridge.collector import Collector, DebugContext
from gdb_bridge.arch.base import ArchAdapter
from gdb_bridge.target.base import TargetAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_arch(**overrides):
    arch = MagicMock(spec=ArchAdapter)
    arch.name = overrides.get("name", "arm-cortex-m")
    arch.get_registers.return_value = overrides.get("registers", {"r0": "0x00000000", "sp": "0x20004000"})
    arch.annotate_registers.side_effect = lambda regs: {k: {"value": v, "role": "general"} for k, v in regs.items()}
    arch.get_fault_registers.return_value = overrides.get("fault_regs", {"cfsr": "0x00000000"})
    arch.decode_crash.return_value = overrides.get("crash", ("none", "no fault"))
    arch.get_exception_frame.return_value = overrides.get("exc_frame", {"r0": "0x00000000", "pc": "0x08001234"})
    return arch


def _make_target(**overrides):
    target = MagicMock(spec=TargetAdapter)
    target.name = overrides.get("name", "openocd")
    target.get_stack_trace.return_value = overrides.get("stack", [
        {"function": "main", "address": "0x08001234", "file": "main.c", "line": 42},
    ])
    target.get_local_variables.return_value = overrides.get("locals", {"x": {"type": "int", "value": "42", "is_param": False}})
    return target


# ---------------------------------------------------------------------------
# Layer 0 — normal collection
# ---------------------------------------------------------------------------

class TestLayer0Normal:
    def test_registers_present(self):
        arch = _make_arch(registers={"r0": "0xDEAD", "sp": "0x20004000"})
        target = _make_target()
        ctx = Collector(arch, target).collect()

        assert ctx.layer0["status"] == "ok"
        assert "registers" in ctx.layer0
        assert ctx.layer0["registers"]["r0"]["value"] == "0xDEAD"

    def test_fault_and_crash_present(self):
        arch = _make_arch(crash=("HardFault", "Instruction bus fault"))
        target = _make_target()
        ctx = Collector(arch, target).collect()

        assert ctx.layer0["crash_type"] == "HardFault"
        assert ctx.layer0["crash_reason"] == "Instruction bus fault"
        assert "fault_registers" in ctx.layer0

    def test_config_populated(self):
        arch = _make_arch()
        arch.name = "riscv32"
        target = _make_target()
        target.name = "jlink"
        ctx = Collector(arch, target, config={"elf_file": "fw.elf"}).collect()

        assert ctx.config["arch"] == "riscv32"
        assert ctx.config["target"] == "jlink"
        assert ctx.config["elf_file"] == "fw.elf"

    def test_timestamp_is_iso(self):
        arch = _make_arch()
        target = _make_target()
        ctx = Collector(arch, target).collect()

        # Should be parseable ISO format
        from datetime import datetime
        datetime.fromisoformat(ctx.timestamp)


# ---------------------------------------------------------------------------
# Layer 1 — normal collection
# ---------------------------------------------------------------------------

class TestLayer1Normal:
    def test_exception_frame_present(self):
        arch = _make_arch(exc_frame={"pc": "0x08001234", "lr": "0x08001000"})
        target = _make_target()
        ctx = Collector(arch, target).collect()

        assert ctx.layer1["status"] == "ok"
        assert ctx.layer1["exception_frame"]["pc"] == "0x08001234"

    def test_stack_trace_present(self):
        arch = _make_arch()
        target = _make_target(stack=[
            {"function": "main", "address": "0x08001234"},
            {"function": "foo", "address": "0x08001000"},
        ])
        ctx = Collector(arch, target).collect()

        assert len(ctx.layer1["stack_trace"]) == 2
        assert ctx.layer1["stack_trace"][0]["function"] == "main"

    def test_local_variables_present(self):
        arch = _make_arch()
        target = _make_target(locals={"cnt": {"type": "int", "value": "10", "is_param": False}})
        ctx = Collector(arch, target).collect()

        assert ctx.layer1["local_variables"]["cnt"]["value"] == "10"


# ---------------------------------------------------------------------------
# Graceful degradation — Layer 0 failures
# ---------------------------------------------------------------------------

class TestLayer0Degradation:
    def test_registers_fail_fault_ok(self):
        arch = _make_arch()
        arch.get_registers.side_effect = RuntimeError("target not halted")
        target = _make_target()
        ctx = Collector(arch, target).collect()

        assert ctx.layer0["registers"]["status"] == "error"
        assert "target not halted" in ctx.layer0["registers"]["error"]
        # fault should still work
        assert ctx.layer0["crash_type"] == "none"

    def test_fault_fail_registers_ok(self):
        arch = _make_arch(registers={"r0": "0xDEAD", "sp": "0x20004000"})
        arch.get_fault_registers.side_effect = RuntimeError("access denied")
        target = _make_target()
        ctx = Collector(arch, target).collect()

        assert ctx.layer0["fault_registers"]["status"] == "error"
        assert "access denied" in ctx.layer0["fault_registers"]["error"]
        # registers should still work
        assert ctx.layer0["status"] == "ok"
        assert "registers" in ctx.layer0
        assert ctx.layer0["registers"]["r0"]["value"] == "0xDEAD"

    def test_both_fail(self):
        arch = _make_arch()
        arch.get_registers.side_effect = RuntimeError("usb disconnected")
        arch.get_fault_registers.side_effect = RuntimeError("usb disconnected")
        target = _make_target()
        ctx = Collector(arch, target).collect()

        assert ctx.layer0["registers"]["status"] == "error"
        assert ctx.layer0["fault_registers"]["status"] == "error"


# ---------------------------------------------------------------------------
# Graceful degradation — Layer 1 failures
# ---------------------------------------------------------------------------

class TestLayer1Degradation:
    def test_exception_frame_fail_rest_ok(self):
        arch = _make_arch()
        arch.get_exception_frame.side_effect = RuntimeError("no exception")
        target = _make_target()
        ctx = Collector(arch, target).collect()

        assert ctx.layer1["exception_frame"]["status"] == "error"
        assert ctx.layer1["stack_trace"][0]["function"] == "main"
        assert ctx.layer1["local_variables"]["x"]["value"] == "42"

    def test_stack_trace_fail_rest_ok(self):
        arch = _make_arch()
        target = _make_target()
        target.get_stack_trace.side_effect = RuntimeError("unwind failed")
        ctx = Collector(arch, target).collect()

        assert ctx.layer1["stack_trace"]["status"] == "error"
        assert ctx.layer1["exception_frame"]["pc"] == "0x08001234"
        assert ctx.layer1["local_variables"]["x"]["value"] == "42"

    def test_locals_fail_rest_ok(self):
        arch = _make_arch()
        target = _make_target()
        target.get_local_variables.side_effect = RuntimeError("no dwarf info")
        ctx = Collector(arch, target).collect()

        assert ctx.layer1["local_variables"]["status"] == "error"
        assert ctx.layer1["stack_trace"][0]["function"] == "main"

    def test_all_layer1_fail(self):
        arch = _make_arch()
        arch.get_exception_frame.side_effect = RuntimeError("e1")
        target = _make_target()
        target.get_stack_trace.side_effect = RuntimeError("e2")
        target.get_local_variables.side_effect = RuntimeError("e3")
        ctx = Collector(arch, target).collect()

        assert ctx.layer1["exception_frame"]["status"] == "error"
        assert ctx.layer1["stack_trace"]["status"] == "error"
        assert ctx.layer1["local_variables"]["status"] == "error"


# ---------------------------------------------------------------------------
# Layer 2 — _get_sp
# ---------------------------------------------------------------------------

class TestGetSp:
    def test_reads_sp_from_arch(self):
        arch = _make_arch(registers={"r0": "0x00000000", "sp": "0x20004000"})
        target = _make_target()
        c = Collector(arch, target)

        assert c._get_sp() == 0x20004000

    def test_sp_not_in_registers(self):
        arch = _make_arch(registers={"r0": "0x00000000"})
        target = _make_target()
        c = Collector(arch, target)

        assert c._get_sp() == 0x0

    def test_sp_is_int(self):
        arch = MagicMock(spec=ArchAdapter)
        arch.get_registers.return_value = {"sp": 0x20008000}
        target = _make_target()
        c = Collector(arch, target)

        assert c._get_sp() == 0x20008000

    def test_arch_raises(self):
        arch = _make_arch()
        arch.get_registers.side_effect = RuntimeError("halted")
        target = _make_target()
        c = Collector(arch, target)

        assert c._get_sp() is None


# ---------------------------------------------------------------------------
# Layer 2 — _get_stack_top (3-level fallback)
# ---------------------------------------------------------------------------

class TestGetStackTop:
    def test_estack_symbol_fallback1(self):
        """Level 1: _estack symbol found and > SP."""
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.parse_and_eval.return_value = 0x20010000
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            result = c._get_stack_top(0x20004000)

        assert result == 0x20010000

    def test_estack_below_sp_fallback2(self):
        """Level 1 fails (_estack < SP), Level 2: SRAM region end."""
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)
        c.safe_regions = [(0x20000000, 0x20010000)]

        mock_gdb = MagicMock()
        mock_gdb.parse_and_eval.return_value = 0x20002000  # below SP
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            result = c._get_stack_top(0x20004000)

        assert result == 0x20010000

    def test_no_estack_no_regions_fallback3(self):
        """Level 1-2 fail, Level 3: default SP + 8KB."""
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.parse_and_eval.side_effect = RuntimeError("no symbol")
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            result = c._get_stack_top(0x20004000)

        assert result == 0x20004000 + 8192

    def test_no_gdb_no_regions_fallback3(self):
        """GDB import fails, no regions → default."""
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)

        # No gdb in sys.modules → import gdb fails
        with patch.dict("sys.modules", {"gdb": None}):
            result = c._get_stack_top(0x20004000)

        assert result == 0x20004000 + 8192

    def test_sp_not_in_any_region_fallback3(self):
        """SP not in any safe region → default."""
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)
        c.safe_regions = [(0x10000000, 0x10060000)]  # SP not in this range

        mock_gdb = MagicMock()
        mock_gdb.parse_and_eval.side_effect = RuntimeError("no symbol")
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            result = c._get_stack_top(0x20004000)

        assert result == 0x20004000 + 8192


# ---------------------------------------------------------------------------
# Layer 2 — _get_data_segments
# ---------------------------------------------------------------------------

class TestGetDataSegments:
    def test_parses_data_and_bss(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)

        gdb_output = (
            "Local exec file:\n"
            "	`fw.elf', file type elf32-littlearm.\n"
            "	0x08000000 - 0x08020000 is .text\n"
            "	0x20000000 - 0x20001000 is .data\n"
            "	0x20001000 - 0x20002000 is .bss\n"
        )
        mock_gdb = MagicMock()
        mock_gdb.execute.return_value = gdb_output
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            segs = c._get_data_segments()

        assert len(segs) == 2
        assert segs[0]["name"] == ".data"
        assert segs[0]["vaddr"] == 0x20000000
        assert segs[0]["size"] == 0x1000
        assert segs[1]["name"] == ".bss"
        assert segs[1]["vaddr"] == 0x20001000
        assert segs[1]["size"] == 0x1000

    def test_no_data_bss_segments(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)

        gdb_output = "	0x08000000 - 0x08020000 is .text\n"
        mock_gdb = MagicMock()
        mock_gdb.execute.return_value = gdb_output
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            segs = c._get_data_segments()

        assert segs == []

    def test_gdb_not_available(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)

        with patch.dict("sys.modules", {"gdb": None}):
            segs = c._get_data_segments()

        assert segs == []


# ---------------------------------------------------------------------------
# Layer 2 — _read_memory_chunked
# ---------------------------------------------------------------------------

class TestReadMemoryChunked:
    def test_full_read(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)
        c.read_memory_safe = MagicMock(return_value=b"\xAB\xCD\xEF\x12")

        result = c._read_memory_chunked(0x20000000, 4, chunk_size=4096)

        assert result == b"\xAB\xCD\xEF\x12"
        c.read_memory_safe.assert_called_once_with(0x20000000, 4)

    def test_multi_chunk(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)
        c.read_memory_safe = MagicMock(return_value=b"\x01" * 4)

        result = c._read_memory_chunked(0x20000000, 8, chunk_size=4)

        assert len(result) == 8
        assert result == b"\x01" * 8
        assert c.read_memory_safe.call_count == 2

    def test_failed_chunk_zero_filled(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)
        # First chunk succeeds, second fails
        c.read_memory_safe = MagicMock(side_effect=[b"\xAA\xBB", None])

        result = c._read_memory_chunked(0x20000000, 4, chunk_size=2)

        assert result == b"\xAA\xBB\x00\x00"

    def test_all_chunks_fail(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)
        c.read_memory_safe = MagicMock(return_value=None)

        result = c._read_memory_chunked(0x20000000, 4096, chunk_size=4096)

        assert result == b"\x00" * 4096

    def test_partial_last_chunk(self):
        arch = _make_arch()
        target = _make_target()
        c = Collector(arch, target)
        c.read_memory_safe = MagicMock(return_value=b"\xFF")

        result = c._read_memory_chunked(0x20000000, 1, chunk_size=4096)

        assert result == b"\xFF"


# ---------------------------------------------------------------------------
# Layer 2 — _collect_layer2 (core dump generation)
# ---------------------------------------------------------------------------

class TestCollectLayer2:
    def test_normal_dump_stack_only(self, tmp_path):
        """Default mode: uses gcore to dump."""
        arch = _make_arch(registers={"r0": "0xDEAD", "sp": "0x20004000"})
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.execute = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            out = str(tmp_path / "test.core")
            result = c._collect_layer2(out)

        assert result["status"] == "ok"
        assert result["file"] == out
        mock_gdb.execute.assert_called_once_with(f"gcore {out}")

    def test_normal_dump_with_data_segments(self, tmp_path):
        """gcore dumps everything — data segments param unused."""
        arch = _make_arch(registers={"r0": "0xDEAD", "sp": "0x20004000"})
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.execute = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            out = str(tmp_path / "test.core")
            result = c._collect_layer2(out)

        assert result["status"] == "ok"

    def test_dump_all_mode(self, tmp_path):
        """dump_all: gcore handles it."""
        arch = _make_arch(registers={"r0": "0x0", "sp": "0x20004000"})
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.execute = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            out = str(tmp_path / "all.core")
            result = c._collect_layer2(out, dump_all=True)

        assert result["status"] == "ok"

    def test_dump_all_exceeds_max_size(self, tmp_path):
        """dump_all + max_size: gcore ignores size limits (GDB handles it)."""
        arch = _make_arch()
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.execute = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            out = str(tmp_path / "all.core")
            result = c._collect_layer2(out, dump_all=True, max_size=512 * 1024)

        assert result["status"] == "ok"

    def test_dump_all_within_max_size(self, tmp_path):
        """dump_all within max_size: gcore success."""
        arch = _make_arch(registers={"r0": "0x0", "sp": "0x20004000"})
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.execute = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            out = str(tmp_path / "ok.core")
            result = c._collect_layer2(out, dump_all=True, max_size=1024 * 1024)

        assert result["status"] == "ok"

    def test_sp_is_none_no_stack_region(self, tmp_path):
        """gcore doesn't need SP — always succeeds."""
        arch = _make_arch()
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        mock_gdb.execute = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            out = str(tmp_path / "nosp.core")
            result = c._collect_layer2(out)

        assert result["status"] == "ok"

    def test_gcore_called(self, tmp_path):
        """Verify gcore is called with correct path."""
        arch = _make_arch()
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target)

        mock_gdb = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            out = str(tmp_path / "gcore.core")
            c._collect_layer2(out)

        mock_gdb.execute.assert_called_once_with(f"gcore {out}")


# ---------------------------------------------------------------------------
# Layer 2 — full_dump toggle (collect integration)
# ---------------------------------------------------------------------------

class TestLayer2Integration:
    def test_no_full_dump(self):
        arch = _make_arch()
        target = _make_target()
        ctx = Collector(arch, target).collect(full_dump=False)

        assert ctx.layer2 == {}

    def test_full_dump_triggers_layer2(self, tmp_path):
        arch = _make_arch(registers={"r0": "0x0", "sp": "0x20004000"})
        arch.name = "arm"
        target = _make_target()
        c = Collector(arch, target, config={"dump_path": str(tmp_path / "auto.core")})

        mock_gdb = MagicMock()
        mock_gdb.execute = MagicMock()
        with patch.dict("sys.modules", {"gdb": mock_gdb}):
            ctx = c.collect(full_dump=True)

        assert ctx.layer2["status"] == "ok"


# ---------------------------------------------------------------------------
# DebugContext basics
# ---------------------------------------------------------------------------

class TestDebugContext:
    def test_to_dict_keys(self):
        ctx = DebugContext()
        d = ctx.to_dict()
        expected = {"version", "timestamp", "config", "layer0", "layer1", "layer2", "errors", "decoded_registers", "tasks"}
        assert set(d.keys()) == expected

    def test_version_default(self):
        ctx = DebugContext()
        assert ctx.version == "1.0"

    def test_errors_list(self):
        ctx = DebugContext()
        assert ctx.errors == []
        ctx.errors.append("test error")
        assert ctx.to_dict()["errors"] == ["test error"]
