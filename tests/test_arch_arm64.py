"""Tests for the ARM64 (AArch64) architecture adapter.

Uses unittest.mock to simulate the gdb module so tests can run
outside of a real GDB session.
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Bootstrap: inject a fake ``gdb`` package into sys.modules so that
# ``import gdb`` inside gdb_bridge/arch/arm64.py picks up our mock.
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GDB_DIR = os.path.join(_PROJECT_ROOT, "gdb")

_mock_gdb = types.ModuleType("gdb")
_mock_gdb.__path__ = [_GDB_DIR]
_mock_gdb.__package__ = "gdb"
sys.modules["gdb"] = _mock_gdb  # Force replace

_parse_and_eval = MagicMock(return_value=0)
_mock_gdb.parse_and_eval = _parse_and_eval

# Force re-import of the adapter module so it picks up our mock
if "gdb_bridge.arch.arm64" in sys.modules:
    del sys.modules["gdb_bridge.arch.arm64"]

from gdb_bridge.arch.arm64 import Arm64Adapter  # noqa: E402

# Ensure the adapter module uses OUR mock, not a stale reference from a
# previous test file that may have injected a different gdb mock.
import gdb_bridge.arch.arm64 as _arm64_mod
_arm64_mod._gdb = _mock_gdb


# -- helpers ---------------------------------------------------------------

def _setup_parse_and_eval(mapping: dict[str, int]):
    """Configure mock to return values from *mapping* for register names."""
    def _side_effect(expr: str):
        reg = expr.lstrip("$")
        if reg in mapping:
            result = MagicMock()
            result.__int__ = lambda s, v=mapping[reg]: v
            return result
        raise ValueError(f"unexpected register: {expr}")
    _parse_and_eval.side_effect = _side_effect


# -- tests -----------------------------------------------------------------

class TestArm64Registers(unittest.TestCase):
    """Register reading tests."""

    def setUp(self):
        self.adapter = Arm64Adapter()

    def test_get_registers_returns_x0_to_x30(self):
        regs = {f"x{i}": i * 0x100 for i in range(31)}
        regs.update({"sp": 0x2000_0000, "pc": 0xFFFF_0000, "pstate": 0x60000000})
        _setup_parse_and_eval(regs)
        result = self.adapter.get_registers()
        # Should contain all 31 X registers plus sp, pc, pstate
        for i in range(31):
            self.assertIn(f"x{i}", result, f"x{i} missing from register set")
        self.assertIn("sp", result)
        self.assertIn("pc", result)
        self.assertIn("pstate", result)
        self.assertEqual(len(result), 34)

    def test_register_values_are_hex_strings_16_digits(self):
        _setup_parse_and_eval({"x0": 0xDEAD_BEEF_CAFE_BABE, "sp": 0x0, "pc": 0x0, "pstate": 0x0})
        result = self.adapter.get_registers()
        self.assertEqual(result["x0"], "0xdeadbeefcafebabe")
        self.assertEqual(result["sp"], "0x0000000000000000")

    def test_get_registers_returns_empty_without_gdb(self):
        import gdb_bridge.arch.arm64 as mod
        saved = mod._gdb
        mod._gdb = None
        try:
            result = self.adapter.get_registers()
            self.assertEqual(result, {})
        finally:
            mod._gdb = saved

    def test_partial_register_failure_is_skipped(self):
        """If some registers fail to read, successfully read ones are returned."""
        def _selective(expr: str):
            reg = expr.lstrip("$")
            if reg in ("x0", "x1", "sp", "pc", "pstate"):
                result = MagicMock()
                result.__int__ = lambda s: 0x42
                return result
            raise ValueError("unavailable")
        _parse_and_eval.side_effect = _selective
        result = self.adapter.get_registers()
        self.assertIn("x0", result)
        self.assertIn("x1", result)
        self.assertNotIn("x2", result)
        self.assertEqual(len(result), 5)  # x0, x1, sp, pc, pstate


class TestArm64AnnotateRegisters(unittest.TestCase):
    """Register role annotation tests."""

    def setUp(self):
        self.adapter = Arm64Adapter()

    def test_argument_registers(self):
        regs = {f"x{i}": f"0x{i:016x}" for i in range(8)}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["x0"]["role"], "arg0/retval")
        self.assertEqual(result["x1"]["role"], "arg1")
        self.assertEqual(result["x2"]["role"], "arg2")
        self.assertEqual(result["x3"]["role"], "arg3")
        self.assertEqual(result["x4"]["role"], "arg4")
        self.assertEqual(result["x5"]["role"], "arg5")
        self.assertEqual(result["x6"]["role"], "arg6")
        self.assertEqual(result["x7"]["role"], "arg7")

    def test_frame_pointer_and_link_register(self):
        regs = {"x29": "0x000000007fff1234", "x30": "0x0000000000401000"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["x29"]["role"], "FP (Frame Pointer)")
        self.assertEqual(result["x30"]["role"], "LR (Link Register)")

    def test_special_registers(self):
        regs = {"sp": "0x000000007fff0000", "pc": "0x0000000000402000", "pstate": "0x60000000"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["sp"]["role"], "Stack Pointer")
        self.assertEqual(result["pc"]["role"], "Program Counter")
        self.assertEqual(result["pstate"]["role"], "Processor State")

    def test_callee_saved_registers(self):
        regs = {f"x{i}": "0x0" for i in range(19, 29)}
        result = self.adapter.annotate_registers(regs)
        for i in range(19, 29):
            self.assertEqual(result[f"x{i}"]["role"], "callee-saved",
                             f"x{i} should be callee-saved")

    def test_scratch_registers(self):
        regs = {f"x{i}": "0x0" for i in range(9, 15)}
        result = self.adapter.annotate_registers(regs)
        for i in range(9, 15):
            self.assertEqual(result[f"x{i}"]["role"], "scratch",
                             f"x{i} should be scratch")

    def test_platform_register(self):
        regs = {"x18": "0x0"}
        result = self.adapter.annotate_registers(regs)
        self.assertIn("platform", result["x18"]["role"])

    def test_value_preserved(self):
        regs = {"x0": "0xdeadbeefcafebabe"}
        result = self.adapter.annotate_registers(regs)
        self.assertEqual(result["x0"]["value"], "0xdeadbeefcafebabe")

    def test_unknown_register_no_role(self):
        regs = {"x99": "0x0"}
        result = self.adapter.annotate_registers(regs)
        self.assertIn("value", result["x99"])
        self.assertNotIn("role", result["x99"])

    def test_full_register_set(self):
        """Annotate a full register set and verify structure."""
        regs = {f"x{i}": f"0x{i:016x}" for i in range(31)}
        regs.update({"sp": "0x0", "pc": "0x0", "pstate": "0x0"})
        result = self.adapter.annotate_registers(regs)
        # Every register should have a value
        for name, entry in result.items():
            self.assertIn("value", entry, f"{name} missing value")
            if name in self.adapter._ROLE_MAP:
                self.assertIn("role", entry, f"{name} missing role")


class TestArm64AdapterName(unittest.TestCase):
    def test_name(self):
        self.assertEqual(Arm64Adapter.name, "arm64")


if __name__ == "__main__":
    unittest.main()
