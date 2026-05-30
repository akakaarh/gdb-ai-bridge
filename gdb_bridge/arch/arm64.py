"""ARM64 (AArch64) architecture adapter.

Handles register reading, annotation, and basic crash support for
64-bit ARM targets (ARMv8-A / ARMv9-A).
"""

from .base import ArchAdapter

try:
    import gdb as _gdb
except ImportError:
    _gdb = None


def _read_reg64(name: str) -> str | None:
    """Read a 64-bit register via GDB, returning hex string or None."""
    if _gdb is None:
        return None
    try:
        val = _gdb.parse_and_eval(f"${name}")
        return f"0x{int(val):016x}"
    except Exception:
        return None


class Arm64Adapter(ArchAdapter):
    """ARM64 (AArch64) architecture adapter."""

    name = "arm64"

    # -- register reading --------------------------------------------------

    def get_registers(self) -> dict:
        """Read X0-X30, SP, PC, and PSTATE."""
        if _gdb is None:
            return {}
        regs = {}
        # X0-X30: general-purpose 64-bit registers
        for i in range(31):
            name = f"x{i}"
            val = _read_reg64(name)
            if val is not None:
                regs[name] = val
        # Special registers
        for name in ("sp", "pc", "pstate"):
            val = _read_reg64(name)
            if val is not None:
                regs[name] = val
        return regs

    # -- register annotation -----------------------------------------------

    _ROLE_MAP = {
        "x0": "arg0/retval",
        "x1": "arg1",
        "x2": "arg2",
        "x3": "arg3",
        "x4": "arg4",
        "x5": "arg5",
        "x6": "arg6",
        "x7": "arg7",
        "x8": "syscall number / indirect result",
        "x9": "scratch",
        "x10": "scratch",
        "x11": "scratch",
        "x12": "scratch",
        "x13": "scratch",
        "x14": "scratch",
        "x15": "scratch (IP0)",
        "x16": "scratch (IP1 / PLT)",
        "x17": "scratch (IP1 / PLT)",
        "x18": "platform register (reserved)",
        "x19": "callee-saved",
        "x20": "callee-saved",
        "x21": "callee-saved",
        "x22": "callee-saved",
        "x23": "callee-saved",
        "x24": "callee-saved",
        "x25": "callee-saved",
        "x26": "callee-saved",
        "x27": "callee-saved",
        "x28": "callee-saved",
        "x29": "FP (Frame Pointer)",
        "x30": "LR (Link Register)",
        "sp": "Stack Pointer",
        "pc": "Program Counter",
        "pstate": "Processor State",
    }

    def annotate_registers(self, regs: dict) -> dict:
        """Annotate each register with its architectural role."""
        result = {}
        for name, value in regs.items():
            role = self._ROLE_MAP.get(name, "")
            entry = {"value": value}
            if role:
                entry["role"] = role
            result[name] = entry
        return result
