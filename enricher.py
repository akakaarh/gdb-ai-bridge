"""Enricher — supplements oops info with kernel-index symbol data and wiki context."""

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field

from parser import OopsInfo


@dataclass
class SymbolInfo:
    name: str = ""
    kind: str = ""
    return_type: str = ""
    signature: str = ""
    file: str = ""
    line: int = 0
    is_static: bool = False


@dataclass
class EnrichedContext:
    crash_symbol: SymbolInfo | None = None
    crash_callers: list[SymbolInfo] = field(default_factory=list)
    crash_callees: list[SymbolInfo] = field(default_factory=list)
    stack_symbols: dict[str, SymbolInfo] = field(default_factory=dict)
    wiki_snippets: list[str] = field(default_factory=list)
    source_context: str = ""
    file_functions: list[str] = field(default_factory=list)


MAX_SOURCE_LINES = 50


DB_PATH = os.environ.get("KERNEL_INDEX_DB", "")
if not DB_PATH:
    # Try common locations
    _candidates = [
        os.path.expanduser("~/.kernel-index/kernel_index.db"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel_index.db"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "kernel-code-index", "kernel_index.db"),
    ]
    for _c in _candidates:
        if os.path.exists(_c):
            DB_PATH = _c
            break

KERNEL_ROOT = os.environ.get("KERNEL_SOURCE_ROOT", "")


def enrich(
    oops: OopsInfo,
    db_path: str = DB_PATH,
    kernel_root: str = KERNEL_ROOT,
) -> EnrichedContext:
    """Enrich oops info with symbol data and wiki context."""
    ctx = EnrichedContext()

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except Exception:
        return ctx

    # 1. Crash function symbol
    ctx.crash_symbol = _lookup_symbol(conn, oops.crash_function)

    # 2. Callers and callees of crash function
    if ctx.crash_symbol:
        ctx.crash_callers = _get_callers(conn, oops.crash_function)
        ctx.crash_callees = _get_callees(conn, oops.crash_function)

    # 3. Symbols for stack trace functions
    seen = set()
    for frame in oops.stack_trace[:15]:  # limit to top 15 frames
        name = frame.function.split(".")[0]  # strip .constprop.0 etc.
        if name not in seen:
            seen.add(name)
            sym = _lookup_symbol(conn, name)
            if sym:
                ctx.stack_symbols[name] = sym

    # 4. Source code context near crash function
    ctx.source_context = _get_source_context(conn, ctx.crash_symbol, kernel_root)

    # 5. Other functions in the same file as the crash function
    if ctx.crash_symbol and ctx.crash_symbol.file:
        ctx.file_functions = _get_file_functions(conn, ctx.crash_symbol.file)

    conn.close()

    # 6. Wiki context
    ctx.wiki_snippets = _search_wiki(oops)

    return ctx


def _lookup_symbol(conn: sqlite3.Connection, name: str) -> SymbolInfo | None:
    row = conn.execute(
        """SELECT s.name, s.kind, s.typeref, s.signature, f.path, s.line, s.is_static
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE s.name = ? AND s.kind = 'function'
           LIMIT 1""",
        (name,),
    ).fetchone()
    if not row:
        return None
    typeref = (row["typeref"] or "").removeprefix("typename:")
    return SymbolInfo(
        name=row["name"],
        kind=row["kind"],
        return_type=typeref,
        signature=row["signature"] or "",
        file=row["path"],
        line=row["line"],
        is_static=bool(row["is_static"]),
    )


def _get_callers(conn: sqlite3.Connection, func_name: str) -> list[SymbolInfo]:
    rows = conn.execute(
        """SELECT DISTINCT s.name, s.kind, s.typeref, s.signature, f.path, s.line, s.is_static
           FROM call_relations cr
           JOIN symbols s ON cr.caller_id = s.id
           JOIN files f ON s.file_id = f.id
           JOIN symbols callee ON cr.callee_id = callee.id
           WHERE callee.name = ?
           LIMIT 20""",
        (func_name,),
    ).fetchall()
    return [
        SymbolInfo(
            name=r["name"], kind=r["kind"], return_type=r["typeref"] or "",
            signature=r["signature"] or "", file=r["path"], line=r["line"],
            is_static=bool(r["is_static"]),
        )
        for r in rows
    ]


def _get_callees(conn: sqlite3.Connection, func_name: str) -> list[SymbolInfo]:
    rows = conn.execute(
        """SELECT DISTINCT s.name, s.kind, s.typeref, s.signature, f.path, s.line, s.is_static
           FROM call_relations cr
           JOIN symbols s ON cr.callee_id = s.id
           JOIN files f ON s.file_id = f.id
           JOIN symbols caller ON cr.caller_id = caller.id
           WHERE caller.name = ?
           LIMIT 20""",
        (func_name,),
    ).fetchall()
    return [
        SymbolInfo(
            name=r["name"], kind=r["kind"], return_type=r["typeref"] or "",
            signature=r["signature"] or "", file=r["path"], line=r["line"],
            is_static=bool(r["is_static"]),
        )
        for r in rows
    ]


def _get_source_context(
    conn: sqlite3.Connection,
    crash_symbol: SymbolInfo | None,
    kernel_root: str = "",
) -> str:
    """Extract source code near the crash function (±10 lines).

    Returns the source context as a string with line numbers, or empty string
    if the source file is unavailable (e.g., remote debugging scenario).
    """
    if not crash_symbol or not crash_symbol.file or crash_symbol.line <= 0:
        return ""

    source_path = crash_symbol.file
    if kernel_root:
        source_path = os.path.join(kernel_root, crash_symbol.file)

    try:
        with open(source_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except (OSError, IOError):
        return ""

    func_line = crash_symbol.line  # 1-indexed
    start = max(0, func_line - 11)  # 10 lines before (0-indexed)
    end = min(len(all_lines), func_line + 10)  # 10 lines after

    selected = all_lines[start:end]

    # Truncate to MAX_SOURCE_LINES
    if len(selected) > MAX_SOURCE_LINES:
        selected = selected[:MAX_SOURCE_LINES]

    # Format with line numbers (1-indexed)
    result_lines = []
    for i, line in enumerate(selected, start=start + 1):
        result_lines.append(f"{i}: {line.rstrip()}")

    return "\n".join(result_lines)


def _get_file_functions(conn: sqlite3.Connection, file_path: str) -> list[str]:
    """Return function signatures defined in the given file, limited to 20."""
    rows = conn.execute(
        """SELECT s.name, s.typeref, s.signature, s.line
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE f.path = ? AND s.kind = 'function'
           ORDER BY s.line
           LIMIT 20""",
        (file_path,),
    ).fetchall()
    result = []
    for r in rows:
        typeref = (r[1] or "").removeprefix("typename:")
        sig = r[2] or ""
        result.append(f"{typeref} {r[0]}{sig}")
    return result


def _search_wiki(oops: OopsInfo) -> list[str]:
    """Search wiki for relevant context using qmd CLI."""
    snippets = []
    queries = [
        oops.error_type,
        oops.crash_function.replace("_", " "),
    ]
    for q in queries:
        try:
            result = subprocess.run(
                ["qmd", "search", "--query", q, "--limit", "3", "--format", "json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for item in data.get("results", []):
                    snippet = item.get("snippet", "").strip()
                    if snippet and len(snippet) > 20:
                        snippets.append(f"[{item.get('title', '?')}] {snippet}")
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass
    return snippets[:5]


def context_to_text(ctx: EnrichedContext) -> str:
    """Format enriched context as readable text for prompt assembly."""
    parts = []

    if ctx.crash_symbol:
        s = ctx.crash_symbol
        static = " (static)" if s.is_static else ""
        parts.append(f"## Crash Function\n"
                      f"- Name: {s.name}{static}\n"
                      f"- Signature: {s.return_type} {s.name}{s.signature}\n"
                      f"- Location: {s.file}:{s.line}")

    if ctx.crash_callers:
        parts.append("## Callers (who calls this function)")
        for c in ctx.crash_callers[:10]:
            parts.append(f"  - {c.name} at {c.file}:{c.line}")

    if ctx.crash_callees:
        parts.append("## Callees (what this function calls)")
        for c in ctx.crash_callees[:10]:
            parts.append(f"  - {c.name} at {c.file}:{c.line}")

    if ctx.stack_symbols:
        parts.append("## Stack Trace Function Details")
        for name, sym in ctx.stack_symbols.items():
            static = " (static)" if sym.is_static else ""
            parts.append(f"  - {name}{static}: {sym.return_type} {name}{sym.signature} "
                          f"at {sym.file}:{sym.line}")

    if ctx.wiki_snippets:
        parts.append("## Related Wiki Knowledge")
        for s in ctx.wiki_snippets:
            parts.append(f"  - {s}")

    if ctx.source_context:
        parts.append("## Source Code Context (crash function)")
        parts.append(ctx.source_context)

    if ctx.file_functions:
        parts.append("## File Functions (same file as crash function)")
        for f in ctx.file_functions:
            parts.append(f"  - {f}")

    return "\n".join(parts)
