"""Base class for architecture-specific adapters.

Each architecture (ARM Cortex-M, ARM64, RISC-V, etc.) should subclass
ArchAdapter and override the methods it supports.  The collector calls
these methods inside try/except so that partial failures degrade
gracefully.
"""


class ArchAdapter:
    """Abstract architecture adapter.  All methods return safe empty defaults."""

    name = ""

    def get_registers(self) -> dict:
        """Return a dict of register name -> hex string value."""
        return {}

    def get_fault_registers(self) -> dict:
        """Return fault-specific registers (e.g. CFSR, HFSR for Cortex-M)."""
        return {}

    def get_exception_frame(self) -> dict:
        """Return the exception/interrupt frame pushed by hardware."""
        return {}

    def decode_crash(self, fault_regs: dict) -> tuple[str, str]:
        """Decode fault registers into (crash_type, human_readable_reason).

        Example return:
            ("HardFault", "Instruction bus fault at 0x08001234")
        """
        return ("unknown", "")

    def annotate_registers(self, regs: dict) -> dict:
        """Return a dict of register name -> annotated dict.

        Each value should be::

            {"value": "0x20004000", "role": "Stack Pointer"}

        The default implementation passes values through without
        annotation.
        """
        return {k: {"value": v} for k, v in regs.items()}
