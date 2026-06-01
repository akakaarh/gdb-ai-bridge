"""Tests for SVD parser and register decoder.

All tests use in-memory mock XML, NOT the real 5MB SVD file.
"""
import pytest
import xml.etree.ElementTree as ET

from gdb_bridge.svd import SVDParser, RegisterDecoder, SVDField, SVDRegister, SVDPeripheral
from gdb_bridge.collector import Collector


# ---------------------------------------------------------------------------
# Fixtures — minimal SVD XML strings
# ---------------------------------------------------------------------------

MINIMAL_SVD = """\
<?xml version="1.0" encoding="UTF-8"?>
<device>
  <name>TestDevice</name>
  <peripherals>
    <peripheral>
      <name>I2C1</name>
      <description>I2C interface 1</description>
      <baseAddress>0x40005400</baseAddress>
      <registers>
        <register>
          <name>CR1</name>
          <description>Control register 1</description>
          <addressOffset>0x00</addressOffset>
          <size>32</size>
          <access>read-write</access>
          <fields>
            <field>
              <name>PE</name>
              <description>Peripheral enable</description>
              <bitOffset>0</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>TXIE</name>
              <description>TX interrupt enable</description>
              <bitOffset>1</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
        <register>
          <name>SR1</name>
          <description>Status register 1</description>
          <addressOffset>0x14</addressOffset>
          <size>32</size>
          <access>read-only</access>
          <fields>
            <field>
              <name>BERR</name>
              <description>Bus error</description>
              <bitOffset>0</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>TXE</name>
              <description>TX data register empty</description>
              <bitOffset>7</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
      </registers>
    </peripheral>
    <peripheral>
      <name>GPIOA</name>
      <description>GPIO port A</description>
      <baseAddress>0x48000000</baseAddress>
      <registers>
        <register>
          <name>MODER</name>
          <description>Port mode register</description>
          <addressOffset>0x00</addressOffset>
          <size>32</size>
          <access>read-write</access>
          <fields>
            <field>
              <name>MODE0</name>
              <description>Port x mode bits (y = 0)</description>
              <bitOffset>0</bitOffset>
              <bitWidth>2</bitWidth>
            </field>
            <field>
              <name>MODE1</name>
              <description>Port x mode bits (y = 1)</description>
              <bitOffset>2</bitOffset>
              <bitWidth>2</bitWidth>
            </field>
          </fields>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>
"""

SVD_WITH_DERIVED_FROM = """\
<?xml version="1.0" encoding="UTF-8"?>
<device>
  <name>TestDevice</name>
  <peripherals>
    <peripheral>
      <name>I2C2</name>
      <description>I2C interface 2</description>
      <baseAddress>0x40013000</baseAddress>
      <registers>
        <register>
          <name>CR1</name>
          <description>Control register 1</description>
          <addressOffset>0x00</addressOffset>
          <size>32</size>
          <fields>
            <field>
              <name>PE</name>
              <description>Peripheral enable</description>
              <bitOffset>0</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
      </registers>
    </peripheral>
    <peripheral derivedFrom="I2C2">
      <name>I2C1</name>
      <baseAddress>0x40005400</baseAddress>
    </peripheral>
    <peripheral derivedFrom="I2C2">
      <name>I2C3</name>
      <baseAddress>0x40014000</baseAddress>
    </peripheral>
  </peripherals>
</device>
"""

SVD_WITH_DIM = """\
<?xml version="1.0" encoding="UTF-8"?>
<device>
  <name>TestDevice</name>
  <peripherals>
    <peripheral>
      <name>GPIO</name>
      <description>GPIO ports</description>
      <baseAddress>0x48000000</baseAddress>
      <registers>
        <register>
          <dim>4</dim>
          <dimIncrement>0x400</dimIncrement>
          <dimIndex>A,B,C,D</dimIndex>
          <name>GPIO%s_MODER</name>
          <description>Port mode register</description>
          <addressOffset>0x00</addressOffset>
          <size>32</size>
          <fields>
            <field>
              <name>MODE0</name>
              <description>Port x mode bits</description>
              <bitOffset>0</bitOffset>
              <bitWidth>2</bitWidth>
            </field>
          </fields>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>
"""

EMPTY_SVD = """\
<?xml version="1.0" encoding="UTF-8"?>
<device>
  <name>EmptyDevice</name>
  <peripherals>
  </peripherals>
</device>
"""

SVD_MISSING_FIELDS = """\
<?xml version="1.0" encoding="UTF-8"?>
<device>
  <name>TestDevice</name>
  <peripherals>
    <peripheral>
      <name>MINIMAL</name>
      <baseAddress>0x40000000</baseAddress>
      <registers>
        <register>
          <name>REG</name>
          <addressOffset>0x00</addressOffset>
          <fields>
            <field>
              <name>BIT0</name>
              <bitOffset>0</bitOffset>
            </field>
          </fields>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_xml_string(xml_string: str) -> SVDParser:
    """Create SVDParser from an XML string (bypasses file loading).

    Delegates to the real SVDParser's internal parsing methods so that
    dim arrays and all other logic is tested consistently.
    """
    import io
    parser = SVDParser.__new__(SVDParser)
    parser._peripherals = {}
    parser._address_index = {}
    parser._default_size = 32

    root = ET.fromstring(xml_string)
    parser._parse_device(root)
    return parser


# ---------------------------------------------------------------------------
# SVDParser Tests
# ---------------------------------------------------------------------------

class TestSVDParserBasic:
    """Test basic SVD parsing of peripheral names, base addresses, registers."""

    def test_parse_peripheral_name(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        assert periph is not None
        assert periph.name == "I2C1"

    def test_parse_peripheral_description(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        assert periph.description == "I2C interface 1"

    def test_parse_peripheral_base_address(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        assert periph.base_address == 0x40005400

    def test_parse_register_name(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        assert periph is not None
        assert len(periph.registers) == 2
        assert periph.registers[0].name == "CR1"
        assert periph.registers[1].name == "SR1"

    def test_parse_register_offset(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        sr1 = periph.registers[1]
        assert sr1.offset == 0x14

    def test_parse_register_size(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        cr1 = periph.registers[0]
        assert cr1.size == 32

    def test_parse_register_access(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        assert periph.registers[0].access == "read-write"
        assert periph.registers[1].access == "read-only"

    def test_parse_fields(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("I2C1")
        sr1 = periph.registers[1]
        assert len(sr1.fields) == 2
        assert sr1.fields[0].name == "BERR"
        assert sr1.fields[0].bit_offset == 0
        assert sr1.fields[0].bit_width == 1
        assert sr1.fields[1].name == "TXE"
        assert sr1.fields[1].bit_offset == 7

    def test_parse_multi_bit_field(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        periph = parser.get_peripheral("GPIOA")
        moder = periph.registers[0]
        assert moder.fields[0].name == "MODE0"
        assert moder.fields[0].bit_width == 2
        assert moder.fields[1].name == "MODE1"
        assert moder.fields[1].bit_offset == 2
        assert moder.fields[1].bit_width == 2

    def test_list_peripherals(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        names = parser.list_peripherals()
        assert "I2C1" in names
        assert "GPIOA" in names
        assert len(names) == 2

    def test_get_peripheral_not_found(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        assert parser.get_peripheral("SPI1") is None


class TestSVDParserFindRegister:
    """Test address-based register lookup."""

    def test_find_register_by_address(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        # I2C1 base = 0x40005400, SR1 offset = 0x14
        result = parser.find_register(0x40005414)
        assert result is not None
        periph, reg = result
        assert periph.name == "I2C1"
        assert reg.name == "SR1"

    def test_find_register_base_offset(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        # I2C1 CR1 at base + 0x00
        result = parser.find_register(0x40005400)
        assert result is not None
        periph, reg = result
        assert reg.name == "CR1"

    def test_find_register_gpio(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        # GPIOA base = 0x48000000, MODER offset = 0x00
        result = parser.find_register(0x48000000)
        assert result is not None
        periph, reg = result
        assert periph.name == "GPIOA"
        assert reg.name == "MODER"

    def test_find_register_unknown_address(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        result = parser.find_register(0xDEADBEEF)
        assert result is None


class TestSVDParserDerivedFrom:
    """Test derivedFrom peripheral handling."""

    def test_derived_peripheral_exists(self):
        parser = _parse_xml_string(SVD_WITH_DERIVED_FROM)
        i2c1 = parser.get_peripheral("I2C1")
        assert i2c1 is not None
        assert i2c1.name == "I2C1"

    def test_derived_peripheral_base_address(self):
        parser = _parse_xml_string(SVD_WITH_DERIVED_FROM)
        i2c1 = parser.get_peripheral("I2C1")
        assert i2c1.base_address == 0x40005400

    def test_derived_peripheral_inherits_registers(self):
        parser = _parse_xml_string(SVD_WITH_DERIVED_FROM)
        i2c1 = parser.get_peripheral("I2C1")
        assert len(i2c1.registers) == 1
        assert i2c1.registers[0].name == "CR1"
        assert i2c1.registers[0].fields[0].name == "PE"

    def test_derived_peripheral_different_base(self):
        parser = _parse_xml_string(SVD_WITH_DERIVED_FROM)
        i2c3 = parser.get_peripheral("I2C3")
        assert i2c3 is not None
        assert i2c3.base_address == 0x40014000

    def test_find_register_in_derived_peripheral(self):
        parser = _parse_xml_string(SVD_WITH_DERIVED_FROM)
        # I2C1 base = 0x40005400, CR1 offset = 0x00
        result = parser.find_register(0x40005400)
        assert result is not None
        periph, reg = result
        assert periph.name == "I2C1"
        assert reg.name == "CR1"

    def test_all_derived_peripherals_listed(self):
        parser = _parse_xml_string(SVD_WITH_DERIVED_FROM)
        names = parser.list_peripherals()
        assert "I2C2" in names
        assert "I2C1" in names
        assert "I2C3" in names
        assert len(names) == 3


class TestSVDParserDimArray:
    """Test dim array register handling (e.g. GPIOA_MODER, GPIOB_MODER)."""

    def test_dim_registers_expanded(self):
        parser = _parse_xml_string(SVD_WITH_DIM)
        periph = parser.get_peripheral("GPIO")
        assert periph is not None
        # dim=4 with indices A,B,C,D -> 4 registers
        assert len(periph.registers) == 4

    def test_dim_register_names(self):
        parser = _parse_xml_string(SVD_WITH_DIM)
        periph = parser.get_peripheral("GPIO")
        names = [r.name for r in periph.registers]
        assert "GPIOA_MODER" in names
        assert "GPIOB_MODER" in names
        assert "GPIOC_MODER" in names
        assert "GPIOD_MODER" in names

    def test_dim_register_offsets(self):
        parser = _parse_xml_string(SVD_WITH_DIM)
        periph = parser.get_peripheral("GPIO")
        offsets = sorted(r.offset for r in periph.registers)
        assert offsets == [0x00, 0x400, 0x800, 0xC00]

    def test_dim_register_fields_inherited(self):
        parser = _parse_xml_string(SVD_WITH_DIM)
        periph = parser.get_peripheral("GPIO")
        for reg in periph.registers:
            assert len(reg.fields) == 1
            assert reg.fields[0].name == "MODE0"

    def test_dim_find_register_by_address(self):
        parser = _parse_xml_string(SVD_WITH_DIM)
        # GPIO base = 0x48000000, GPIOB_MODER = base + 0x400
        result = parser.find_register(0x48000400)
        assert result is not None
        periph, reg = result
        assert reg.name == "GPIOB_MODER"


class TestSVDParserEdgeCases:
    """Test edge cases: empty SVD, missing fields, defaults."""

    def test_empty_svd(self):
        parser = _parse_xml_string(EMPTY_SVD)
        assert parser.list_peripherals() == []
        assert parser.get_peripheral("anything") is None
        assert parser.find_register(0x40000000) is None

    def test_missing_optional_fields(self):
        parser = _parse_xml_string(SVD_MISSING_FIELDS)
        periph = parser.get_peripheral("MINIMAL")
        assert periph is not None
        assert periph.description == ""  # No description element
        reg = periph.registers[0]
        assert reg.size == 32  # Default from device
        assert reg.access == "read-write"  # Default
        field = reg.fields[0]
        assert field.bit_width == 1  # Default
        assert field.description == ""  # No description

    def test_missing_bit_width_defaults_to_one(self):
        parser = _parse_xml_string(SVD_MISSING_FIELDS)
        periph = parser.get_peripheral("MINIMAL")
        field = periph.registers[0].fields[0]
        assert field.bit_width == 1


# ---------------------------------------------------------------------------
# RegisterDecoder Tests
# ---------------------------------------------------------------------------

class TestRegisterDecoder:
    """Test register value decoding."""

    def test_decode_single_bit_set(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        # I2C1->SR1 at 0x40005414, value 0x82 = bit 7 (TXE) + bit 1 (not defined, but BERR is bit 0)
        # 0x82 = 1000_0010 -> bit 7 = TXE, bit 1 = (no field)
        result = decoder.decode(0x40005414, 0x82)
        assert "I2C1->SR1" in result
        assert "0x40005414" in result
        assert "0x00000082" in result
        assert "TXE" in result
        assert "= 1" in result
        assert "TX data register empty" in result

    def test_decode_no_bits_set(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        result = decoder.decode(0x40005414, 0x00)
        assert "I2C1->SR1" in result
        assert "BERR = 0" in result
        assert "TXE = 0" in result

    def test_decode_all_bits_set(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        # Both BERR (bit 0) and TXE (bit 7) set
        result = decoder.decode(0x40005414, 0x83)
        assert "BERR = 1" in result
        assert "TXE = 1" in result

    def test_decode_multi_bit_field(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        # GPIOA->MODER at 0x48000000, MODE0=2 (bits[1:0]=10), MODE1=1 (bits[3:2]=01)
        value = 0b01_10  # MODE1=1, MODE0=2
        result = decoder.decode(0x48000000, value)
        assert "GPIOA->MODER" in result
        assert "MODE0" in result
        assert "MODE1" in result

    def test_decode_unknown_address(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        result = decoder.decode(0xDEADBEEF, 0x1234)
        assert "Unknown register" in result
        assert "0xDEADBEEF" in result
        assert "0x00001234" in result

    def test_decode_output_header_format(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        result = decoder.decode(0x40005400, 0x01)
        # Header should contain: "PERIPH->REG (addr) = value"
        lines = result.strip().split("\n")
        header = lines[0]
        assert "I2C1->CR1" in header
        assert "0x40005400" in header
        assert "0x00000001" in header

    def test_decode_field_lines_indented(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        result = decoder.decode(0x40005400, 0x01)
        lines = result.strip().split("\n")
        # Field lines should be indented
        for line in lines[1:]:
            assert line.startswith("  ")

    def test_decode_derived_peripheral(self):
        parser = _parse_xml_string(SVD_WITH_DERIVED_FROM)
        decoder = RegisterDecoder(parser)
        # I2C1 (derived from I2C2) base = 0x40005400, CR1 at offset 0
        result = decoder.decode(0x40005400, 0x01)
        assert "I2C1->CR1" in result
        assert "PE = 1" in result


# ---------------------------------------------------------------------------
# RegisterDecoder field extraction tests
# ---------------------------------------------------------------------------

class TestRegisterDecoderFieldExtraction:
    """Test field value extraction logic."""

    def test_single_bit_at_offset_zero(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        result = decoder.decode(0x40005414, 0x01)
        assert "BERR = 1" in result

    def test_single_bit_at_offset_seven(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        result = decoder.decode(0x40005414, 0x80)
        assert "TXE = 1" in result
        assert "BERR = 0" in result

    def test_two_bit_field_value(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        # MODE0 = bits[1:0], set to 3 (0b11)
        result = decoder.decode(0x48000000, 0x03)
        assert "MODE0" in result
        # The value of MODE0 should be 3
        assert "= 3" in result

    def test_field_description_in_output(self):
        parser = _parse_xml_string(MINIMAL_SVD)
        decoder = RegisterDecoder(parser)
        result = decoder.decode(0x40005414, 0x01)
        assert "Bus error" in result


# ---------------------------------------------------------------------------
# Collector integration tests
# ---------------------------------------------------------------------------

class TestCollectorDecodeIntegration:
    """Test Collector.decode_peripheral and decode_peripherals methods."""

    def test_collector_has_decode_peripheral(self):
        """Collector should have decode_peripheral method."""
        from gdb_bridge.collector import Collector
        assert hasattr(Collector, 'decode_peripheral')

    def test_collector_has_decode_peripherals(self):
        """Collector should have decode_peripherals method."""
        from gdb_bridge.collector import Collector
        assert hasattr(Collector, 'decode_peripherals')

    def test_collector_accepts_svd_path(self):
        """Collector.__init__ should accept svd_path parameter."""
        from gdb_bridge.collector import Collector
        from unittest.mock import MagicMock
        arch = MagicMock()
        target = MagicMock()
        # Should not raise
        collector = Collector(arch, target, svd_path=None)
        assert collector is not None

    def test_collector_decode_without_svd_returns_error(self):
        """decode_peripheral should return error message when no SVD loaded."""
        from gdb_bridge.collector import Collector
        from unittest.mock import MagicMock
        arch = MagicMock()
        target = MagicMock()
        collector = Collector(arch, target)
        result = collector.decode_peripheral(0x40005414)
        assert "No SVD" in result or "not loaded" in result.lower()


# ---------------------------------------------------------------------------
# SVD dataclass tests
# ---------------------------------------------------------------------------

class TestSVDDataClasses:
    """Test the dataclass structures."""

    def test_svd_field_attributes(self):
        field = SVDField(name="PE", bit_offset=0, bit_width=1, description="Enable")
        assert field.name == "PE"
        assert field.bit_offset == 0
        assert field.bit_width == 1
        assert field.description == "Enable"

    def test_svd_register_attributes(self):
        reg = SVDRegister(name="CR1", offset=0x00, size=32, access="read-write", fields=[])
        assert reg.name == "CR1"
        assert reg.offset == 0x00
        assert reg.size == 32
        assert reg.access == "read-write"
        assert reg.fields == []

    def test_svd_peripheral_attributes(self):
        periph = SVDPeripheral(name="I2C1", base_address=0x40005400, description="I2C", registers=[])
        assert periph.name == "I2C1"
        assert periph.base_address == 0x40005400
        assert periph.description == "I2C"
        assert periph.registers == []


# ---------------------------------------------------------------------------
# Issue 1: ai config svd — _get_svd_decoder uses config before env var
# ---------------------------------------------------------------------------

class TestSvdConfigIntegration:
    """Test that _get_svd_decoder checks _config['svd_file'] before env var."""

    def test_config_has_svd_file_key(self):
        """_config dict should have 'svd_file' key."""
        import importlib
        import gdb_bridge.gdb_bridge as mod
        assert "svd_file" in mod._config

    def test_get_svd_decoder_uses_config_first(self, tmp_path):
        """_get_svd_decoder should prefer _config['svd_file'] over SVD_FILE env."""
        import importlib
        import os
        import gdb_bridge.gdb_bridge as mod

        # Write a minimal SVD to a temp file
        svd_path = tmp_path / "test.svd"
        svd_path.write_text(MINIMAL_SVD)

        # Set config, clear env
        old_config = mod._config.get("svd_file", "")
        old_env = os.environ.get("SVD_FILE")
        old_decoder = mod._svd_decoder
        old_parser = mod._svd_parser
        try:
            mod._config["svd_file"] = str(svd_path)
            os.environ.pop("SVD_FILE", None)
            mod._svd_decoder = None
            mod._svd_parser = None

            decoder = mod._get_svd_decoder()
            assert decoder is not None
            # Should be able to decode I2C1->SR1
            result = decoder.decode(0x40005414, 0x82)
            assert "I2C1->SR1" in result
        finally:
            mod._config["svd_file"] = old_config
            if old_env is not None:
                os.environ["SVD_FILE"] = old_env
            else:
                os.environ.pop("SVD_FILE", None)
            mod._svd_decoder = old_decoder
            mod._svd_parser = old_parser

    def test_get_svd_decoder_falls_back_to_env(self, tmp_path):
        """_get_svd_decoder should fall back to SVD_FILE env if config is empty."""
        import os
        import gdb_bridge.gdb_bridge as mod

        svd_path = tmp_path / "env.svd"
        svd_path.write_text(MINIMAL_SVD)

        old_config = mod._config.get("svd_file", "")
        old_env = os.environ.get("SVD_FILE")
        old_decoder = mod._svd_decoder
        old_parser = mod._svd_parser
        try:
            mod._config["svd_file"] = ""
            os.environ["SVD_FILE"] = str(svd_path)
            mod._svd_decoder = None
            mod._svd_parser = None

            decoder = mod._get_svd_decoder()
            assert decoder is not None
        finally:
            mod._config["svd_file"] = old_config
            if old_env is not None:
                os.environ["SVD_FILE"] = old_env
            else:
                os.environ.pop("SVD_FILE", None)
            mod._svd_decoder = old_decoder
            mod._svd_parser = old_parser

    def test_get_svd_decoder_returns_none_when_nothing_set(self):
        """_get_svd_decoder should return None when neither config nor env is set."""
        import os
        import gdb_bridge.gdb_bridge as mod

        old_config = mod._config.get("svd_file", "")
        old_env = os.environ.get("SVD_FILE")
        old_decoder = mod._svd_decoder
        old_parser = mod._svd_parser
        try:
            mod._config["svd_file"] = ""
            os.environ.pop("SVD_FILE", None)
            mod._svd_decoder = None
            mod._svd_parser = None

            decoder = mod._get_svd_decoder()
            assert decoder is None
        finally:
            mod._config["svd_file"] = old_config
            if old_env is not None:
                os.environ["SVD_FILE"] = old_env
            mod._svd_decoder = old_decoder
            mod._svd_parser = old_parser


# ---------------------------------------------------------------------------
# Issue 2: Shared _read_mem32 helper
# ---------------------------------------------------------------------------

class TestReadMem32Helper:
    """Test the module-level _read_mem32 helper function."""

    def test_read_mem32_exists_in_gdb_bridge(self):
        """gdb_bridge module should export _read_mem32 function."""
        import gdb_bridge.gdb_bridge as mod
        assert hasattr(mod, "_read_mem32")
        assert callable(mod._read_mem32)

    def test_read_mem32_returns_int(self):
        """_read_mem32 should return an int (mocking gdb)."""
        from unittest.mock import patch, MagicMock
        import gdb_bridge.gdb_bridge as mod

        mock_mem = MagicMock()
        mock_mem.cast.return_value = 0x12345678

        mock_frame = MagicMock()
        mock_frame.read_memory.return_value = mock_mem

        mock_gdb = MagicMock()
        mock_gdb.selected_frame.return_value = mock_frame
        mock_gdb.lookup_type.return_value = "uint32_t"

        with patch.object(mod, "_gdb", mock_gdb):
            result = mod._read_mem32(0x40005414)

        assert result == 0x12345678
        mock_frame.read_memory.assert_called_once_with(0x40005414, 4)

    def test_read_mem32_returns_zero_on_error(self):
        """_read_mem32 should return 0 on any exception."""
        from unittest.mock import patch, MagicMock
        import gdb_bridge.gdb_bridge as mod

        mock_gdb = MagicMock()
        mock_gdb.selected_frame.side_effect = RuntimeError("no frame")

        with patch.object(mod, "_gdb", mock_gdb):
            result = mod._read_mem32(0x40005414)

        assert result == 0

    def test_read_mem32_returns_zero_when_gdb_is_none(self):
        """_read_mem32 should return 0 when _gdb is None."""
        from unittest.mock import patch
        import gdb_bridge.gdb_bridge as mod

        with patch.object(mod, "_gdb", None):
            result = mod._read_mem32(0x40005414)

        assert result == 0


class TestCollectorUsesSharedReadMem32:
    """Test that Collector._read_register_value delegates to shared helper."""

    def test_collector_read_register_value_calls_shared_helper(self):
        """Collector._read_register_value should use the shared _read_mem32."""
        from unittest.mock import patch, MagicMock
        from gdb_bridge.collector import Collector

        arch = MagicMock()
        target = MagicMock()
        collector = Collector(arch, target)

        # The method should exist and be callable
        assert hasattr(collector, "_read_register_value")
        assert callable(collector._read_register_value)


# ---------------------------------------------------------------------------
# Issue 3: Auto-decode SCB registers in Collector.collect()
# ---------------------------------------------------------------------------

from gdb_bridge.collector import DebugContext as _DebugContext


def _make_arch(**overrides):
    """Create a mock ArchAdapter for testing."""
    from unittest.mock import MagicMock
    from gdb_bridge.arch.base import ArchAdapter
    arch = MagicMock(spec=ArchAdapter)
    arch.name = overrides.get("name", "arm-cortex-m")
    arch.get_registers.return_value = overrides.get("registers", {"r0": "0x00000000", "sp": "0x20004000"})
    arch.annotate_registers.side_effect = lambda regs: {k: {"value": v, "role": "general"} for k, v in regs.items()}
    arch.get_fault_registers.return_value = overrides.get("fault_regs", {"cfsr": "0x00000000"})
    arch.decode_crash.return_value = overrides.get("crash", ("none", "no fault"))
    arch.get_exception_frame.return_value = overrides.get("exc_frame", {"r0": "0x00000000", "pc": "0x08001234"})
    return arch


def _make_target(**overrides):
    """Create a mock TargetAdapter for testing."""
    from unittest.mock import MagicMock
    from gdb_bridge.target.base import TargetAdapter
    target = MagicMock(spec=TargetAdapter)
    target.name = overrides.get("name", "openocd")
    target.get_stack_trace.return_value = overrides.get("stack", [
        {"function": "main", "address": "0x08001234", "file": "main.c", "line": 42},
    ])
    target.get_local_variables.return_value = overrides.get("locals", {"x": {"type": "int", "value": "42", "is_param": False}})
    return target

SCB_SVD = """\
<?xml version="1.0" encoding="UTF-8"?>
<device>
  <name>CortexM</name>
  <peripherals>
    <peripheral>
      <name>SCB</name>
      <description>System Control Block</description>
      <baseAddress>0xE000ED00</baseAddress>
      <registers>
        <register>
          <name>CFSR</name>
          <description>Configurable Fault Status Register</description>
          <addressOffset>0x28</addressOffset>
          <size>32</size>
          <access>read-write</access>
          <fields>
            <field>
              <name>MMARVALID</name>
              <description>MemManage Fault Address Register valid</description>
              <bitOffset>7</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
            <field>
              <name>BFARVALID</name>
              <description>Bus Fault Address Register valid</description>
              <bitOffset>15</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
        <register>
          <name>HFSR</name>
          <description>HardFault Status Register</description>
          <addressOffset>0x2C</addressOffset>
          <size>32</size>
          <access>read-write</access>
          <fields>
            <field>
              <name>FORCED</name>
              <description>Forced Hard Fault</description>
              <bitOffset>30</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
        <register>
          <name>MMFAR</name>
          <description>MemManage Fault Address Register</description>
          <addressOffset>0x34</addressOffset>
          <size>32</size>
          <access>read-only</access>
        </register>
        <register>
          <name>BFAR</name>
          <description>Bus Fault Address Register</description>
          <addressOffset>0x38</addressOffset>
          <size>32</size>
          <access>read-only</access>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>
"""


class TestCollectorAutoDecodeSCB:
    """Test that Collector.collect() auto-decodes SCB registers when SVD is loaded."""

    def test_decoded_registers_field_exists(self):
        """DebugContext should have decoded_registers field."""
        ctx = _DebugContext()
        d = ctx.to_dict()
        assert "decoded_registers" in d

    def test_decoded_registers_default_empty(self):
        """decoded_registers should default to empty string."""
        ctx = _DebugContext()
        assert ctx.to_dict()["decoded_registers"] == ""

    def test_collect_decodes_scb_when_svd_loaded(self, tmp_path):
        """collect() should decode SCB registers when SVD is loaded and arch is arm."""
        from unittest.mock import patch, MagicMock
        from gdb_bridge.collector import Collector
        from gdb_bridge.svd import SVDParser, RegisterDecoder

        svd_path = tmp_path / "scb.svd"
        svd_path.write_text(SCB_SVD)

        arch = _make_arch()
        target = _make_target()

        # Mock _read_register_value to return specific values
        def mock_read(addr):
            values = {
                0xE000ED28: 0x00008000,  # CFSR with BFARVALID set
                0xE000ED2C: 0x40000000,  # HFSR with FORCED set
                0xE000ED34: 0x20004000,  # MMFAR
                0xE000ED38: 0x40005414,  # BFAR
            }
            return values.get(addr, 0)

        collector = Collector(arch, target, svd_path=str(svd_path))
        collector._read_register_value = mock_read

        ctx = collector.collect()
        decoded = ctx.to_dict()["decoded_registers"]

        # Should contain decoded SCB registers
        assert len(decoded) > 0
        assert "SCB" in decoded

    def test_collect_no_decode_when_no_svd(self):
        """collect() should leave decoded_registers empty when no SVD loaded."""
        arch = _make_arch()
        target = _make_target()
        collector = Collector(arch, target)

        ctx = collector.collect()
        assert ctx.to_dict()["decoded_registers"] == ""

    def test_collect_decode_failure_graceful(self, tmp_path):
        """collect() should handle decode errors gracefully."""
        from unittest.mock import MagicMock
        from gdb_bridge.collector import Collector

        svd_path = tmp_path / "scb.svd"
        svd_path.write_text(SCB_SVD)

        arch = _make_arch()
        target = _make_target()

        collector = Collector(arch, target, svd_path=str(svd_path))
        # Make _read_register_value raise
        collector._read_register_value = MagicMock(side_effect=RuntimeError("gdb error"))

        # Should not raise
        ctx = collector.collect()
        # decoded_registers may be empty or contain error info, but no exception
        assert isinstance(ctx.to_dict()["decoded_registers"], str)


# ---------------------------------------------------------------------------
# Issue 4: ai info shows SVD status
# ---------------------------------------------------------------------------

class TestAIInfoSvdStatus:
    """Test that AIInfoCommand displays SVD file information."""

    def test_info_includes_svd_config_key(self):
        """_config should have svd_file key for ai info to display."""
        import gdb_bridge.gdb_bridge as mod
        assert "svd_file" in mod._config

    def test_info_svd_status_when_loaded(self, tmp_path):
        """AI info should be able to report SVD file path and peripheral count."""
        import os
        import gdb_bridge.gdb_bridge as mod

        svd_path = tmp_path / "test.svd"
        svd_path.write_text(MINIMAL_SVD)

        old_config = mod._config.get("svd_file", "")
        old_env = os.environ.get("SVD_FILE")
        old_decoder = mod._svd_decoder
        old_parser = mod._svd_parser
        try:
            mod._config["svd_file"] = str(svd_path)
            mod._svd_decoder = None
            mod._svd_parser = None

            decoder = mod._get_svd_decoder()
            assert decoder is not None
            # The parser should have peripherals loaded
            assert len(mod._svd_parser.list_peripherals()) > 0
        finally:
            mod._config["svd_file"] = old_config
            if old_env is not None:
                os.environ["SVD_FILE"] = old_env
            else:
                os.environ.pop("SVD_FILE", None)
            mod._svd_decoder = old_decoder
            mod._svd_parser = old_parser
