"""Layered debug context collector with graceful degradation."""
import json
from datetime import datetime


class DebugContext:
    def __init__(self):
        self.version = "1.0"
        self.timestamp = ""
        self.config = {}
        self.layer0 = {}
        self.layer1 = {}
        self.layer2 = {}
        self.errors = []

    def to_dict(self):
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "config": self.config,
            "layer0": self.layer0,
            "layer1": self.layer1,
            "layer2": self.layer2,
            "errors": self.errors,
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
    def __init__(self, arch, target, config=None):
        self.arch = arch      # ArchAdapter instance
        self.target = target  # TargetAdapter instance
        self.config = config or {}
        self.safe_regions = []

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
