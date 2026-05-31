"""Tests for debug_loop.safety."""

import pytest

from debug_loop.safety import SafetyChecker


class TestWhitelist:
    @pytest.mark.parametrize("cmd", [
        "info registers",
        "print x",
        "x/4wx 0x20000000",
        "backtrace",
        "bt",
        "step",
        "next",
        "continue",
        "break main",
        "finish",
        "info locals",
        "info breakpoints",
        "info all-registers",
    ])
    def test_allowed_commands(self, cmd):
        sc = SafetyChecker()
        assert sc.is_allowed(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "monitor reset",
        "set var x = 10",
        "delete",
        "shell rm -rf /",
        "quit",
    ])
    def test_rejected_commands(self, cmd):
        sc = SafetyChecker()
        assert sc.is_allowed(cmd) is False

    def test_whitespace_stripped(self):
        sc = SafetyChecker()
        assert sc.is_allowed("  info registers  ") is True


class TestIterationLimit:
    def test_under_limit(self):
        sc = SafetyChecker()
        for _ in range(49):
            assert sc.check_iteration_limit() is True

    def test_at_limit(self):
        sc = SafetyChecker()
        for _ in range(49):
            sc.check_iteration_limit()
        # 50th call should return False
        assert sc.check_iteration_limit() is False


class TestOscillationDetection:
    def test_no_oscillation_with_different_states(self):
        sc = SafetyChecker()
        for i in range(5):
            assert sc.check_oscillation(f"state_{i}") is False

    def test_oscillation_with_same_state(self):
        sc = SafetyChecker()
        for _ in range(4):
            assert sc.check_oscillation("same") is False
        # 5th same state triggers oscillation
        assert sc.check_oscillation("same") is True

    def test_oscillation_two_alternating(self):
        sc = SafetyChecker()
        for _ in range(3):
            sc.check_oscillation("a")
            sc.check_oscillation("b")
        # 6 entries, window=5, last 5 = a,b,a,b,a → 2 unique → oscillation
        assert sc.check_oscillation("b") is True
