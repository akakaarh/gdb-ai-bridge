"""Tests for parser module."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse, parse_oops, parse_json, OopsInfo, Frame


FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class TestParseOops:
    def test_parse_sample_oops(self):
        with open(os.path.join(FIXTURES, "sample_oops.txt"), encoding="utf-8") as f:
            text = f.read()
        info = parse_oops(text)
        assert info.error_type == "Unable to handle kernel NULL pointer dereference"
        assert info.fault_address == "0000000000000020"
        assert info.crash_function == "pca953x_irq_handler"
        assert info.arch == "arm64"
        assert len(info.registers) > 0
        assert len(info.stack_trace) > 0

    def test_parse_real_oops(self):
        with open(os.path.join(FIXTURES, "real_oops.txt"), encoding="utf-8") as f:
            text = f.read()
        info = parse_oops(text)
        assert "Kernel panic" in info.error_type
        assert len(info.stack_trace) > 0

    def test_parse_empty_text(self):
        info = parse_oops("")
        assert info.error_type == ""
        assert len(info.stack_trace) == 0


class TestParseJson:
    def test_parse_json_file(self):
        with open(os.path.join(FIXTURES, "m4_hardfault.json"), encoding="utf-8") as f:
            text = f.read()
        info = parse_json(text)
        assert info.arch == "arm"
        assert len(info.registers) > 0


class TestParseAuto:
    def test_auto_detect_text(self):
        info = parse("Unable to handle kernel NULL pointer")
        assert isinstance(info, OopsInfo)

    def test_auto_detect_json(self):
        info = parse('{"config": {"arch": "arm"}, "layer0": {"status": "ok"}}')
        assert isinstance(info, OopsInfo)
        assert info.arch == "arm"
