"""Tests for analyzer module."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import OopsInfo, Frame
from enricher import EnrichedContext
from analyzer import build_prompt, get_system_prompt, build_prompt_from_json


class TestGetSystemPrompt:
    def test_baremetal_prompt(self):
        p = get_system_prompt("baremetal")
        assert "Cortex-M" in p
        assert "CFSR" in p

    def test_linux_prompt(self):
        p = get_system_prompt("linux")
        assert "Linux" in p

    def test_generic_prompt(self):
        p = get_system_prompt("generic")
        assert "嵌入式" in p or "调试" in p


class TestBuildPrompt:
    def test_basic_prompt(self):
        oops = OopsInfo(error_type="test error", crash_function="test_func")
        ctx = EnrichedContext()
        prompt = build_prompt(oops, ctx, "baremetal")
        assert "test error" in prompt
        assert "test_func" in prompt
        assert "CFSR" in prompt


class TestBuildPromptFromJson:
    def test_from_gdb_json(self):
        data = {
            "config": {"arch": "arm", "target": "baremetal"},
            "layer0": {
                "status": "ok",
                "registers": {"r0": {"value": "0x0", "role": "arg0"}},
            },
            "layer1": {
                "status": "ok",
                "stack_trace": [{"function": "main", "confidence": "high"}],
            },
        }
        prompt = build_prompt_from_json(data)
        assert "arm" in prompt
        assert "main" in prompt
