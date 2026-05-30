"""Tests for the ARM Cortex-M architecture adapter.

Uses unittest.mock to simulate the gdb module so tests can run
outside of a real GDB session.
"""

import struct
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Bootstrap: inject a fake ``gdb`` module into sys.modules *before* importing
# the adapter so that ``import gdb`` inside arm.py picks up the mock.
# ---------------------------------------------------------------------------

_mock_gdb = types.ModuleType("gdb")
sys.modules["gdb"] = _mock_gdb

# We need to control parse_and_eval per-test, so we keep a reference to the
# mock and reconfigure it as needed.
_parse_and_eval = MagicMock(return_value=0)
_mock_gdb.parse_and_eval = _parse_and_eval

# Mock selected_frame / read_memory for fault register reads
_mock_frame = MagicMock()
_mock_gdb.selected_frame = MagicMock(return_value=_mock_frame)

# Force re-import so the module-level ``import gdb`` picks up our mock.
if "gdb_bridge.arch.arm" in sys.modules:
    del sys.modules["gdb_bridge.arch.arm"]

from gdb_bridge.arch.arm import ArmAdapter  # noqa: E402

# Ensure the adapter module uses OUR mock, not a stale reference.
import gdb_bridge.arch.arm as _arm_mod
_arm_mod._gdb = _mock_gdb


# -- helpers ---------------------------------------------------------------

def _make_memory_le(val: int) -> bytes:
    """Return 4 little-endian bytes for *val*."""
    return struct.pack("<I", val)


def _setup_parse_and_eval(mapping: dict[str, int]):
    """Configure mock to return values from *mapping* for register names."""
    def _side_effect(expr: str):
        # expr is like "$r0", "$xpsr", etc.
        reg = expr.lstrip("$")
        if reg in mapping:
            result = MagicMock()
            result.__int__ = lambda s, v=mapping[reg]: v
            return result
        raise ValueError(f"unexpected register: {expr}")
    _parse_and_eval.side_effect = _side_effect


def _setup_read_memory(mapping: dict[int, int]):
    """Configure mock frame.read_memory to return bytes from *mapping*."""
    def _side_effect(addr: int, size: int = 4):
        if addr in mapping:
            return _make_memory_le(mapping[addr])
        raise ValueError(f"unexpected memory read at 0x{addr:x}")
    _mock_frame.read_memory.side_effect = _side_effect


# -- tests -----------------------------------------------------------------

class TestArmRegisters(unittest.TestCase):
    """Register reading tests."""

    def setUp(self):
        self.adapter = ArmAdapter()

    def test_get_registers_returns_expected_keys(self):
        regs = {f"r{i}": i * 0x10 for i in range(16)}
        regs["xpsr"] = 0x61000000
        _setup_parse_and_eval(regs)
        result = self.adapter.get_registers()
        self.assertIn("r0", result)
        self.assertIn("r15", result)
        self.assertIn("xpsr", result)
        self.assertEqual(len(result), 17)

    def test_get_registers_returns_empty_without_gdb(self):
        # Temporarily remove gdb from the adapter's module
        import gdb_bridge.arch.arm as arm_mod
        saved = arm_mod._gdb
        arm_mod._gdb = None
        try:
            result = self.adapter.get_registers()
            self.assertEqual(result, {})
        finally:
            arm_mod._gdb = saved

    def test_register_values_are_hex_strings(self):
        _setup_parse_and_eval({"r0": 0xDEAD_BEEF, "xpsr": 0x41000000, "r1": 0x0})
        # Only these three will resolve; others will raise and be skipped.
        result = self.adapter.get_registers()
        self.assertEqual(result["r0"], "0xdeadbeef")
        self.assertEqual(result["xpsr"], "0x41000000")


class TestArmFaultRegisters(unittest.TestCase):
    """SCB fault register reading."""

    def setUp(self):
        self.adapter = ArmAdapter()

    def test_get_fault_registers_reads_scb(self):
        scb_data = {
            0xE000ED00: 0x410FC241,  # CPUID (Cortex-M4 r0p1)
            0xE000ED28: 0x00000082,  # CFSR: DACCVIOL | MMARVALID
            0xE000ED2C: 0x00000000,  # HFSR
            0xE000ED34: 0x20001000,  # MMFAR
            0xE000ED38: 0x00000000,  # BFAR
        }
        _setup_read_memory(scb_data)
        result = self.adapter.get_fault_registers()
        self.assertIn("cfsr", result)
        self.assertIn("hfsr", result)
        self.assertIn("mmfar", result)
        self.assertIn("bfar", result)
        self.assertIn("cpuid", result)
        self.assertEqual(result["cfsr"], "0x00000082")
        self.assertEqual(result["mmfar"], "0x20001000")


class TestArmExceptionFrame(unittest.TestCase):
    """Exception frame extraction."""

    def setUp(self):
        self.adapter = ArmAdapter()

    def test_get_exception_frame_from_sp(self):
        _setup_parse_and_eval({"sp": 0x20004000})
        frame_words = {
            0x20004000 + i * 4: 0xAA00_0000 + i
            for i in range(8)
        }
        _setup_read_memory(frame_words)
        result = self.adapter.get_exception_frame()
        self.assertIn("r0", result)
        self.assertIn("r3", result)
        self.assertIn("r12", result)
        self.assertIn("lr", result)
        self.assertIn("pc", result)
        self.assertIn("xpsr", result)
        self.assertEqual(len(result), 8)

    def test_get_exception_frame_returns_empty_without_gdb(self):
        import gdb_bridge.arch.arm as arm_mod
        saved = arm_mod._gdb
        arm_mod._gdb = None
        try:
            result = self.adapter.get_exception_frame()
            self.assertEqual(result, {})
        finally:
            arm_mod._gdb = saved


class TestArmCrashDecode(unittest.TestCase):
    """CFSR/HFSR crash decoding."""

    def setUp(self):
        self.adapter = ArmAdapter()

    # -- individual CFSR bit tests ----------------------------------------

    def test_iaccviol(self):
        fault = {"cfsr": "0x00000001", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "MemManage")
        self.assertIn("IACCVIOL", reason)

    def test_daccviol(self):
        fault = {"cfsr": "0x00000002", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "MemManage")
        self.assertIn("DACCVIOL", reason)

    def test_daccviol_with_mmfar(self):
        fault = {
            "cfsr": "0x00000082",  # DACCVIOL | MMARVALID
            "hfsr": "0x00000000",
            "mmfar": "0x20001000",
        }
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "MemManage")
        self.assertIn("DACCVIOL", reason)
        self.assertIn("MMFAR=0x20001000", reason)

    def test_ibuserr(self):
        fault = {"cfsr": "0x00000100", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "BusFault")
        self.assertIn("IBUSERR", reason)

    def test_preciserr_with_bfar(self):
        fault = {
            "cfsr": "0x00008200",  # PRECISERR | BFARVALID
            "hfsr": "0x00000000",
            "bfar": "0x08001234",
        }
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "BusFault")
        self.assertIn("PRECISERR", reason)
        self.assertIn("BFAR=0x08001234", reason)

    def test_impreciserr(self):
        fault = {"cfsr": "0x00000400", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "BusFault")
        self.assertIn("IMPRECISERR", reason)

    def test_undefinstr(self):
        fault = {"cfsr": "0x00010000", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "UsageFault")
        self.assertIn("UNDEFINSTR", reason)

    def test_invstate(self):
        fault = {"cfsr": "0x00020000", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "UsageFault")
        self.assertIn("INVSTATE", reason)

    def test_divbyzero(self):
        fault = {"cfsr": "0x02000000", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "UsageFault")
        self.assertIn("DIVBYZERO", reason)

    def test_unaligned(self):
        fault = {"cfsr": "0x01000000", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "UsageFault")
        self.assertIn("UNALIGNED", reason)

    # -- HFSR tests -------------------------------------------------------

    def test_hfsr_debug_evt(self):
        fault = {"cfsr": "0x00000000", "hfsr": "0x40000000"}  # bit 30
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "DebugEvt")
        self.assertIn("Debug event", reason)

    def test_hfsr_forced(self):
        fault = {"cfsr": "0x00000000", "hfsr": "0x00000002"}  # bit 1
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "HardFault")
        self.assertIn("escalated", reason)

    # -- edge cases -------------------------------------------------------

    def test_empty_fault_regs(self):
        crash_type, reason = self.adapter.decode_crash({})
        self.assertEqual(crash_type, "unknown")

    def test_zero_cfsr(self):
        fault = {"cfsr": "0x00000000", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        self.assertEqual(crash_type, "HardFault")
        self.assertIn("no CFSR bits", reason)

    def test_multiple_faults_returns_lowest_priority(self):
        # DACCVIOL + IBUSERR set simultaneously
        fault = {"cfsr": "0x00000102", "hfsr": "0x00000000"}
        crash_type, reason = self.adapter.decode_crash(fault)
        # DACCVIOL (bit 1) is lower than IBUSERR (bit 8), so MemManage wins
        self.assertEqual(crash_type, "MemManage")
        self.assertIn("DACCVIOL", reason)
        self.assertIn("IBUSERR", reason)


class TestArmAnnotateRegisters(unittest.TestCase):
    """Register role annotation."""

    def setUp(self):
        self.adapter = ArmAdapter()

    def test_argument_registers(self):
        regs = {"r0": "0x1", "r1": "0x2", "r2": "0x3", "r3": "0x4"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["r0"]["role"], "arg0/retval")
        self.assertEqual(result["r1"]["role"], "arg1")
        self.assertEqual(result["r2"]["role"], "arg2")
        self.assertEqual(result["r3"]["role"], "arg3")

    def test_special_registers(self):
        regs = {"r13": "0x20004000", "r14": "0x08001234", "r15": "0x08005678"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["r13"]["role"], "SP")
        self.assertEqual(result["r14"]["role"], "LR")
        self.assertEqual(result["r15"]["role"], "PC")

    def test_fp_registers(self):
        regs = {"r7": "0x1000", "r11": "0x2000"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["r7"]["role"], "FP (Thumb)")
        self.assertEqual(result["r11"]["role"], "FP (ARM)")

    def test_value_preserved(self):
        regs = {"r0": "0xdeadbeef"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["r0"]["value"], "0xdeadbeef")

    def test_xpsr_annotation(self):
        regs = {"xpsr": "0x41000000"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["xpsr"]["role"], "Program Status Register")

    def test_unknown_register_no_role(self):
        regs = {"r99": "0x0"}
        result = self.adapter.annotate_registers(regs)
        self.assertIn("value", result["r99"])
        self.assertNotIn("role", result["r99"])

    def test_full_register_set_roles(self):
        """Annotate a full register set and verify every role is present."""
        regs = {f"r{i}": f"0x{i:08x}" for i in range(16)}
        regs["xpsr"] = "0x61000000"
        result = self.adapter.annotate_registers(regs)
        # Spot-check a few
        self.assertEqual(result["r4"]["role"], "callee-saved")
        self.assertEqual(result["r9"]["role"], "callee-saved (platform)")
        self.assertEqual(result["r12"]["role"], "IP (Intra-Procedure-call scratch)")


if __name__ == "__main__":
    unittest.main()
