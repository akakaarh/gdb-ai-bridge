"""Base class for target-specific adapters.

A *target* represents the connection to the debuggee (bare-metal via
JTAG/SWD, QEMU, Linux userspace process, etc.).  Subclass and override
the methods you need.
"""

from __future__ import annotations

import sys

# Maximum number of frames to traverse to avoid runaway on corrupt stacks.
_MAX_FRAMES = 20


class TargetAdapter:
    """Abstract target adapter.  All methods return safe empty defaults."""

    name = ""

    # ------------------------------------------------------------------
    # GDB helpers (shared by all subclasses)
    # ------------------------------------------------------------------

    def _get_gdb(self):
        """Return the GDB Python API module, or raise if unavailable."""
        mod = sys.modules.get("gdb")
        if mod is None or not hasattr(mod, "selected_frame"):
            raise RuntimeError(
                "GDB Python API not available — are you running inside GDB?"
            )
        return mod

    def _gdb_error(self, gdb_mod):
        """Return the ``gdb.error`` exception class, falling back to Exception."""
        return getattr(gdb_mod, "error", Exception)

    # ------------------------------------------------------------------
    # Frame walking (default implementation)
    # ------------------------------------------------------------------

    def _walk_frames(self, max_frames=_MAX_FRAMES):
        """Walk the GDB frame chain and return a structured frame list.

        Each dict contains:

            function   – symbol name or ``"<unknown>"``
            address    – PC as hex string
            file       – source file (may be empty)
            line       – source line number (0 if unknown)
            confidence – ``"high"`` / ``"medium"`` / ``"low"``
        """
        gdb = self._get_gdb()
        frames: list[dict] = []
        try:
            frame = gdb.selected_frame()
        except self._gdb_error(gdb):
            return frames

        for _ in range(max_frames):
            if frame is None:
                break
            try:
                name = frame.name()
            except self._gdb_error(gdb):
                name = None

            try:
                sal = frame.find_sal()
                filename = sal.symtab.filename if sal.symtab else ""
                line = sal.line if sal.line else 0
            except self._gdb_error(gdb):
                filename = ""
                line = 0

            try:
                pc = frame.pc()
                address = f"0x{pc:08x}"
            except self._gdb_error(gdb):
                address = "0x00000000"

            if name:
                confidence = "high"
            elif address != "0x00000000":
                confidence = "medium"
            else:
                confidence = "low"

            frames.append(
                {
                    "function": name or "<unknown>",
                    "address": address,
                    "file": filename,
                    "line": line,
                    "confidence": confidence,
                }
            )

            try:
                frame = frame.older()
            except self._gdb_error(gdb):
                break

        return frames

    # ------------------------------------------------------------------
    # Abstract interface (safe defaults)
    # ------------------------------------------------------------------

    def get_stack_trace(self) -> list[dict]:
        """Return a list of stack frame dicts.

        Each dict should contain at least::

            {"function": "main", "address": "0x08001234", "file": "main.c", "line": 42}
        """
        return []

    def resolve_symbol(self, addr: str) -> str | None:
        """Resolve an address to a symbol name, or None."""
        return None

    def get_local_variables(self) -> dict:
        """Return local variables in the current frame.

        Keys are variable names, values are dicts::

            {"type": "int", "value": "42", "is_param": False}
        """
        return {}

    def get_metadata(self) -> dict:
        """Return target metadata (firmware version, board name, etc.)."""
        return {}
