"""SVD (System View Description) parser and peripheral register decoder.

Parses CMSIS-SVD XML files to decode peripheral register values into
human-readable format. Zero external dependencies (uses stdlib xml.etree).

Usage:
    from gdb_bridge.svd import SVDParser, RegisterDecoder

    parser = SVDParser("STM32MP157x.svd")
    decoder = RegisterDecoder(parser)
    print(decoder.decode(0x40005414, 0x82))
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SVDField:
    """A single bit-field within a register."""
    name: str           # e.g. "BERR"
    bit_offset: int     # e.g. 0
    bit_width: int      # e.g. 1
    description: str    # e.g. "Bus error"


@dataclass
class SVDRegister:
    """A peripheral register with its bit-fields."""
    name: str           # e.g. "SR1"
    offset: int         # e.g. 0x14 (relative to peripheral base)
    size: int           # e.g. 32 (bits)
    access: str         # e.g. "read-write"
    fields: list[SVDField] = field(default_factory=list)
    description: str = ""


@dataclass
class SVDPeripheral:
    """A peripheral with its registers."""
    name: str           # e.g. "I2C1"
    base_address: int   # e.g. 0x40005400
    description: str
    registers: list[SVDRegister] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SVD Parser
# ---------------------------------------------------------------------------

class SVDParser:
    """Parse a CMSIS-SVD file and provide register lookup by name or address.

    Args:
        svd_path: Path to the .svd XML file. If None, uses SVD_FILE env var.
    """

    def __init__(self, svd_path: str | None = None):
        if svd_path is None:
            svd_path = os.environ.get("SVD_FILE", "")

        self._peripherals: dict[str, SVDPeripheral] = {}
        self._address_index: dict[int, tuple[SVDPeripheral, SVDRegister]] = {}
        self._default_size: int = 32

        if not svd_path or not os.path.isfile(svd_path):
            return

        tree = ET.parse(svd_path)
        root = tree.getroot()
        self._parse_device(root)

    def _parse_device(self, root: ET.Element) -> None:
        """Parse the top-level <device> element."""
        size_el = root.find("size")
        if size_el is not None and size_el.text:
            self._default_size = int(size_el.text, 0)

        # Collect all peripheral elements
        periph_els = root.findall(".//peripheral")

        # First pass: parse non-derived peripherals
        bases: dict[str, SVDPeripheral] = {}
        derived: list[ET.Element] = []
        for el in periph_els:
            if el.get("derivedFrom"):
                derived.append(el)
                continue
            periph = self._parse_peripheral(el)
            if periph:
                self._peripherals[periph.name] = periph
                bases[periph.name] = periph

        # Second pass: resolve derivedFrom
        for el in derived:
            src_name = el.get("derivedFrom", "")
            if src_name not in bases:
                continue
            base = bases[src_name]
            name = el.findtext("name", "")
            if not name:
                continue
            base_addr_el = el.find("baseAddress")
            base_addr = int(base_addr_el.text, 0) if base_addr_el is not None else base.base_address
            desc = el.findtext("description", "") or base.description
            periph = SVDPeripheral(
                name=name,
                base_address=base_addr,
                description=desc,
                registers=base.registers,
            )
            self._peripherals[name] = periph

        # Build address index
        for periph in self._peripherals.values():
            for reg in periph.registers:
                abs_addr = periph.base_address + reg.offset
                self._address_index[abs_addr] = (periph, reg)

    def _parse_peripheral(self, el: ET.Element) -> SVDPeripheral | None:
        """Parse a single <peripheral> element."""
        name = el.findtext("name", "")
        if not name:
            return None
        desc = el.findtext("description", "")
        base_addr_el = el.find("baseAddress")
        if base_addr_el is None or base_addr_el.text is None:
            return None
        base_addr = int(base_addr_el.text, 0)

        registers: list[SVDRegister] = []
        for reg_el in el.findall(".//register"):
            parsed = self._parse_register(reg_el)
            if parsed is not None:
                registers.extend(parsed)

        return SVDPeripheral(
            name=name,
            base_address=base_addr,
            description=desc,
            registers=registers,
        )

    def _parse_register(self, el: ET.Element) -> list[SVDRegister] | None:
        """Parse a <register> element, expanding dim arrays if present."""
        name = el.findtext("name", "")
        if not name:
            return None

        offset_el = el.find("addressOffset")
        if offset_el is None or offset_el.text is None:
            return None
        offset = int(offset_el.text, 0)
        size_el = el.find("size")
        size = int(size_el.text, 0) if size_el is not None and size_el.text else self._default_size
        access = el.findtext("access", "read-write")
        desc = el.findtext("description", "")

        fields = self._parse_fields(el)

        # Check for dim array
        dim_el = el.find("dim")
        if dim_el is not None and dim_el.text:
            dim_count = int(dim_el.text, 0)
            dim_inc_el = el.find("dimIncrement")
            dim_inc = int(dim_inc_el.text, 0) if dim_inc_el is not None and dim_inc_el.text else 0
            dim_index_text = el.findtext("dimIndex", "")

            if dim_index_text:
                indices = [s.strip() for s in dim_index_text.split(",")]
            else:
                indices = [str(i) for i in range(dim_count)]

            result = []
            for i, idx in enumerate(indices):
                reg_name = name.replace("%s", idx).replace("%d", str(i))
                reg = SVDRegister(
                    name=reg_name,
                    offset=offset + i * dim_inc,
                    size=size,
                    access=access,
                    fields=fields,
                    description=desc,
                )
                result.append(reg)
            return result

        return [SVDRegister(
            name=name,
            offset=offset,
            size=size,
            access=access,
            fields=fields,
            description=desc,
        )]

    def _parse_fields(self, reg_el: ET.Element) -> list[SVDField]:
        """Parse all <field> elements within a register."""
        fields: list[SVDField] = []
        for field_el in reg_el.findall(".//field"):
            fname = field_el.findtext("name", "")
            if not fname:
                continue
            bit_offset_el = field_el.find("bitOffset")
            if bit_offset_el is None or bit_offset_el.text is None:
                continue
            bit_offset = int(bit_offset_el.text, 0)
            bit_width_el = field_el.find("bitWidth")
            bit_width = int(bit_width_el.text, 0) if bit_width_el is not None and bit_width_el.text else 1
            fdesc = field_el.findtext("description", "")
            fields.append(SVDField(
                name=fname,
                bit_offset=bit_offset,
                bit_width=bit_width,
                description=fdesc,
            ))
        return fields

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_peripheral(self, name: str) -> SVDPeripheral | None:
        """Get a peripheral by name."""
        return self._peripherals.get(name)

    def find_register(self, address: int) -> tuple[SVDPeripheral, SVDRegister] | None:
        """Find the peripheral and register for an absolute address."""
        return self._address_index.get(address)

    def list_peripherals(self) -> list[str]:
        """List all peripheral names."""
        return list(self._peripherals.keys())


# ---------------------------------------------------------------------------
# Register Decoder
# ---------------------------------------------------------------------------

class RegisterDecoder:
    """Decode register values into human-readable bit-field descriptions.

    Args:
        parser: A loaded SVDParser instance.
    """

    def __init__(self, parser: SVDParser):
        self._parser = parser

    def decode(self, address: int, value: int) -> str:
        """Decode a register value at the given address.

        Returns a multi-line string like:
            I2C1->SR1 (0x40005414) = 0x00000082
              [7] TXE  = 1  (TX data register empty)
              [0] BERR = 1  (Bus error)

        For unknown addresses:
            Unknown register (0xDEADBEEF) = 0x00001234
        """
        result = self._parser.find_register(address)

        if result is None:
            return self._format_unknown(address, value)

        periph, reg = result
        header = f"{periph.name}->{reg.name} (0x{address:08X}) = 0x{value:08X}\n"

        lines = []
        for svd_field in sorted(reg.fields, key=lambda f: f.bit_offset):
            mask = (1 << svd_field.bit_width) - 1
            field_value = (value >> svd_field.bit_offset) & mask

            if svd_field.bit_width == 1:
                bit_range = f"[{svd_field.bit_offset}]"
            else:
                hi = svd_field.bit_offset + svd_field.bit_width - 1
                bit_range = f"[{hi}:{svd_field.bit_offset}]"

            desc = f"  ({svd_field.description})" if svd_field.description else ""
            lines.append(
                f"  {bit_range} {svd_field.name} = {field_value}{desc}"
            )

        return header + "\n".join(lines)

    def _format_unknown(self, address: int, value: int) -> str:
        """Format output for an address not found in SVD."""
        return f"Unknown register (0x{address:08X}) = 0x{value:08X}"
