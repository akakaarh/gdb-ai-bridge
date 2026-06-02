"""Tests for debug_loop.actions module."""

import pytest

from debug_loop.actions import ACTIONS, get_available_actions, translate_action, validate_action


class TestValidateAction:
    def test_valid_no_params(self):
        ok, err = validate_action({"action": "backtrace", "params": {}})
        assert ok is True
        assert err is None

    def test_valid_with_params(self):
        ok, err = validate_action({"action": "read_register", "params": {"name": "pc"}})
        assert ok is True
        assert err is None

    def test_valid_params_default_empty(self):
        """Params key is optional and defaults to empty dict."""
        ok, err = validate_action({"action": "step"})
        assert ok is True
        assert err is None

    def test_not_a_dict(self):
        ok, err = validate_action("backtrace")
        assert ok is False
        assert "dict" in err

    def test_missing_action_field(self):
        ok, err = validate_action({"params": {}})
        assert ok is False
        assert "missing 'action'" in err

    def test_unknown_action(self):
        ok, err = validate_action({"action": "fly_to_moon", "params": {}})
        assert ok is False
        assert "unknown action" in err

    def test_missing_required_param(self):
        ok, err = validate_action({"action": "read_register", "params": {}})
        assert ok is False
        assert "missing params" in err
        assert "name" in err

    def test_extra_param(self):
        ok, err = validate_action({"action": "step", "params": {"extra": 1}})
        assert ok is False
        assert "unexpected params" in err

    def test_params_not_dict(self):
        ok, err = validate_action({"action": "step", "params": "wrong"})
        assert ok is False
        assert "dict" in err


class TestTranslateAction:
    def test_read_register(self):
        cmd, err = translate_action({"action": "read_register", "params": {"name": "pc"}})
        assert err is None
        assert cmd == "print $pc"

    def test_read_registers(self):
        cmd, err = translate_action({"action": "read_registers", "params": {}})
        assert err is None
        assert cmd == "info registers"

    def test_read_variable(self):
        cmd, err = translate_action({"action": "read_variable", "params": {"name": "counter"}})
        assert err is None
        assert cmd == "print counter"

    def test_read_memory(self):
        cmd, err = translate_action({
            "action": "read_memory",
            "params": {"addr": "0x20000000", "count": "8"},
        })
        assert err is None
        assert cmd == "x/8wx 0x20000000"

    def test_read_memory_different_values(self):
        cmd, err = translate_action({
            "action": "read_memory",
            "params": {"addr": "0x80000000", "count": "16"},
        })
        assert err is None
        assert cmd == "x/16wx 0x80000000"

    def test_set_breakpoint(self):
        cmd, err = translate_action({
            "action": "set_breakpoint",
            "params": {"location": "drivers/net/eth.c:42"},
        })
        assert err is None
        assert cmd == "break drivers/net/eth.c:42"

    def test_delete_breakpoint(self):
        cmd, err = translate_action({"action": "delete_breakpoint", "params": {"number": "3"}})
        assert err is None
        assert cmd == "delete 3"

    def test_step(self):
        cmd, err = translate_action({"action": "step"})
        assert err is None
        assert cmd == "step"

    def test_next(self):
        cmd, err = translate_action({"action": "next"})
        assert err is None
        assert cmd == "next"

    def test_continue_exec(self):
        cmd, err = translate_action({"action": "continue_exec"})
        assert err is None
        assert cmd == "continue"

    def test_backtrace(self):
        cmd, err = translate_action({"action": "backtrace"})
        assert err is None
        assert cmd == "backtrace"

    def test_info_locals(self):
        cmd, err = translate_action({"action": "info_locals"})
        assert err is None
        assert cmd == "info locals"

    def test_finish(self):
        cmd, err = translate_action({"action": "finish"})
        assert err is None
        assert cmd == "finish"

    def test_dump_memory(self):
        cmd, err = translate_action({
            "action": "dump_memory",
            "params": {"file": "/tmp/core.bin", "addr": "0x20000000", "size": "4096"},
        })
        assert err is None
        assert cmd == "dump binary memory /tmp/core.bin 0x20000000 0x20000000+4096"

    def test_dump_memory_different_values(self):
        cmd, err = translate_action({
            "action": "dump_memory",
            "params": {"file": "stack.bin", "addr": "0x10000000", "size": "8192"},
        })
        assert err is None
        assert cmd == "dump binary memory stack.bin 0x10000000 0x10000000+8192"

    def test_dump_memory_missing_file(self):
        ok, err = validate_action({
            "action": "dump_memory",
            "params": {"addr": "0x20000000", "size": "4096"},
        })
        assert ok is False
        assert "file" in err

    def test_dump_memory_missing_addr(self):
        ok, err = validate_action({
            "action": "dump_memory",
            "params": {"file": "/tmp/core.bin", "size": "4096"},
        })
        assert ok is False
        assert "addr" in err

    def test_dump_memory_missing_size(self):
        ok, err = validate_action({
            "action": "dump_memory",
            "params": {"file": "/tmp/core.bin", "addr": "0x20000000"},
        })
        assert ok is False
        assert "size" in err

    def test_invalid_action_returns_error(self):
        cmd, err = translate_action({"action": "nonexistent"})
        assert cmd is None
        assert err is not None


class TestGetAvailableActions:
    def test_returns_all_actions(self):
        actions = get_available_actions()
        names = {a["action"] for a in actions}
        assert names == set(ACTIONS.keys())

    def test_entry_shape(self):
        actions = get_available_actions()
        for entry in actions:
            assert "action" in entry
            assert "params" in entry
            assert isinstance(entry["params"], list)

    def test_count_matches_actions_dict(self):
        actions = get_available_actions()
        assert len(actions) == len(ACTIONS)
