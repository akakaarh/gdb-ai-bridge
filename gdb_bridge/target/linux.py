"""Linux kernel target adapter.

Parses ``bt`` output for speed and falls back to frame-chain walking
when the output format is unexpected.  Also exposes kernel metadata
(e.g. ``linux_banner``).

The real GDB ``gdb`` built-in module is accessed via ``sys.modules``
at **call time** (not import time) to avoid conflicts with our own
package that shadows it on ``sys.path``.
"""

from __future__ import annotations

import re

from .base import TargetAdapter

# Regex matching typical GDB ``bt`` output lines:
#   #0  0xffffffff81001234 in func_name at file.c:42
#   #1  0xffffffff81005678 in func_name ()
_BT_RE = re.compile(
    r"^#(?P<frame>\d+)\s+"           # frame number
    r"(?P<addr>0x[0-9a-fA-F]+)\s+"   # address
    r"in\s+(?P<func>[^\s(]+)"        # function name (no spaces or parens)
    r"(?:\s*\(\))?"                   # optional "()"
    r"(?:\s+at\s+(?P<file>[^:]+)"    # optional "at file"
    r"(?::(?P<line>\d+))?)?",        # optional ":line"
    re.MULTILINE,
)

_MAX_FRAMES = 20


class LinuxAdapter(TargetAdapter):
    """Adapter for Linux kernel debugging (vmlinux, kdump, QEMU)."""

    name = "linux"

    # ------------------------------------------------------------------
    # Stack trace
    # ------------------------------------------------------------------

    def get_stack_trace(self) -> list[dict]:
        """Return stack frames, preferring ``bt`` output parsing.

        Falls back to the frame-chain walk (from the base class) when
        parsing fails.
        """
        frames = self._parse_bt()
        if frames:
            return frames
        return self._walk_frames()

    def _parse_bt(self) -> list[dict]:
        """Try to parse ``bt`` output."""
        gdb = self._get_gdb()
        try:
            raw = gdb.execute("bt", to_string=True)
        except self._gdb_error(gdb):
            return []

        frames: list[dict] = []
        for m in _BT_RE.finditer(raw):
            func = m.group("func") or "<unknown>"
            addr = m.group("addr") or "0x00000000"
            filename = m.group("file") or ""
            line = int(m.group("line")) if m.group("line") else 0

            frames.append(
                {
                    "function": func,
                    "address": addr,
                    "file": filename,
                    "line": line,
                    "confidence": "high" if func != "<unknown>" else "medium",
                }
            )

        return frames[:_MAX_FRAMES]

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    def resolve_symbol(self, addr: str) -> str | None:
        """Resolve *addr* to a symbol via ``info symbol``."""
        gdb = self._get_gdb()
        try:
            output = gdb.execute(f"info symbol {addr}", to_string=True)
        except self._gdb_error(gdb):
            return None
        output = output.strip()
        if not output or "No symbol matches" in output:
            return None
        return output

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_metadata(self) -> dict:
        """Try to read the ``linux_banner`` symbol for kernel version."""
        gdb = self._get_gdb()
        meta: dict = {}
        try:
            val = gdb.parse_and_eval("linux_banner")
            meta["linux_banner"] = str(val).strip('"')
        except self._gdb_error(gdb):
            pass
        return meta
