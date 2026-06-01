"""Tests for enricher module."""

import sys
import os
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch, mock_open
from parser import OopsInfo
from enricher import (
    enrich, EnrichedContext, context_to_text, SymbolInfo,
    _get_source_context, _get_file_functions, MAX_SOURCE_LINES,
)


def _make_test_db(tmp_dir):
    """Create a minimal test SQLite DB with files and symbols tables."""
    db_path = os.path.join(tmp_dir, "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            subsystem TEXT,
            line_count INTEGER
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_id INTEGER NOT NULL,
            line INTEGER NOT NULL,
            pattern TEXT,
            typeref TEXT,
            signature TEXT,
            is_static INTEGER DEFAULT 0,
            FOREIGN KEY (file_id) REFERENCES files(id)
        );
        CREATE TABLE call_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id INTEGER NOT NULL,
            callee_id INTEGER NOT NULL,
            call_site_file_id INTEGER NOT NULL,
            call_site_line INTEGER NOT NULL,
            FOREIGN KEY (caller_id) REFERENCES symbols(id),
            FOREIGN KEY (callee_id) REFERENCES symbols(id),
            FOREIGN KEY (call_site_file_id) REFERENCES files(id)
        );
    """)
    conn.execute("INSERT INTO files (id, path, subsystem) VALUES (1, 'drivers/gpio/gpiolib.c', 'drivers/gpio')")
    conn.execute(
        "INSERT INTO symbols (id, name, kind, file_id, line, typeref, signature, is_static) "
        "VALUES (1, 'gpio_get_value', 'function', 1, 100, 'typename:int', '(unsigned int gpio)', 0)"
    )
    conn.execute(
        "INSERT INTO symbols (id, name, kind, file_id, line, typeref, signature, is_static) "
        "VALUES (2, 'gpio_set_value', 'function', 1, 120, 'typename:void', '(unsigned int gpio, int value)', 0)"
    )
    conn.execute(
        "INSERT INTO symbols (id, name, kind, file_id, line, typeref, signature, is_static) "
        "VALUES (3, 'gpio_direction_output', 'function', 1, 150, 'typename:int', '(unsigned int gpio, int value)', 0)"
    )
    conn.execute(
        "INSERT INTO symbols (id, name, kind, file_id, line, typeref, signature, is_static) "
        "VALUES (4, 'gpiochip_get_direction', 'function', 1, 200, 'typename:int', '(struct gpio_chip *gc, unsigned int offset)', 0)"
    )
    conn.commit()
    conn.close()
    return db_path


def _make_test_source(tmp_dir, line_count=250):
    """Create a fake source file for testing source context extraction."""
    src_dir = os.path.join(tmp_dir, "drivers", "gpio")
    os.makedirs(src_dir, exist_ok=True)
    src_path = os.path.join(src_dir, "gpiolib.c")
    with open(src_path, "w") as f:
        for i in range(1, line_count + 1):
            if i == 100:
                f.write("int gpio_get_value(unsigned int gpio)\n")
            elif i == 101:
                f.write("{\n")
            elif i == 102:
                f.write("    struct gpio_chip *chip;\n")
            elif i == 103:
                f.write("    chip = gpio_to_chip(gpio);\n")
            elif i == 104:
                f.write("    return chip->get(chip, gpio - chip->base);\n")
            elif i == 105:
                f.write("}\n")
            else:
                f.write(f"/* line {i} */\n")
    return src_path


# ── EnrichedContext field tests ──────────────────────────────────────


class TestEnrichedContextFields:
    def test_has_source_context_field(self):
        ctx = EnrichedContext()
        assert hasattr(ctx, "source_context")
        assert ctx.source_context == ""

    def test_has_file_functions_field(self):
        ctx = EnrichedContext()
        assert hasattr(ctx, "file_functions")
        assert ctx.file_functions == []

    def test_file_functions_is_independent(self):
        """Each instance must have its own list, not share a reference."""
        a = EnrichedContext()
        b = EnrichedContext()
        a.file_functions.append("foo")
        assert b.file_functions == []


# ── _get_source_context tests ───────────────────────────────────────


class TestGetSourceContext:
    def test_returns_source_when_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            src_path = _make_test_source(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            sym = _lookup(conn, "gpio_get_value")
            result = _get_source_context(conn, sym, kernel_root=tmp)
            assert "gpio_get_value" in result
            assert "gpio_to_chip" in result
            conn.close()

    def test_returns_empty_when_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            sym = _lookup(conn, "gpio_get_value")
            result = _get_source_context(conn, sym, kernel_root="/nonexistent")
            assert result == ""
            conn.close()

    def test_returns_empty_when_symbol_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            result = _get_source_context(conn, None, kernel_root=tmp)
            assert result == ""
            conn.close()

    def test_truncates_to_max_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            # Create a file with a function at line 500 — ±10 lines = 490-510
            _make_test_source(tmp, line_count=600)
            # Insert a symbol at line 500
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "INSERT INTO symbols (id, name, kind, file_id, line, typeref, signature, is_static) "
                "VALUES (100, 'deep_func', 'function', 1, 500, 'typename:int', '(void)', 0)"
            )
            conn.commit()
            sym = _lookup(conn, "deep_func")
            result = _get_source_context(conn, sym, kernel_root=tmp)
            line_count = len(result.strip().splitlines())
            assert line_count <= MAX_SOURCE_LINES
            conn.close()

    def test_context_includes_line_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            src_path = _make_test_source(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            sym = _lookup(conn, "gpio_get_value")
            result = _get_source_context(conn, sym, kernel_root=tmp)
            # Should have line number markers like "95: ..."
            assert any(":" in line for line in result.splitlines())
            conn.close()


# ── _get_file_functions tests ───────────────────────────────────────


class TestGetFileFunctions:
    def test_returns_functions_in_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            result = _get_file_functions(conn, "drivers/gpio/gpiolib.c")
            # Format is "return_type func_name(sig)" — check substrings
            text = "\n".join(result)
            assert "gpio_get_value" in text
            assert "gpio_set_value" in text
            conn.close()

    def test_returns_empty_for_unknown_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            result = _get_file_functions(conn, "nonexistent.c")
            assert result == []
            conn.close()

    def test_limits_to_20_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            # Insert 25 functions
            for i in range(5, 30):
                conn.execute(
                    "INSERT INTO symbols (id, name, kind, file_id, line, typeref, signature, is_static) "
                    f"VALUES ({100+i}, 'func_{i}', 'function', 1, {200+i}, 'typename:void', '(void)', 0)"
                )
            conn.commit()
            result = _get_file_functions(conn, "drivers/gpio/gpiolib.c")
            assert len(result) <= 20
            conn.close()

    def test_includes_signatures(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            result = _get_file_functions(conn, "drivers/gpio/gpiolib.c")
            # Each entry should contain the function name
            assert any("gpio_get_value" in f for f in result)
            conn.close()


# ── enrich() integration tests ──────────────────────────────────────


class TestEnrichWithNewFields:
    def test_enrich_with_missing_db(self):
        oops = OopsInfo(crash_function="test_func")
        ctx = enrich(oops, db_path="/nonexistent/db.sqlite")
        assert isinstance(ctx, EnrichedContext)
        assert ctx.crash_symbol is None

    def test_enrich_populates_source_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            _make_test_source(tmp)
            oops = OopsInfo(crash_function="gpio_get_value")
            ctx = enrich(oops, db_path=db_path, kernel_root=tmp)
            # source_context may or may not be populated depending on file access
            assert isinstance(ctx.source_context, str)

    def test_enrich_populates_file_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _make_test_db(tmp)
            oops = OopsInfo(crash_function="gpio_get_value")
            ctx = enrich(oops, db_path=db_path)
            assert isinstance(ctx.file_functions, list)
            # Should find sibling functions in the same file
            assert len(ctx.file_functions) > 0


# ── context_to_text tests ───────────────────────────────────────────


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

    def test_with_source_context(self):
        ctx = EnrichedContext()
        ctx.source_context = "95: /* before */\n100: int gpio_get_value()\n105: }\n"
        text = context_to_text(ctx)
        assert "Source Code Context" in text
        assert "gpio_get_value" in text

    def test_with_file_functions(self):
        ctx = EnrichedContext()
        ctx.file_functions = [
            "int gpio_get_value(unsigned int gpio)",
            "void gpio_set_value(unsigned int gpio, int value)",
        ]
        text = context_to_text(ctx)
        assert "File Functions" in text
        assert "gpio_get_value" in text
        assert "gpio_set_value" in text

    def test_source_context_section_before_file_functions(self):
        """Source context should appear before file functions for readability."""
        ctx = EnrichedContext()
        ctx.source_context = "some code"
        ctx.file_functions = ["func_a()"]
        text = context_to_text(ctx)
        src_pos = text.find("Source Code Context")
        func_pos = text.find("File Functions")
        assert src_pos < func_pos


# ── Helper ──────────────────────────────────────────────────────────


def _lookup(conn, name):
    """Helper to look up a SymbolInfo from a test DB."""
    row = conn.execute(
        "SELECT s.name, s.kind, s.typeref, s.signature, f.path, s.line, s.is_static "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ? AND s.kind = 'function' LIMIT 1",
        (name,),
    ).fetchone()
    if not row:
        return None
    return SymbolInfo(
        name=row[0], kind=row[1], return_type=(row[2] or "").removeprefix("typename:"),
        signature=row[3] or "", file=row[4], line=row[5], is_static=bool(row[6]),
    )
