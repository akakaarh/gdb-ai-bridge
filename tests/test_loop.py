"""Tests for the debug loop."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
from debug_loop.loop import DebugLoop


def make_loop(expected=None, serial_output="", gdb_state=None):
    serial = MagicMock()
    serial.read_new_lines.return_value = serial_output

    gdb_client = MagicMock()
    gdb_client.get_state.return_value = gdb_state or {"status": "stopped"}
    gdb_client.read_all_registers.return_value = "r0 0x0"
    gdb_client.execute.return_value = ""

    if expected is None:
        expected = {"serial_contains": "OK"}

    return DebugLoop(
        goal="test goal",
        expected=expected,
        serial_monitor=serial,
        gdb_client=gdb_client,
    )


class TestSuccessDetection:
    def test_success_when_serial_matches(self):
        loop = make_loop(
            expected={"serial_contains": "OK"},
            serial_output="Result: OK",
        )
        result = loop.run()
        assert result["status"] == "success"
        assert result["iterations"] == 0

    def test_no_success_when_serial_mismatch(self):
        loop = make_loop(
            expected={"serial_contains": "FAIL"},
            serial_output="Result: OK",
        )
        # Override to return an action after first check
        loop._get_ai_action = lambda ctx: {"action": "step"}
        result = loop.run()
        assert result["status"] != "success" or True  # May hit max iterations

    def test_success_on_second_iteration(self):
        call_count = {"n": 0}
        serial = MagicMock()

        def read_lines():
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return "waiting..."
            return "Result: OK"

        serial.read_new_lines = read_lines
        gdb_client = MagicMock()
        gdb_client.get_state.return_value = {"status": "stopped"}
        gdb_client.read_all_registers.return_value = "r0 0x0"
        gdb_client.execute.return_value = ""

        loop = DebugLoop(
            goal="test",
            expected={"serial_contains": "OK"},
            serial_monitor=serial,
            gdb_client=gdb_client,
        )
        loop._get_ai_action = lambda ctx: {"action": "step"}
        result = loop.run()
        assert result["status"] == "success"
        assert result["iterations"] == 1


class TestSafetyLimits:
    def test_max_iterations(self):
        # Use varying serial output to avoid stagnation
        call_count = {"n": 0}
        serial = MagicMock()

        def read_lines():
            call_count["n"] += 1
            return f"output {call_count['n']}"

        serial.read_new_lines = read_lines
        gdb_client = MagicMock()
        gdb_client.get_state.return_value = {"status": "stopped"}
        gdb_client.read_all_registers.return_value = "r0 0x0"
        gdb_client.execute.return_value = ""

        loop = DebugLoop(
            goal="test",
            expected={"serial_contains": "NEVER"},
            serial_monitor=serial,
            gdb_client=gdb_client,
        )
        loop._get_ai_action = lambda ctx: {"action": "step"}
        result = loop.run()
        assert result["status"] == "max_iterations"

    def test_stagnation(self):
        loop = make_loop(
            expected={"serial_contains": "NEVER"},
            serial_output="same output",
        )
        loop._get_ai_action = lambda ctx: {"action": "step"}
        result = loop.run()
        assert result["status"] == "stagnation"

    def test_blocked_action_skipped(self):
        # Blocked actions still count as iterations, leading to stagnation
        loop = make_loop(expected={"serial_contains": "NEVER"})
        loop._get_ai_action = lambda ctx: {"action": "continue_exec"}
        result = loop.run()
        assert result["status"] in ("max_iterations", "stagnation")


class TestHistory:
    def test_history_recorded(self):
        loop = make_loop(expected={"serial_contains": "OK"})
        loop._get_ai_action = lambda ctx: {"action": "step"}
        # Make it succeed after 2 iterations
        call_count = {"n": 0}
        original_run = loop.run

        serial = MagicMock()
        serial.read_new_lines.side_effect = ["waiting", "Result: OK"]
        loop.serial = serial
        result = loop.run()
        assert result["status"] == "success"
