"""Tests for enricher module."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
from parser import OopsInfo
from enricher import enrich, EnrichedContext, context_to_text, SymbolInfo


class TestEnrich:
    def test_enrich_with_missing_db(self):
        oops = OopsInfo(crash_function="test_func")
        ctx = enrich(oops, db_path="/nonexistent/db.sqlite")
        assert isinstance(ctx, EnrichedContext)
        assert ctx.crash_symbol is None


class TestContextToText:
    def test_empty_context(self):
        ctx = EnrichedContext()
        text = context_to_text(ctx)
        assert text == ""

    def test_with_crash_symbol(self):
        ctx = EnrichedContext()
        sym = SymbolInfo(
            name="test_func",
            file="test.c",
            line=42,
            is_static=False,
            return_type="int",
            signature="(void)",
        )
        ctx.crash_symbol = sym
        text = context_to_text(ctx)
        assert "test_func" in text
        assert "test.c" in text
        assert "42" in text

    def test_with_callers(self):
        ctx = EnrichedContext()
        ctx.crash_callers = [
            SymbolInfo(name="caller_a", file="a.c", line=10),
            SymbolInfo(name="caller_b", file="b.c", line=20),
        ]
        text = context_to_text(ctx)
        assert "caller_a" in text
        assert "caller_b" in text

    def test_with_wiki_snippets(self):
        ctx = EnrichedContext()
        ctx.wiki_snippets = ["snippet one", "snippet two"]
        text = context_to_text(ctx)
        assert "snippet one" in text
        assert "snippet two" in text
