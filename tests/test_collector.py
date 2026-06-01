"""Tests for gdb.collector — layered collection with graceful degradation."""
import pytest
from unittest.mock import MagicMock

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
# Layer 2 — full_dump toggle
# ---------------------------------------------------------------------------

class TestLayer2:
    def test_no_full_dump(self):
        arch = _make_arch()
        target = _make_target()
        ctx = Collector(arch, target).collect(full_dump=False)

        assert ctx.layer2 == {}

    def test_full_dump_triggers_layer2(self):
        arch = _make_arch()
        target = _make_target()
        ctx = Collector(arch, target).collect(full_dump=True)

        assert ctx.layer2["status"] == "not_implemented"


# ---------------------------------------------------------------------------
# DebugContext basics
# ---------------------------------------------------------------------------

class TestDebugContext:
    def test_to_dict_keys(self):
        ctx = DebugContext()
        d = ctx.to_dict()
        expected = {"version", "timestamp", "config", "layer0", "layer1", "layer2", "errors", "decoded_registers"}
        assert set(d.keys()) == expected

    def test_version_default(self):
        ctx = DebugContext()
        assert ctx.version == "1.0"

    def test_errors_list(self):
        ctx = DebugContext()
        assert ctx.errors == []
        ctx.errors.append("test error")
        assert ctx.to_dict()["errors"] == ["test error"]
