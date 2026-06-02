"""Tests for gdb_bridge/coredump.py — ELF core dump builder."""

import struct
import pytest

from gdb_bridge.coredump import (
    ELFCoreDumpBuilder, MemoryRegion,
    ELF_MAGIC, ELFCLASS32, ELFCLASS64, ET_CORE, EM_ARM, EM_AARCH64,
    PT_NOTE, PT_LOAD, NT_PRSTATUS, NT_PRPSINFO,
    ARM32_REG_ORDER, ARM64_REG_ORDER,
    ELF32_EHDR, ELF32_PHDR, ELF64_EHDR, ELF64_PHDR,
)


def _make_arm32_regs(**overrides):
    """Create a full set of ARM32 register values."""
    regs = {f"r{i}": i * 0x1000 for i in range(13)}
    regs["sp"] = 0x20004000
    regs["lr"] = 0x08001234
    regs["pc"] = 0x08001000
    regs["cpsr"] = 0x61000000
    regs.update(overrides)
    return regs


def _make_arm64_regs(**overrides):
    """Create a full set of AArch64 register values."""
    regs = {f"x{i}": i * 0x10000 for i in range(31)}
    regs["sp"] = 0x80010000
    regs["pc"] = 0x40008000
    regs["pstate"] = 0x60000000
    regs.update(overrides)
    return regs


def _read_elf32(path):
    """Read and parse a 32-bit ELF core dump file."""
    with open(path, "rb") as f:
        data = f.read()

    ehdr = struct.unpack_from(ELF32_EHDR, data, 0)
    e_ident, e_type, e_machine, e_version, e_entry, e_phoff, e_shoff, \
        e_flags, e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = ehdr

    phdrs = []
    for i in range(e_phnum):
        offset = e_phoff + i * e_phentsize
        phdr = struct.unpack_from(ELF32_PHDR, data, offset)
        phdrs.append({
            "type": phdr[0], "offset": phdr[1], "vaddr": phdr[2],
            "paddr": phdr[3], "filesz": phdr[4], "memsz": phdr[5],
            "flags": phdr[6], "align": phdr[7],
        })

    return {
        "ident": e_ident, "type": e_type, "machine": e_machine,
        "phoff": e_phoff, "phdrs": phdrs, "data": data,
    }


def _read_elf64(path):
    """Read and parse a 64-bit ELF core dump file."""
    with open(path, "rb") as f:
        data = f.read()

    ehdr = struct.unpack_from(ELF64_EHDR, data, 0)
    e_ident, e_type, e_machine, e_version, e_entry, e_phoff, e_shoff, \
        e_flags, e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = ehdr

    phdrs = []
    for i in range(e_phnum):
        offset = e_phoff + i * e_phentsize
        phdr = struct.unpack_from(ELF64_PHDR, data, offset)
        phdrs.append({
            "type": phdr[0], "flags": phdr[1], "offset": phdr[2],
            "vaddr": phdr[3], "memsz": phdr[4], "filesz": phdr[5],
            "align": phdr[6],
        })

    return {
        "ident": e_ident, "type": e_type, "machine": e_machine,
        "phoff": e_phoff, "phdrs": phdrs, "data": data,
    }


def _parse_notes(data, note_offset, note_size):
    """Parse ELF note sections from raw data."""
    notes = []
    pos = note_offset
    end = note_offset + note_size
    while pos < end:
        namesz, descsz, ntype = struct.unpack_from("<III", data, pos)
        pos += 12
        name = data[pos:pos + namesz]
        pos += namesz
        # Align to 4
        pos += (4 - pos % 4) % 4
        desc = data[pos:pos + descsz]
        pos += descsz
        pos += (4 - pos % 4) % 4
        notes.append({"type": ntype, "name": name.rstrip(b"\x00"), "desc": desc})
    return notes


# ─── ARM32 tests ────────────────────────────────────────────────────

class TestARM32CoreDump:
    """ARM32 ELF core dump generation."""

    def test_elf_magic_bytes(self, tmp_path):
        """ELF magic bytes at offset 0."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 256)
        builder.build(path)

        elf = _read_elf32(path)
        assert elf["ident"][:4] == ELF_MAGIC

    def test_elf_class_32bit(self, tmp_path):
        """32-bit ELF class field."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        assert elf["ident"][4] == ELFCLASS32

    def test_elf_type_core(self, tmp_path):
        """ELF type is ET_CORE."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        assert elf["type"] == ET_CORE

    def test_elf_machine_arm(self, tmp_path):
        """ELF machine is EM_ARM."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        assert elf["machine"] == EM_ARM

    def test_program_header_count(self, tmp_path):
        """PHDR count = 1 (PT_NOTE) + N (PT_LOAD)."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.add_memory_region(".data", 0x08000000, b"\x00" * 128)
        builder.build(path)

        elf = _read_elf32(path)
        assert len(elf["phdrs"]) == 3  # 1 PT_NOTE + 2 PT_LOAD

    def test_pt_note_present(self, tmp_path):
        """First program header is PT_NOTE."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        assert elf["phdrs"][0]["type"] == PT_NOTE

    def test_pt_load_segments(self, tmp_path):
        """PT_LOAD segments for each memory region."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\xAB" * 256)
        builder.add_memory_region(".data", 0x08000000, b"\xCD" * 128)
        builder.build(path)

        elf = _read_elf32(path)
        loads = [p for p in elf["phdrs"] if p["type"] == PT_LOAD]
        assert len(loads) == 2
        assert loads[0]["vaddr"] == 0x20003000
        assert loads[0]["memsz"] == 256
        assert loads[1]["vaddr"] == 0x08000000
        assert loads[1]["memsz"] == 128

    def test_pt_load_flags(self, tmp_path):
        """PT_LOAD segments have RWX flags."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        loads = [p for p in elf["phdrs"] if p["type"] == PT_LOAD]
        assert all(p["flags"] == 7 for p in loads)  # PF_R|PF_W|PF_X

    def test_arm32_register_count(self, tmp_path):
        """NT_PRSTATUS has 17 registers for ARM32."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        note_phdr = elf["phdrs"][0]
        notes = _parse_notes(elf["data"], note_phdr["offset"], note_phdr["memsz"])

        prstatus = [n for n in notes if n["type"] == NT_PRSTATUS]
        assert len(prstatus) == 1
        # desc = signal(4) + 17 regs × 4B = 72 bytes
        assert len(prstatus[0]["desc"]) == 72

    def test_arm32_register_order(self, tmp_path):
        """ARM32 registers match GDB expected order (R0-R12, SP, LR, PC, CPSR)."""
        regs = _make_arm32_regs()
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(regs)
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        note_phdr = elf["phdrs"][0]
        notes = _parse_notes(elf["data"], note_phdr["offset"], note_phdr["memsz"])
        prstatus = [n for n in notes if n["type"] == NT_PRSTATUS][0]

        # Skip signal (4 bytes), then read 17 uint32 values
        values = struct.unpack_from("<17I", prstatus["desc"], 4)
        for i, name in enumerate(ARM32_REG_ORDER):
            assert values[i] == regs[name], f"Register {name} mismatch"

    def test_arm32_note_alignment(self, tmp_path):
        """Note sections are 4-byte aligned."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        note_phdr = elf["phdrs"][0]
        assert note_phdr["offset"] % 4 == 0
        assert note_phdr["memsz"] % 4 == 0

    def test_memory_data_preserved(self, tmp_path):
        """Memory region data is preserved in the core dump."""
        stack_data = bytes(range(256))
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, stack_data)
        builder.build(path)

        elf = _read_elf32(path)
        loads = [p for p in elf["phdrs"] if p["type"] == PT_LOAD]
        region_offset = loads[0]["offset"]
        assert elf["data"][region_offset:region_offset + 256] == stack_data


# ─── ARM64 tests ────────────────────────────────────────────────────

class TestARM64CoreDump:
    """AArch64 ELF core dump generation."""

    def test_elf_class_64bit(self, tmp_path):
        """64-bit ELF class field."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm64")
        builder.set_registers(_make_arm64_regs())
        builder.add_memory_region("stack", 0x80000000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf64(path)
        assert elf["ident"][4] == ELFCLASS64

    def test_elf_machine_aarch64(self, tmp_path):
        """ELF machine is EM_AARCH64."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm64")
        builder.set_registers(_make_arm64_regs())
        builder.add_memory_region("stack", 0x80000000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf64(path)
        assert elf["machine"] == EM_AARCH64

    def test_arm64_register_count(self, tmp_path):
        """NT_PRSTATUS has 34 registers for AArch64."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm64")
        builder.set_registers(_make_arm64_regs())
        builder.add_memory_region("stack", 0x80000000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf64(path)
        note_phdr = elf["phdrs"][0]
        notes = _parse_notes(elf["data"], note_phdr["offset"], note_phdr["memsz"])

        prstatus = [n for n in notes if n["type"] == NT_PRSTATUS]
        assert len(prstatus) == 1
        # desc = signal(4) + pad(4) + 34 regs × 8B = 280 bytes
        assert len(prstatus[0]["desc"]) == 280

    def test_arm64_register_order(self, tmp_path):
        """ARM64 registers match GDB expected order."""
        regs = _make_arm64_regs()
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm64")
        builder.set_registers(regs)
        builder.add_memory_region("stack", 0x80000000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf64(path)
        note_phdr = elf["phdrs"][0]
        notes = _parse_notes(elf["data"], note_phdr["offset"], note_phdr["memsz"])
        prstatus = [n for n in notes if n["type"] == NT_PRSTATUS][0]

        # Skip signal(4) + pad(4), then read 34 uint64 values
        values = struct.unpack_from("<34Q", prstatus["desc"], 8)
        for i, name in enumerate(ARM64_REG_ORDER):
            assert values[i] == regs[name], f"Register {name} mismatch"


# ─── Edge cases ─────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and robustness."""

    def test_multiple_regions(self, tmp_path):
        """Multiple PT_LOAD segments are all present."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\xAA" * 128)
        builder.add_memory_region(".data", 0x08000000, b"\xBB" * 64)
        builder.add_memory_region(".bss", 0x08001000, b"\xCC" * 32)
        builder.build(path)

        elf = _read_elf32(path)
        loads = [p for p in elf["phdrs"] if p["type"] == PT_LOAD]
        assert len(loads) == 3
        assert loads[0]["vaddr"] == 0x20003000
        assert loads[1]["vaddr"] == 0x08000000
        assert loads[2]["vaddr"] == 0x08001000

    def test_empty_regions_list(self, tmp_path):
        """Builder with no regions does not crash."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.build(path)

        elf = _read_elf32(path)
        # Only PT_NOTE, no PT_LOAD
        loads = [p for p in elf["phdrs"] if p["type"] == PT_LOAD]
        assert len(loads) == 0

    def test_zero_length_region(self, tmp_path):
        """Zero-length memory region produces valid PT_LOAD."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("empty", 0x20003000, b"")
        builder.build(path)

        elf = _read_elf32(path)
        loads = [p for p in elf["phdrs"] if p["type"] == PT_LOAD]
        assert len(loads) == 1
        assert loads[0]["memsz"] == 0
        assert loads[0]["vaddr"] == 0x20003000

    def test_missing_registers_default_zero(self, tmp_path):
        """Missing register values default to 0."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers({"r0": 0xDEAD})  # Only r0 set
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        note_phdr = elf["phdrs"][0]
        notes = _parse_notes(elf["data"], note_phdr["offset"], note_phdr["memsz"])
        prstatus = [n for n in notes if n["type"] == NT_PRSTATUS][0]

        values = struct.unpack_from("<17I", prstatus["desc"], 4)
        assert values[0] == 0xDEAD  # r0
        assert values[1] == 0       # r1 defaults to 0
        assert values[16] == 0      # cpsr defaults to 0

    def test_prpsinfo_present(self, tmp_path):
        """NT_PRPSINFO note is present."""
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("stack", 0x20003000, b"\x00" * 64)
        builder.build(path)

        elf = _read_elf32(path)
        note_phdr = elf["phdrs"][0]
        notes = _parse_notes(elf["data"], note_phdr["offset"], note_phdr["memsz"])
        prpsinfo = [n for n in notes if n["type"] == NT_PRPSINFO]
        assert len(prpsinfo) == 1

    def test_region_data_integrity(self, tmp_path):
        """Region data bytes are exactly preserved."""
        data1 = bytes(range(64))
        data2 = bytes(range(64, 128))
        path = str(tmp_path / "core.dump")
        builder = ELFCoreDumpBuilder(arch="arm")
        builder.set_registers(_make_arm32_regs())
        builder.add_memory_region("a", 0x1000, data1)
        builder.add_memory_region("b", 0x2000, data2)
        builder.build(path)

        elf = _read_elf32(path)
        loads = [p for p in elf["phdrs"] if p["type"] == PT_LOAD]
        assert elf["data"][loads[0]["offset"]:loads[0]["offset"] + 64] == data1
        assert elf["data"][loads[1]["offset"]:loads[1]["offset"] + 64] == data2
