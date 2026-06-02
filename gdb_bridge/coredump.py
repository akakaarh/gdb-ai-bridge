"""ELF core dump builder — pure Python, zero dependencies.

Generates standard ELF core dump files that GDB can open directly.
Supports ARM32 (Cortex-M/A) and AArch64.
"""

from dataclasses import dataclass, field
import struct

# ─── ELF constants ──────────────────────────────────────────────────

ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFCLASS64 = 2
ELFDATA2LSB = 1
ET_CORE = 4
EM_ARM = 40
EM_AARCH64 = 183
PT_NOTE = 4
PT_LOAD = 1
NT_PRSTATUS = 1
NT_PRPSINFO = 3

# ARM32 register order — matches GDB arm-linux-tdep.c arm_linux_gregmap
ARM32_REG_ORDER = [
    "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
    "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc", "cpsr",
]

# AArch64 register order — matches GDB aarch64-linux-tdep.c
ARM64_REG_ORDER = [f"x{i}" for i in range(31)] + ["sp", "pc", "pstate"]

# ELF struct formats
ELF32_EHDR = "<16sHHIIIIIHHHHHH"  # 52 bytes
ELF32_PHDR = "<IIIIIIII"          # 32 bytes
ELF64_EHDR = "<16sHHIQQQIHHHHHH"  # 64 bytes
ELF64_PHDR = "<IIQQQQQQ"          # 56 bytes


@dataclass
class MemoryRegion:
    """A contiguous memory region to include in the core dump."""
    name: str
    vaddr: int
    data: bytes


class ELFCoreDumpBuilder:
    """Builds standard ELF core dump files.

    Usage:
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers({"r0": 0x1234, "sp": 0x20004000, ...})
        builder.add_memory_region("stack", 0x20003F00, stack_bytes)
        builder.build("core.dump")
    """

    def __init__(self, arch: str = "arm"):
        self.arch = arch
        self.regions: list[MemoryRegion] = []
        self.registers: dict[str, int] = {}

    def add_memory_region(self, name: str, vaddr: int, data: bytes):
        self.regions.append(MemoryRegion(name=name, vaddr=vaddr, data=data))

    def set_registers(self, reg_dict: dict[str, int]):
        self.registers = dict(reg_dict)

    def build(self, output_path: str):
        if self.arch == "arm64":
            self._build_elf64(output_path)
        else:
            self._build_elf32(output_path)

    # ─── 32-bit ELF ─────────────────────────────────────────────────

    def _build_elf32(self, output_path: str):
        phdr_count = 1 + len(self.regions)  # PT_NOTE + PT_LOAD×N
        ehdr_size = struct.calcsize(ELF32_EHDR)
        phdr_size = struct.calcsize(ELF32_PHDR)
        phdr_table_size = phdr_size * phdr_count

        note_data = self._build_note_data_32()
        # Align note data to 4 bytes
        note_data = note_data + b"\x00" * ((4 - len(note_data) % 4) % 4)

        note_offset = ehdr_size + phdr_table_size
        region_base = note_offset + len(note_data)

        # Build program headers
        phdrs = bytearray()
        # PT_NOTE
        phdrs += struct.pack(ELF32_PHDR,
                             PT_NOTE, note_offset, note_offset, 0,
                             len(note_data), len(note_data), 0, 4)

        # PT_LOAD for each region
        current_offset = region_base
        for region in self.regions:
            data_len = len(region.data)
            phdrs += struct.pack(ELF32_PHDR,
                                 PT_LOAD, current_offset, region.vaddr, 0,
                                 data_len, data_len, 7, 4)  # flags: RWX
            current_offset += data_len

        # ELF header
        e_ident = ELF_MAGIC + bytes([ELFCLASS32, ELFDATA2LSB, 1, 0, 0] + [0] * 7)
        ehdr = struct.pack(ELF32_EHDR,
                           e_ident, ET_CORE, EM_ARM, 1,
                           0, ehdr_size, 0,
                           0, ehdr_size, phdr_size, phdr_count,
                           0, 0, 0)

        with open(output_path, "wb") as f:
            f.write(ehdr)
            f.write(phdrs)
            f.write(note_data)
            for region in self.regions:
                f.write(region.data)

    # ─── 64-bit ELF ─────────────────────────────────────────────────

    def _build_elf64(self, output_path: str):
        phdr_count = 1 + len(self.regions)
        ehdr_size = struct.calcsize(ELF64_EHDR)
        phdr_size = struct.calcsize(ELF64_PHDR)
        phdr_table_size = phdr_size * phdr_count

        note_data = self._build_note_data_64()
        note_data = note_data + b"\x00" * ((4 - len(note_data) % 4) % 4)

        note_offset = ehdr_size + phdr_table_size
        region_base = note_offset + len(note_data)

        phdrs = bytearray()
        # PT_NOTE
        phdrs += struct.pack(ELF64_PHDR,
                             PT_NOTE, 0, note_offset, 0,
                             len(note_data), len(note_data), 0, 4)

        current_offset = region_base
        for region in self.regions:
            data_len = len(region.data)
            phdrs += struct.pack(ELF64_PHDR,
                                 PT_LOAD, 0, current_offset, region.vaddr,
                                 data_len, data_len, 7, 4)
            current_offset += data_len

        e_ident = ELF_MAGIC + bytes([ELFCLASS64, ELFDATA2LSB, 1, 0, 0] + [0] * 7)
        ehdr = struct.pack(ELF64_EHDR,
                           e_ident, ET_CORE, EM_AARCH64, 1,
                           0, ehdr_size, 0,
                           0, ehdr_size, phdr_size, phdr_count,
                           0, 0, 0)

        with open(output_path, "wb") as f:
            f.write(ehdr)
            f.write(phdrs)
            f.write(note_data)
            for region in self.regions:
                f.write(region.data)

    # ─── Note builders ───────────────────────────────────────────────

    def _build_note_data_32(self) -> bytes:
        """Build NT_PRSTATUS + NT_PRPSINFO for ARM32."""
        # NT_PRSTATUS: signal(4) + 17 regs × 4B = 72 bytes
        sig = 0  # SIGSEGV default
        reg_values = []
        for name in ARM32_REG_ORDER:
            reg_values.append(self.registers.get(name, 0))
        prstatus_desc = struct.pack("<i", sig) + struct.pack(f"<{len(reg_values)}I", *reg_values)

        # NT_PRPSINFO: minimal — fname(16) + flags(4) + pid(4) = 24 bytes
        fname = b"firmware\x00" + b"\x00" * 7  # 16 bytes
        prpsinfo_desc = fname + struct.pack("<II", 0, 0)

        note_data = bytearray()
        note_data += self._make_note(NT_PRSTATUS, b"CORE", prstatus_desc)
        note_data += self._make_note(NT_PRPSINFO, b"CORE", prpsinfo_desc)
        return bytes(note_data)

    def _build_note_data_64(self) -> bytes:
        """Build NT_PRSTATUS + NT_PRPSINFO for AArch64."""
        sig = 0
        reg_values = []
        for name in ARM64_REG_ORDER:
            reg_values.append(self.registers.get(name, 0))
        # 4 bytes signal + padding + 34 regs × 8B
        prstatus_desc = struct.pack("<i", sig) + b"\x00" * 4 + struct.pack(f"<{len(reg_values)}Q", *reg_values)

        fname = b"firmware\x00" + b"\x00" * 7
        prpsinfo_desc = fname + struct.pack("<II", 0, 0)

        note_data = bytearray()
        note_data += self._make_note(NT_PRSTATUS, b"CORE", prstatus_desc)
        note_data += self._make_note(NT_PRPSINFO, b"CORE", prpsinfo_desc)
        return bytes(note_data)

    @staticmethod
    def _make_note(note_type: int, name: bytes, desc: bytes) -> bytes:
        """Build a single ELF note entry (always 4-byte aligned)."""
        name_padded = name + b"\x00" * ((4 - len(name) % 4) % 4)
        desc_padded = desc + b"\x00" * ((4 - len(desc) % 4) % 4)
        header = struct.pack("<III", len(name), len(desc), note_type)
        return header + name_padded + desc_padded
