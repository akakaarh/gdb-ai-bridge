"""GDB-AI Bridge MCP Server — exposes crash analysis tools to AI agents."""

from mcp.server.fastmcp import FastMCP
from pydantic import Field
import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

mcp = FastMCP("gdb_ai_bridge")


# --- Tool: parse_oops ---
@mcp.tool()
def parse_oops(text: str = Field(description="Kernel oops log text or GDB bridge JSON")) -> str:
    """Parse a kernel oops log or GDB bridge JSON output into structured crash info.
    Returns JSON with: error_type, crash_function, arch, registers, stack_trace.
    """
    from parser import parse

    info = parse(text)
    return json.dumps(
        {
            "error_type": info.error_type,
            "fault_address": info.fault_address,
            "crash_function": info.crash_function,
            "crash_module": info.crash_module,
            "arch": info.arch,
            "registers": info.registers,
            "stack_trace": [
                {
                    "function": f.function,
                    "address": f.address,
                    "file": f.file,
                    "line": f.line,
                }
                for f in info.stack_trace
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


# --- Tool: analyze_crash ---
@mcp.tool()
def analyze_crash(
    text: str = Field(description="Kernel oops log text or GDB bridge JSON"),
    target_type: str = Field(
        default="generic", description="Target type: baremetal, linux, or generic"
    ),
) -> str:
    """Parse crash data and generate an AI analysis prompt.
    Returns a ready-to-use prompt for crash analysis.
    """
    from parser import parse
    from analyzer import build_prompt, build_prompt_from_json, get_system_prompt
    from enricher import enrich, context_to_text

    # Try JSON first
    try:
        data = json.loads(text)
        if "layer0" in data or "config" in data:
            return build_prompt_from_json(data)
    except (json.JSONDecodeError, KeyError):
        pass

    # Fall back to oops text
    oops = parse(text)
    ctx = enrich(oops)
    return build_prompt(oops, ctx, target_type)


# --- Tool: list_actions ---
@mcp.tool()
def list_actions() -> str:
    """List all available structured debug actions that AI can use to control GDB.
    Returns JSON array of action definitions with parameters.
    """
    from debug_loop.actions import get_available_actions

    return json.dumps(get_available_actions(), indent=2)


# --- Tool: translate_action ---
@mcp.tool()
def translate_action(
    action: str = Field(description="Action name (e.g. 'read_register', 'set_breakpoint')"),
    params: dict = Field(default_factory=dict, description="Action parameters"),
) -> str:
    """Translate a structured debug action into a GDB command string.
    Returns JSON with: gdb_command or error.
    """
    from debug_loop.actions import translate_action as _translate

    action_dict = {"action": action, "params": params}
    cmd, err = _translate(action_dict)
    if err:
        return json.dumps({"error": err})
    return json.dumps({"gdb_command": cmd})


# --- Tool: get_system_prompt ---
@mcp.tool()
def get_system_prompt(
    target_type: str = Field(description="Target type: baremetal, linux, or generic"),
) -> str:
    """Get the system prompt for a specific target type.
    Use this to understand what knowledge the AI should have for analyzing crashes on this target.
    """
    from analyzer import get_system_prompt as _get

    return _get(target_type)


if __name__ == "__main__":
    mcp.run(transport="stdio")
