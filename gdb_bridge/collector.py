"""Layered debug context collector with graceful degradation."""
import json
import re
from datetime import datetime

from gdb_bridge.coredump import ELFCoreDumpBuilder, MemoryRegion
from gdb_bridge.svd import SVDParser, RegisterDecoder
from gdb_bridge.freertos import FreeRTOSParser, tasks_to_dicts


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
        self.tasks = []  # list of TaskInfo dicts (from FreeRTOS)

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
            "tasks": self.tasks,
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
            try:
                from datetime import datetime as dt
                ts = dt.now().strftime("%Y%m%d_%H%M%S")
                output_path = self.config.get("dump_path", f"core_{ts}.dump")
                ctx.layer2 = self._collect_layer2(output_path)
            except Exception as e:
                ctx.layer2 = {"status": "error", "error": str(e)}

        # Auto-decode SCB fault registers when SVD is loaded
        if self._register_decoder is not None:
            ctx.decoded_registers = self._decode_scb_registers()

        # Collect FreeRTOS task list if available
        ctx.tasks = self._collect_freertos_tasks()

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

    def _collect_layer2(self, output_path, dump_all=False, max_size=64*1024*1024):
        """Dump memory regions to ELF core dump.

        Args:
            output_path: Path to write the ELF core dump.
            dump_all: If True, dump all safe regions. If False (default),
                      dump stack + .data/.bss segments.
            max_size: Maximum total bytes to dump (default 64MB).
                      Only enforced when dump_all=True.

        Returns:
            dict with status, file path, and region count.
        """
        regions = []

        if dump_all:
            total = sum(end - start for start, end in self.safe_regions)
            if total > max_size:
                return {
                    "status": "error",
                    "reason": f"Total size {total} exceeds max {max_size}",
                }
            for start, end in self.safe_regions:
                data = self._read_memory_chunked(start, end - start)
                regions.append(MemoryRegion("ram", start, data))
        else:
            sp = self._get_sp()
            if sp is not None:
                stack_top = self._get_stack_top(sp)
                stack_size = stack_top - sp
                if stack_size > 0:
                    stack_data = self._read_memory_chunked(sp, stack_size)
                    regions.append(MemoryRegion("stack", sp, stack_data))

            for seg in self._get_data_segments():
                data = self._read_memory_chunked(seg["vaddr"], seg["size"])
                regions.append(MemoryRegion(seg["name"], seg["vaddr"], data))

        # Get raw register values (integers) for core dump
        raw_regs = {}
        try:
            regs = self.arch.get_registers()
            for k, v in regs.items():
                raw_regs[k] = int(v, 16) if isinstance(v, str) else v
        except Exception:
            pass

        builder = ELFCoreDumpBuilder(arch=self.arch.name)
        builder.set_registers(raw_regs)
        for r in regions:
            builder.add_memory_region(r.name, r.vaddr, r.data)
        builder.build(output_path)

        return {"status": "ok", "file": output_path, "regions": len(regions)}

    def _get_sp(self):
        """Read the stack pointer register. Returns int or None."""
        try:
            regs = self.arch.get_registers()
            sp_str = regs.get("sp", "0x0")
            return int(sp_str, 16) if isinstance(sp_str, str) else sp_str
        except Exception:
            return None

    def _get_stack_top(self, sp):
        """Find the top of the stack using a 3-level fallback.

        1. _estack symbol from the ELF file
        2. End of the SRAM region containing SP
        3. Default: SP + 8KB

        Args:
            sp: Current stack pointer value (int).

        Returns:
            Stack top address (int).
        """
        # Level 1: _estack symbol
        try:
            import gdb
            estack = int(gdb.parse_and_eval("&_estack"))
            if estack > sp:
                return estack
        except Exception:
            pass

        # Level 2: SRAM region containing SP
        for start, end in self.safe_regions:
            if start <= sp < end:
                return end

        # Level 3: default 8KB
        return sp + 8192

    def _get_data_segments(self):
        """Get .data and .bss segments from GDB 'info files'.

        Returns list of dicts: [{"name": ".data", "vaddr": 0x..., "size": 0x...}, ...]
        """
        segments = []
        try:
            import gdb
            output = gdb.execute("info files", to_string=True)
            for match in re.finditer(
                r"0x([0-9a-fA-F]+)\s+-\s+0x([0-9a-fA-F]+)\s+is\s+(\.\w+)",
                output,
            ):
                start = int(match.group(1), 16)
                end = int(match.group(2), 16)
                name = match.group(3)
                if name in (".data", ".bss"):
                    segments.append({"name": name, "vaddr": start, "size": end - start})
        except Exception:
            pass
        return segments

    def _read_memory_chunked(self, addr, size, chunk_size=4096):
        """Read memory in chunks, filling failed chunks with zeros.

        Args:
            addr: Start address.
            size: Total bytes to read.
            chunk_size: Bytes per chunk (default 4KB).

        Returns:
            bytes of length size.
        """
        result = bytearray()
        for offset in range(0, size, chunk_size):
            chunk = min(chunk_size, size - offset)
            data = self.read_memory_safe(addr + offset, chunk)
            if data is not None:
                result.extend(bytes(data))
            else:
                result.extend(b"\x00" * chunk)
        return bytes(result)

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

    def _collect_freertos_tasks(self) -> list[dict]:
        """Try to parse FreeRTOS tasks. Returns list of task dicts, or empty list."""
        try:
            parser = FreeRTOSParser(self._read_mem32)
            if not parser.detect():
                return []
            tasks = parser.parse_tasks()
            return tasks_to_dicts(tasks)
        except Exception:
            return []
