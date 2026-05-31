"""Tests for debug_loop.evaluator."""

import pytest

from debug_loop.evaluator import Evaluator


class TestSerialMatch:
    def test_serial_contains_match(self):
        ev = Evaluator({"serial_contains": "Temperature: 25"})
        ok, reason = ev.check("Sensor: OK\nTemperature: 25 C", {})
        assert ok is True
        assert reason == "Expected output found"

    def test_serial_contains_no_match(self):
        ev = Evaluator({"serial_contains": "Temperature: 25"})
        ok, reason = ev.check("Sensor: OK\nTemperature: 30 C", {})
        assert ok is False
        assert reason == "Not yet achieved"


class TestVariableMatch:
    def test_variable_match(self):
        ev = Evaluator({"variable": {"name": "sensor_value", "value": 25}})
        ok, reason = ev.check("", {"variables": {"sensor_value": 25}})
        assert ok is True
        assert "sensor_value" in reason

    def test_variable_mismatch(self):
        ev = Evaluator({"variable": {"name": "sensor_value", "value": 25}})
        ok, reason = ev.check("", {"variables": {"sensor_value": 99}})
        assert ok is False

    def test_variable_missing(self):
        ev = Evaluator({"variable": {"name": "sensor_value", "value": 25}})
        ok, reason = ev.check("", {"variables": {}})
        assert ok is False


class TestCrashDetection:
    def test_crash_returns_failure(self):
        ev = Evaluator({"no_crash": True})
        ok, reason = ev.check("", {"crash": "HardFault"})
        assert ok is False
        assert "HardFault" in reason

    def test_no_crash_ok(self):
        ev = Evaluator({"serial_contains": "done"})
        ok, reason = ev.check("done", {})
        assert ok is True


class TestStagnation:
    def test_stagnation_after_three_same_states(self):
        ev = Evaluator({"serial_contains": "TARGET"})
        for _ in range(3):
            ev.check("output", {"state": "same"})
        assert ev.is_stagnant is False  # 3 checks → count=2 (first sets hash, next 2 increment)

    def test_stagnation_four_same(self):
        ev = Evaluator({"serial_contains": "TARGET"})
        for _ in range(4):
            ev.check("output", {"state": "same"})
        # First call sets last_state_hash, calls 2-4 increment → count=3
        assert ev.is_stagnant is True

    def test_state_change_resets_stagnation(self):
        ev = Evaluator({"serial_contains": "TARGET"})
        ev.check("output", {"state": "a"})
        ev.check("output", {"state": "a"})
        ev.check("output", {"state": "a"})
        assert ev.stagnation_count == 2
        # State changes → reset
        ev.check("output", {"state": "b"})
        assert ev.stagnation_count == 0
        assert ev.is_stagnant is False
