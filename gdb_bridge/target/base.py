"""Base class for target-specific adapters.

A *target* represents the connection to the debuggee (bare-metal via
JTAG/SWD, QEMU, Linux userspace process, etc.).  Subclass and override
the methods you need.
"""


class TargetAdapter:
    """Abstract target adapter.  All methods return safe empty defaults."""

    name = ""

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
