"""Bare-metal target adapter (ARM Cortex-M, RISC-V, etc. via JTAG/SWD).

Uses the GDB Python API directly to walk frames, resolve symbols,
and read local variables.

The real GDB ``gdb`` built-in module is accessed via ``sys.modules``
at **call time** (not import time) to avoid conflicts with our own
``gdb`` package that shadows it on ``sys.path``.
"""

from __future__ import annotations

from .base import TargetAdapter


class BaremetalAdapter(TargetAdapter):
    """Adapter for bare-metal targets connected via JTAG/SWD."""

    name = "baremetal"

    # ------------------------------------------------------------------
    # Stack trace
    # ------------------------------------------------------------------

    def get_stack_trace(self) -> list[dict]:
        """Walk the frame chain and collect one dict per frame.

        Delegates to the base class ``_walk_frames()`` implementation.
        """
        return self._walk_frames()

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    def resolve_symbol(self, addr: str) -> str | None:
        """Resolve *addr* to a symbol via ``info symbol``.

        Returns the symbol string (e.g. ``"main + 4"``) or None.
        """
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
    # Local variables
    # ------------------------------------------------------------------

    def get_local_variables(self) -> dict:
        """Read local variables from the selected frame's innermost block.

        Returns ``{name: {"type": ..., "value": ..., "is_param": bool}}``.
        """
        gdb = self._get_gdb()
        result: dict = {}
        try:
            frame = gdb.selected_frame()
            block = frame.block()
        except self._gdb_error(gdb):
            return result

        for symbol in block:
            if symbol.is_variable or symbol.is_argument:
                try:
                    val = symbol.value(frame)
                    type_str = str(val.type)
                    value_str = str(val)
                except self._gdb_error(gdb):
                    type_str = str(symbol.type) if symbol.type else "<unknown>"
                    value_str = "<unavailable>"

                result[symbol.name] = {
                    "type": type_str,
                    "value": value_str,
                    "is_param": symbol.is_argument,
                }

        return result
