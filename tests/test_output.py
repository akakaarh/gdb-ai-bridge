"""Tests for gdb.output — JSON formatting and file I/O."""
import json
import os
import tempfile

import pytest

from gdb_bridge.collector import DebugContext
from gdb_bridge.output import context_to_dict, save_context, print_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(**overrides):
    ctx = DebugContext()
    ctx.timestamp = overrides.get("timestamp", "2026-05-31T12:00:00")
    ctx.config = overrides.get("config", {"arch": "arm-cortex-m", "target": "openocd", "elf_file": "fw.elf"})
    ctx.layer0 = overrides.get("layer0", {"status": "ok", "registers": {"r0": {"value": "0x0", "role": "general"}}})
    ctx.layer1 = overrides.get("layer1", {"status": "ok"})
    ctx.layer2 = overrides.get("layer2", {})
    ctx.errors = overrides.get("errors", [])
    return ctx


# ---------------------------------------------------------------------------
# context_to_dict
# ---------------------------------------------------------------------------

class TestContextToDict:
    def test_returns_dict(self):
        ctx = _make_context()
        result = context_to_dict(ctx)
        assert isinstance(result, dict)

    def test_has_all_top_level_keys(self):
        ctx = _make_context()
        result = context_to_dict(ctx)
        expected = {"version", "timestamp", "config", "layer0", "layer1", "layer2", "errors", "decoded_registers"}
        assert set(result.keys()) == expected

    def test_preserves_data(self):
        ctx = _make_context(
            config={"arch": "riscv32", "target": "jlink", "elf_file": "app.elf"},
            layer0={"status": "ok", "registers": {"a0": {"value": "0xABCD"}}},
            errors=["some warning"],
        )
        result = context_to_dict(ctx)

        assert result["config"]["arch"] == "riscv32"
        assert result["layer0"]["registers"]["a0"]["value"] == "0xABCD"
        assert result["errors"] == ["some warning"]

    def test_json_serializable(self):
        ctx = _make_context()
        result = context_to_dict(ctx)
        # Should not raise
        json.dumps(result)


# ---------------------------------------------------------------------------
# save_context
# ---------------------------------------------------------------------------

class TestSaveContext:
    def test_creates_valid_json_file(self, tmp_path):
        ctx = _make_context()
        filepath = str(tmp_path / "output.json")

        save_context(ctx, filepath)

        assert os.path.exists(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == "1.0"

    def test_roundtrip_preserves_data(self, tmp_path):
        ctx = _make_context(
            layer0={"status": "ok", "registers": {"sp": {"value": "0x20004000", "role": "Stack Pointer"}}},
            layer1={"status": "ok", "stack_trace": [{"function": "main", "line": 42}]},
            errors=["timeout reading register"],
        )
        filepath = str(tmp_path / "roundtrip.json")

        save_context(ctx, filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        assert loaded["layer0"]["registers"]["sp"]["role"] == "Stack Pointer"
        assert loaded["layer1"]["stack_trace"][0]["line"] == 42
        assert loaded["errors"] == ["timeout reading register"]

    def test_utf8_content(self, tmp_path):
        ctx = _make_context(
            layer0={"status": "ok", "crash_reason": "指令总线错误"}
        )
        filepath = str(tmp_path / "utf8.json")

        save_context(ctx, filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["layer0"]["crash_reason"] == "指令总线错误"


# ---------------------------------------------------------------------------
# print_context
# ---------------------------------------------------------------------------

class TestPrintContext:
    def test_prints_json_to_stdout(self, capsys):
        ctx = _make_context()
        print_context(ctx)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["version"] == "1.0"

    def test_printed_json_is_pretty(self, capsys):
        ctx = _make_context()
        print_context(ctx)

        captured = capsys.readouterr()
        # Pretty-printed: should contain newlines and indentation
        assert "\n" in captured.out
        assert "  " in captured.out
