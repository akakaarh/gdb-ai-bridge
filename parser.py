"""Kernel oops/panic log parser — extracts structured crash info from raw text or GDB bridge JSON."""

import json
import re
from dataclasses import dataclass, field


@dataclass
class Frame:
    address: str = ""
    function: str = ""
    offset: str = ""
    size: str = ""
    module: str = ""
    file: str = ""
    line: int = 0


@dataclass
class OopsInfo:
    error_type: str = ""
    fault_address: str = ""
    crash_function: str = ""
    crash_module: str = ""
    crash_file: str = ""
    crash_line: int = 0
    arch: str = ""
    registers: dict = field(default_factory=dict)
    stack_trace: list = field(default_factory=list)
    raw_text: str = ""


def parse_oops(text: str) -> OopsInfo:
    """Parse a kernel oops/panic log into structured data."""
    info = OopsInfo(raw_text=text)
    lines = text.splitlines()

    _parse_error_line(lines, info)
    _parse_pc(lines, info)
    _parse_registers(lines, info)
    _parse_call_trace(lines, info)
    return info


def _parse_error_line(lines: list[str], info: OopsInfo):
    for raw_line in lines:
        line = _strip_timestamp(raw_line)
        # "Unable to handle kernel NULL pointer dereference at virtual address XXXX"
        m = re.search(r"Unable to handle kernel (.+?)(?:\s+at virtual address\s+([0-9a-fA-F]+))?\s*$", line)
        if m:
            info.error_type = f"Unable to handle kernel {m.group(1).strip()}"
            if m.group(2):
                info.fault_address = m.group(2)
            continue
        # "Kernel panic - not syncing: ..."
        m = re.search(r"Kernel panic - not syncing:\s*(.*)", line)
        if m:
            info.error_type = f"Kernel panic: {m.group(1).strip()}"
            continue
        # "BUG: unable to handle ..."
        m = re.search(r"BUG:\s*(.*)", line)
        if m and not info.error_type:
            info.error_type = m.group(1).strip()
            continue
        # "Internal error: Oops: ..."
        m = re.search(r"Internal error:\s*(.*)", line)
        if m and not info.error_type:
            info.error_type = m.group(1).strip()


def _parse_pc(lines: list[str], info: OopsInfo):
    for raw_line in lines:
        line = _strip_timestamp(raw_line)
        # "pc : func_name+0x48/0x1c0 [module]"  or  "pc : func_name+0x48/0x1c0 at file.c:N"
        m = re.search(r"pc\s*:\s*(\w+)\+?(0x[0-9a-fA-F]+)?/?(0x[0-9a-fA-F]+)?\s*(?:\[([^\]]+)\])?", line)
        if m:
            info.crash_function = m.group(1)
            if m.group(4):
                info.crash_module = m.group(4)
            continue
        # ARM32: "PC is at func_name+0x48/0x1c0"
        m = re.search(r"PC is at (\w+)\+?(0x[0-9a-fA-F]+)?/?(0x[0-9a-fA-F]+)?", line)
        if m:
            info.crash_function = m.group(1)


def _strip_timestamp(line: str) -> str:
    """Remove kernel log timestamp prefix like '[   45.123507] '."""
    return re.sub(r"^\[\s*[\d.+\-\s]+\]\s*", "", line)


def _parse_registers(lines: list[str], info: OopsInfo):
    for line in lines:
        stripped = _strip_timestamp(line).strip()
        # ARM64 register lines: "x29: ... x28: ..." or "pc : ..." or "lr : ..."
        if re.match(r"x\d+\s*:", stripped) or re.match(r"pc\s*:", stripped) or re.match(r"lr\s*:", stripped):
            pairs = re.findall(r"((?:x\d+|pc|lr|sp))\s*:\s*([0-9a-fA-F]+)", stripped)
            for name, val in pairs:
                info.registers[name.lower()] = val
            continue
        # ARM64: "pstate: XXXXXXXX"
        m = re.match(r"pstate\s*:\s*([0-9a-fA-F]+)", stripped)
        if m:
            info.registers["pstate"] = m.group(1)
            continue
        # ARM32: "r0: 00000000 r1: 00000001 ..."
        if re.match(r"r\d+\s*:", stripped) or re.match(r"(?:pc|lr|sp|cpsr|ip|fp)\s*:", stripped):
            pairs = re.findall(r"(r\d+|pc|lr|sp|cpsr|ip|fp)\s*:\s*([0-9a-fA-F]+)", stripped)
            for name, val in pairs:
                info.registers[name.lower()] = val

    # Detect arch from register names
    if any(k.startswith("x") for k in info.registers):
        info.arch = "arm64"
    elif any(k.startswith("r") for k in info.registers):
        info.arch = "arm"


def _parse_call_trace(lines: list[str], info: OopsInfo):
    in_trace = False
    for raw_line in lines:
        line = _strip_timestamp(raw_line)
        if re.search(r"Call trace:|Backtrace:", line):
            in_trace = True
            continue
        if in_trace:
            # End of trace
            if re.search(r"---\[|end trace|Instruction fetch", line):
                break

            frame = _parse_frame(line)
            if frame and frame.function:
                info.stack_trace.append(frame)

    # If no "Call trace:" header found, try pattern-based detection
    if not info.stack_trace:
        for raw_line in lines:
            line = _strip_timestamp(raw_line)
            # ARM32 backtrace: "[<addr>] (func) from [<addr>] (func)"
            if re.search(r"\[<[0-9a-fA-F]+>\].*from.*\[<[0-9a-fA-F]+>\]", line):
                # Parse both sides of "from"
                parts = re.findall(r"\[<([0-9a-fA-F]+)>\]\s*\(([^)]*)\)", line)
                for addr, func_name in parts:
                    if func_name:
                        frame = Frame(address=addr, function=func_name)
                        info.stack_trace.append(frame)


def _parse_frame(line: str) -> Frame | None:
    line = line.strip()
    if not line:
        return None

    # Skip non-trace lines
    if line.startswith("---[") or line.startswith("Code:") or line.startswith("end trace"):
        return None

    frame = Frame()

    # ARM64 call trace: " func_name+0x48/0x1c0 [module]" or "func.constprop.0+0x68/0x100"
    m = re.match(r"\s*([\w.]+)\+(0x[0-9a-fA-F]+)/(0x[0-9a-fA-F]+)\s*(?:\[([^\]]+)\])?\s*$", line)
    if m:
        frame.function = m.group(1)
        frame.offset = m.group(2)
        frame.size = m.group(3)
        frame.module = m.group(4) or ""
        return frame

    # ARM64 with address: "[<ffffffc0001234>] func_name+0x48/0x1c0 [module]"
    m = re.search(r"\[<([0-9a-fA-F]+)>\]\s*([\w.]+)\+(0x[0-9a-fA-F]+)/(0x[0-9a-fA-F]+)\s*(?:\[([^\]]+)\])?", line)
    if m:
        frame.address = m.group(1)
        frame.function = m.group(2)
        frame.offset = m.group(3)
        frame.size = m.group(4)
        frame.module = m.group(5) or ""
        return frame

    # ARM32: "[<c0123456>] (func_name) from [<c0789abc>]"
    m = re.search(r"\[<([0-9a-fA-F]+)>\]\s*\(?(\w+)\)?", line)
    if m:
        frame.address = m.group(1)
        frame.function = m.group(2)
        if len(frame.function) < 3 or frame.function in ("Code", "from", "end"):
            return None
        return frame

    return None


def _parse_arm32_backtrace(lines: list[str], info: OopsInfo):
    """Parse ARM32 backtrace format: '[<addr>] (func) from [<addr>] (func)'"""
    in_trace = False
    for line in lines:
        if "Backtrace:" in line:
            in_trace = True
            continue
        if in_trace:
            if "---[ end trace" in line:
                break
            # Each line may have multiple "from" chains
            parts = re.findall(r"\[<([0-9a-fA-F]+)>\]\s*\(?(\w+)\)?", line)
            for addr, func in parts:
                if func and func not in ("from", "end"):
                    info.stack_trace.append(Frame(address=addr, function=func))


def parse_json(text: str) -> OopsInfo:
    """Parse GDB bridge JSON output into OopsInfo."""
    data = json.loads(text)
    info = OopsInfo(raw_text=text)

    # Arch
    info.arch = data.get("config", {}).get("arch", "")

    # Layer 0: registers + fault
    layer0 = data.get("layer0", {})
    if layer0.get("status") == "ok":
        # Extract raw register values
        for name, reg_info in layer0.get("registers", {}).items():
            if isinstance(reg_info, dict):
                info.registers[name] = reg_info.get("value", reg_info.get("raw", ""))

        # Crash type from fault analysis
        info.crash_type = layer0.get("crash_type", "")
        info.error_type = layer0.get("crash_reason", "")

    # Layer 1: stack trace + exception frame
    layer1 = data.get("layer1", {})
    if layer1.get("status") == "ok":
        for frame_data in layer1.get("stack_trace", []):
            if isinstance(frame_data, dict):
                frame = Frame(
                    address=frame_data.get("address", ""),
                    function=frame_data.get("function", ""),
                    file=frame_data.get("file", ""),
                    line=frame_data.get("line", 0),
                )
                info.stack_trace.append(frame)

        # Set crash function from first stack frame
        if info.stack_trace:
            info.crash_function = info.stack_trace[0].function
            info.crash_file = info.stack_trace[0].file
            info.crash_line = info.stack_trace[0].line

        # Exception frame (Cortex-M)
        exc_frame = layer1.get("exception_frame", {})
        if exc_frame and "pc" in exc_frame:
            info.crash_function = info.crash_function or "unknown"

    return info


def parse(text: str) -> OopsInfo:
    """Auto-detect format and parse."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return parse_json(text)
        except (json.JSONDecodeError, KeyError):
            pass
    return parse_oops(text)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    info = parse(text)

    print(f"Error: {info.error_type}")
    print(f"Fault address: {info.fault_address}")
    print(f"Crash function: {info.crash_function}")
    print(f"Crash module: {info.crash_module}")
    print(f"Arch: {info.arch}")
    print(f"Registers ({len(info.registers)}):")
    for k, v in info.registers.items():
        print(f"  {k} = {v}")
    print(f"Stack trace ({len(info.stack_trace)} frames):")
    for f in info.stack_trace:
        mod = f" [{f.module}]" if f.module else ""
        print(f"  {f.address} {f.function}+{f.offset}/{f.size}{mod}")
