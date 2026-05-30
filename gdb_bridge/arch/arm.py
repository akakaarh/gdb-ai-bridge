"""ARM Cortex-M architecture adapter.

Handles register reading, fault register decoding (SCB block),
exception frame extraction, and crash analysis for Cortex-M targets.
"""

from .base import ArchAdapter

try:
    import gdb as _gdb
except ImportError:
    _gdb = None


# SCB (System Control Block) register addresses for Cortex-M
_SCB_BASE = 0xE000ED00
_SCB_REGS = {
    "cpuid": _SCB_BASE + 0x00,
    "cfsr": _SCB_BASE + 0x28,
    "hfsr": _SCB_BASE + 0x2C,
    "mmfar": _SCB_BASE + 0x34,
    "bfar": _SCB_BASE + 0x38,
}

# CFSR bit definitions
_CFSR_BITS = {
    # MemManage (MM) faults - bits [7:0]
    0: ("IACCVIOL", "Instruction access violation"),
    1: ("DACCVIOL", "Data access violation"),
    3: ("MUNSTKERR", "MemManage fault on unstacking"),
    4: ("MSTKERR", "MemManage fault on stacking"),
    5: ("MLSPERR", "MemManage fault on lazy state preservation"),
    7: ("MMARVALID", "MemManage Address Register valid"),
    # Bus Fault (BF) - bits [15:8]
    8: ("IBUSERR", "Instruction bus error"),
    9: ("PRECISERR", "Precise data bus error"),
    10: ("IMPRECISERR", "Imprecise data bus error"),
    11: ("UNSTKERR", "BusFault on unstacking"),
    12: ("STKERR", "BusFault on stacking"),
    13: ("LSPERR", "BusFault on lazy state preservation"),
    15: ("BFARVALID", "BusFault Address Register valid"),
    # Usage Fault (UF) - bits [25:16]
    16: ("UNDEFINSTR", "Undefined instruction"),
    17: ("INVSTATE", "Invalid state (Thumb bit)"),
    18: ("INVPC", "Invalid PC load"),
    19: ("NOCP", "No coprocessor"),
    24: ("UNALIGNED", "Unaligned access"),
    25: ("DIVBYZERO", "Divide by zero"),
}

# High-level crash type mapping from CFSR bits to crash names
_CRASH_TYPE_MAP = {
    0: "MemManage",
    1: "MemManage",
    3: "MemManage",
    4: "MemManage",
    5: "MemManage",
    8: "BusFault",
    9: "BusFault",
    10: "BusFault",
    11: "BusFault",
    12: "BusFault",
    13: "BusFault",
    16: "UsageFault",
    17: "UsageFault",
    18: "UsageFault",
    19: "UsageFault",
    24: "UsageFault",
    25: "UsageFault",
}


def _read_reg32(name: str) -> str | None:
    """Read a 32-bit register via GDB, returning hex string or None."""
    if _gdb is None:
        return None
    try:
        val = _gdb.parse_and_eval(f"${name}")
        return f"0x{int(val):08x}"
    except Exception:
        return None


def _read_memory32(addr: int) -> int | None:
    """Read a 32-bit value from target memory via GDB, returning int or None."""
    if _gdb is None:
        return None
    try:
        frame = _gdb.selected_frame()
        mem = frame.read_memory(addr, 4)
        # gdb.MemoryView -> bytes
        data = bytes(mem)
        return int.from_bytes(data, byteorder="little")
    except Exception:
        return None


class ArmAdapter(ArchAdapter):
    """ARM Cortex-M (ARMv7-M / ARMv8-M) architecture adapter."""

    name = "arm"

    # -- register reading --------------------------------------------------

    def get_registers(self) -> dict:
        """Read R0-R15 and xPSR, returning hex string values."""
        if _gdb is None:
            return {}
        regs = {}
        for i in range(16):
            name = f"r{i}"
            val = _read_reg32(name)
            if val is not None:
                regs[name] = val
        val = _read_reg32("xpsr")
        if val is not None:
            regs["xpsr"] = val
        return regs

    # -- fault registers (SCB) ---------------------------------------------

    def get_fault_registers(self) -> dict:
        """Read SCB fault registers (CFSR, HFSR, MMFAR, BFAR, CPUID)."""
        result = {}
        for name, addr in _SCB_REGS.items():
            val = _read_memory32(addr)
            if val is not None:
                result[name] = f"0x{val:08x}"
        return result

    # -- exception frame ---------------------------------------------------

    def get_exception_frame(self) -> dict:
        """Read the 8-word exception frame from the current SP (MSP).

        On Cortex-M exception entry the hardware pushes:
        R0, R1, R2, R3, R12, LR, PC, xPSR
        """
        if _gdb is None:
            return {}
        try:
            sp = int(_gdb.parse_and_eval("$sp"))
        except Exception:
            return {}

        names = ["r0", "r1", "r2", "r3", "r12", "lr", "pc", "xpsr"]
        frame = {}
        for i, reg_name in enumerate(names):
            val = _read_memory32(sp + i * 4)
            if val is not None:
                frame[reg_name] = f"0x{val:08x}"
        return frame

    # -- crash decoding ----------------------------------------------------

    def decode_crash(self, fault_regs: dict) -> tuple[str, str]:
        """Decode CFSR/HFSR into (crash_type, human_readable_reason)."""
        if not fault_regs:
            return ("unknown", "No fault registers available")

        # Parse HFSR first for special conditions
        hfsr_str = fault_regs.get("hfsr")
        if hfsr_str:
            hfsr = int(hfsr_str, 16)
            if hfsr & (1 << 30):
                return ("DebugEvt", "Debug event (halted by debugger or vector catch)")
            if hfsr & (1 << 1):
                return ("HardFault", "BusFault escalated to HardFault")

        # Parse CFSR for detailed fault info
        cfsr_str = fault_regs.get("cfsr")
        if not cfsr_str:
            return ("HardFault", "HardFault with no CFSR detail")

        cfsr = int(cfsr_str, 16)
        if cfsr == 0:
            return ("HardFault", "HardFault with no CFSR bits set (likely a forced HardFault)")

        # Find the lowest set bit to identify primary fault
        reasons = []
        primary_crash = "HardFault"
        primary_set = False
        for bit_pos in sorted(_CFSR_BITS.keys()):
            if cfsr & (1 << bit_pos):
                name, desc = _CFSR_BITS[bit_pos]
                reasons.append(f"{name}: {desc}")
                # Use the first (lowest) active fault as primary crash type
                if not primary_set and bit_pos != 7 and bit_pos != 15:
                    primary_crash = _CRASH_TYPE_MAP.get(bit_pos, "HardFault")
                    primary_set = True

        # Attach fault address if available
        if cfsr & (1 << 7) and "mmfar" in fault_regs:
            reasons.append(f"MMFAR={fault_regs['mmfar']}")
        if cfsr & (1 << 15) and "bfar" in fault_regs:
            reasons.append(f"BFAR={fault_regs['bfar']}")

        # Check for escalation
        if cfsr & (1 << 25) or cfsr & (1 << 24):
            # FORCED bit or usage fault with other active faults
            pass  # Keep the decoded crash type

        reason = "; ".join(reasons) if reasons else "Unknown CFSR state"
        return (primary_crash, reason)

    # -- register annotation -----------------------------------------------

    _ROLE_MAP = {
        "r0": "arg0/retval",
        "r1": "arg1",
        "r2": "arg2",
        "r3": "arg3",
        "r4": "callee-saved",
        "r5": "callee-saved",
        "r6": "callee-saved",
        "r7": "FP (Thumb)",
        "r8": "callee-saved",
        "r9": "callee-saved (platform)",
        "r10": "callee-saved",
        "r11": "FP (ARM)",
        "r12": "IP (Intra-Procedure-call scratch)",
        "r13": "SP",
        "r14": "LR",
        "r15": "PC",
        "sp": "Stack Pointer",
        "lr": "Link Register",
        "pc": "Program Counter",
        "xpsr": "Program Status Register",
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
