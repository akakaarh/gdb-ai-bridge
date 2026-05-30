"""Bare-metal target adapter (ARM Cortex-M, RISC-V, etc. via JTAG/SWD).

Uses the GDB Python API directly to walk frames, resolve symbols,
and read local variables.

The real GDB ``gdb`` built-in module is accessed via ``sys.modules``
at **call time** (not import time) to avoid conflicts with our own
``gdb`` package that shadows it on ``sys.path``.
"""

from __future__ import annotations

import sys

from .base import TargetAdapter

# Maximum number of frames to traverse to avoid runaway on corrupt stacks.
_MAX_FRAMES = 20


def _get_gdb():
    """Return the GDB Python API module, or raise if unavailable."""
    mod = sys.modules.get("gdb")
    if mod is None or not hasattr(mod, "selected_frame"):
        raise RuntimeError(
            "GDB Python API not available — are you running inside GDB?"
        )
    return mod


def _gdb_error(gdb_mod):
    """Return the gdb.error exception class, falling back to Exception."""
    return getattr(gdb_mod, "error", Exception)


class BaremetalAdapter(TargetAdapter):
    """Adapter for bare-metal targets connected via JTAG/SWD."""

    name = "baremetal"

    # ------------------------------------------------------------------
    # Stack trace
    # ------------------------------------------------------------------

    def get_stack_trace(self) -> list[dict]:
        """Walk the frame chain and collect one dict per frame.

        Each dict contains:
            function  – symbol name or "<unknown>"
            address   – PC as hex string
            file      – source file (may be empty)
            line      – source line number (0 if unknown)
            confidence – "high" / "medium" / "low"
        """
        gdb = _get_gdb()
        frames: list[dict] = []
        try:
            frame = gdb.selected_frame()
        except _gdb_error(gdb):
            return frames

        for _ in range(_MAX_FRAMES):
            if frame is None:
                break
            try:
                name = frame.name()
            except _gdb_error(gdb):
                name = None

            try:
                sal = frame.find_sal()  # Symtab and line
                filename = sal.symtab.filename if sal.symtab else ""
                line = sal.line if sal.line else 0
            except _gdb_error(gdb):
                filename = ""
                line = 0

            try:
                pc = frame.pc()
                address = f"0x{pc:08x}"
            except _gdb_error(gdb):
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
            except _gdb_error(gdb):
                break

        return frames

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    def resolve_symbol(self, addr: str) -> str | None:
        """Resolve *addr* to a symbol via ``info symbol``.

        Returns the symbol string (e.g. ``"main + 4"``) or None.
        """
        gdb = _get_gdb()
        try:
            output = gdb.execute(f"info symbol {addr}", to_string=True)
        except _gdb_error(gdb):
            return None
        output = output.strip()
        if not output or "No symbol matches" in output:
            return None
        return output

    # ------------------------------------------------------------------
    # Local variables
    # ------------------------------------------------------------------

    def get_local_variables(self) -> dict:
        """Read local variables from the selected frame's innermost block.

        Returns ``{name: {"type": ..., "value": ..., "is_param": bool}}``.
        """
        gdb = _get_gdb()
        result: dict = {}
        try:
            frame = gdb.selected_frame()
            block = frame.block()
        except _gdb_error(gdb):
            return result

        for symbol in block:
            if symbol.is_variable or symbol.is_argument:
                try:
                    val = symbol.value(frame)
                    type_str = str(val.type)
                    value_str = str(val)
                except _gdb_error(gdb):
                    type_str = str(symbol.type) if symbol.type else "<unknown>"
                    value_str = "<unavailable>"

                result[symbol.name] = {
                    "type": type_str,
                    "value": value_str,
                    "is_param": symbol.is_argument,
                }

        return result
