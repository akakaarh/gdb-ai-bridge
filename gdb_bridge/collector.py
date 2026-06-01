"""Layered debug context collector with graceful degradation."""
import json
from datetime import datetime

from gdb_bridge.svd import SVDParser, RegisterDecoder


# Cortex-M SCB (System Control Block) fault registers
_SCB_BASE = 0xE000ED00
_SCB_FAULT_REGISTERS = [
    _SCB_BASE + 0x28,   # CFSR  (Configurable Fault Status Register)
    _SCB_BASE + 0x2C,   # HFSR  (HardFault Status Register)
    _SCB_BASE + 0x34,   # MMFAR (MemManage Fault Address Register)
    _SCB_BASE + 0x38,   # BFAR  (Bus Fault Address Register)
]


def _read_mem32_default(address: int) -> int:
    """Read a 32-bit value from memory via GDB. Returns 0 on failure.

    This is a fallback for when the Collector is used standalone.
    When running inside gdb_bridge, the module-level _read_mem32 from
    gdb_bridge.gdb_bridge is preferred.
    """
    try:
        import gdb
        frame = gdb.selected_frame()
        mem = frame.read_memory(address, 4)
        return int(mem.cast(gdb.lookup_type("uint32_t")))
    except Exception:
        return 0


class DebugContext:
    def __init__(self):
        self.version = "1.0"
        self.timestamp = ""
        self.config = {}
        self.layer0 = {}
        self.layer1 = {}
        self.layer2 = {}
        self.errors = []
        self.decoded_registers = ""

    def to_dict(self):
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "config": self.config,
            "layer0": self.layer0,
            "layer1": self.layer1,
            "layer2": self.layer2,
            "errors": self.errors,
            "decoded_registers": self.decoded_registers,
        }


# Known safe memory regions for common STM32 chips
# (start, end, description) — end is exclusive
STM32MP1_M4_REGIONS = [
    (0x10000000, 0x10060000, "MCU SRAM (384KB)"),
    (0x00000000, 0x00200000, "MCU Flash alias"),
]

STM32MP1_A7_REGIONS = [
    (0xC0000000, 0xFFFFFFFF, "DDR (1GB typical)"),
    (0x2FFC0000, 0x30000000, "SRAM (256KB)"),
]

# MMIO regions to NEVER read (read side-effects)
KNOWN_MMIO_REGIONS = [
    (0x40000000, 0x60000000, "APB/AHB peripherals"),
    (0xE0000000, 0xE0100000, "CoreSight/SCB (OK for fault regs, NOT for bulk read)"),
]


class Collector:
    def __init__(self, arch, target, config=None, svd_path=None, read_mem32=None):
        self.arch = arch      # ArchAdapter instance
        self.target = target  # TargetAdapter instance
        self.config = config or {}
        self.safe_regions = []
        self._svd_parser = None
        self._register_decoder = None
        self._read_mem32 = read_mem32 or _read_mem32_default
        if svd_path is not None:
            self._svd_parser = SVDParser(svd_path)
            self._register_decoder = RegisterDecoder(self._svd_parser)

    def collect(self, full_dump=False):
        ctx = DebugContext()
        ctx.timestamp = datetime.now().isoformat()
        ctx.config = {
            "arch": self.arch.name,
            "target": self.target.name,
            "elf_file": self.config.get("elf_file", ""),
        }

        ctx.layer0 = self._collect_layer0()
        ctx.layer1 = self._collect_layer1()

        if full_dump:
            ctx.layer2 = self._collect_layer2()

        # Auto-decode SCB fault registers when SVD is loaded
        if self._register_decoder is not None:
            ctx.decoded_registers = self._decode_scb_registers()

        return ctx

    def _collect_layer0(self):
        """Layer 0: always safe (<1ms). Registers + fault + source."""
        result = {"status": "ok"}

        try:
            regs = self.arch.get_registers()
            result["registers"] = self.arch.annotate_registers(regs)
        except Exception as e:
            result["registers"] = {"status": "error", "error": str(e)}

        try:
            fault = self.arch.get_fault_registers()
            crash_type, crash_reason = self.arch.decode_crash(fault)
            result["fault_registers"] = fault
            result["crash_type"] = crash_type
            result["crash_reason"] = crash_reason
        except Exception as e:
            result["fault_registers"] = {"status": "error", "error": str(e)}

        return result

    def _collect_layer1(self):
        """Layer 1: needs SP verification (<10ms)."""
        result = {"status": "ok"}

        try:
            result["exception_frame"] = self.arch.get_exception_frame()
        except Exception as e:
            result["exception_frame"] = {"status": "error", "error": str(e)}

        try:
            result["stack_trace"] = self.target.get_stack_trace()
        except Exception as e:
            result["stack_trace"] = {"status": "error", "error": str(e)}

        try:
            result["local_variables"] = self.target.get_local_variables()
        except Exception as e:
            result["local_variables"] = {"status": "error", "error": str(e)}

        return result

    def _collect_layer2(self):
        """Layer 2: user-triggered, large dumps."""
        return {"status": "not_implemented"}

    def set_safe_regions(self, regions):
        """Set safe memory regions for bulk reads.
        regions: list of (start, end) tuples.
        """
        self.safe_regions = regions

    def load_safe_regions_from_elf(self, elf_path):
        """Parse ELF LOAD segments to determine safe memory regions."""
        try:
            import subprocess
            result = subprocess.run(
                ["arm-none-eabi-readelf", "-l", elf_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return
            for line in result.stdout.splitlines():
                if "LOAD" in line:
                    parts = line.split()
                    # Format: LOAD phys_addr virt_addr file_size mem_size ...
                    if len(parts) >= 6:
                        try:
                            vaddr = int(parts[2], 16)
                            memsz = int(parts[5], 16)
                            if vaddr > 0 and memsz > 0:
                                self.safe_regions.append((vaddr, vaddr + memsz))
                        except ValueError:
                            pass
        except Exception:
            pass

    def _is_safe_address(self, addr, size=4):
        """Check if address range is safe to read (not MMIO)."""
        if not self.safe_regions:
            return True  # No regions configured, allow all

        for start, end in self.safe_regions:
            if start <= addr and addr + size <= end:
                return True
        return False

    def read_memory_safe(self, addr, size=4):
        """Read memory safely, avoiding MMIO regions.
        Returns bytes or None if unsafe/unreadable.
        """
        if not self._is_safe_address(addr, size):
            return None
        try:
            import gdb
            frame = gdb.selected_frame()
            return frame.read_memory(addr, size)
        except Exception:
            return None

    def decode_peripheral(self, address: int, count: int = 1) -> str:
        """Decode peripheral register(s) at the given address.

        Args:
            address: Absolute memory address of the register.
            count: Number of consecutive 32-bit registers to decode.

        Returns:
            Human-readable decode string, or error message if SVD not loaded.
        """
        if self._register_decoder is None:
            return "No SVD file loaded. Pass svd_path to Collector.__init__."

        results = []
        for i in range(count):
            addr = address + i * 4
            value = self._read_register_value(addr)
            results.append(self._register_decoder.decode(addr, value))
        return "\n".join(results)

    def decode_peripherals(self, addresses: list[int]) -> dict[int, str]:
        """Decode multiple peripheral registers at the given addresses.

        Args:
            addresses: List of absolute memory addresses.

        Returns:
            Dict mapping address to decode string.
        """
        if self._register_decoder is None:
            error = "No SVD file loaded. Pass svd_path to Collector.__init__."
            return {addr: error for addr in addresses}

        result = {}
        for addr in addresses:
            value = self._read_register_value(addr)
            result[addr] = self._register_decoder.decode(addr, value)
        return result

    def _read_register_value(self, address: int) -> int:
        """Read a 32-bit register value. Returns 0 on failure."""
        return self._read_mem32(address)

    def _decode_scb_registers(self) -> str:
        """Decode Cortex-M SCB fault registers (CFSR, HFSR, MMFAR, BFAR).

        Returns a multi-line decode string, or empty string on failure.
        """
        try:
            lines = []
            for addr in _SCB_FAULT_REGISTERS:
                value = self._read_mem32(addr)
                decoded = self._register_decoder.decode(addr, value)
                lines.append(decoded)
            return "\n".join(lines)
        except Exception:
            return ""
