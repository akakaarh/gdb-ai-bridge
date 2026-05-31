---
name: analyze-crash
description: Analyze kernel oops logs, GDB crash dumps, and embedded system crash reports. Use when user pastes an oops log, asks to analyze a crash, or mentions kernel panic/HardFault.
---

## When to use

- User pastes a kernel oops log or panic output
- User asks to analyze a crash dump
- User mentions "kernel panic", "HardFault", "oops", "crash analysis"
- User has a GDB bridge JSON file to analyze

## How to use

### Step 1: Identify the input type

- **Oops log text**: Contains "Unable to handle", "Kernel panic", "Call trace:", etc.
- **GDB bridge JSON**: Starts with `{` and has `layer0`, `layer1` keys
- **File path**: User points to a `.txt` or `.json` file

### Step 2: Parse and analyze

For oops log text:
```python
python E:/projects/gdb-ai-bridge/analyzer.py <input_file>
```

Or use the MCP tool:
```
mcp__gdb-ai-bridge__analyze_crash(text="<oops_text>", target_type="generic")
```

For GDB bridge JSON:
```python
python E:/projects/gdb-ai-bridge/analyzer.py <json_file>
```

### Step 3: Enrich with code context

If the crash involves functions in the kernel-index database:
```
mcp__kernel-index__find_symbol(name="<function_name>")
mcp__kernel-index__call_graph(name="<function_name>", direction="callers")
```

### Step 4: Generate analysis

Use the enriched prompt to analyze the crash. Include:
1. **Crash cause**: NULL pointer, stack overflow, use-after-free, etc.
2. **Crash location**: Function and code line
3. **Call path analysis**: How execution reached the crash point
4. **Debugging steps**: Concrete actionable suggestions
5. **Possible fix**: If determinable from the available context

### Step 5: Save Q&A to wiki (optional)

If the analysis reveals a reusable debugging pattern:
```
Create wiki page at E:\Wiki\embedded\wiki\questions/?<brief-description>.md
```

## Architecture awareness

The analyzer supports multiple target types:
- `baremetal`: Cortex-M HardFault, SCB/CFSR decoding
- `linux`: Kernel oops, kallsyms, stack trace parsing
- `generic`: Auto-detect

For Cortex-M HardFault, the analyzer knows about:
- CFSR bit fields (IACCVIOL, DACCVIOL, MMARVALID, etc.)
- Exception frame structure (R0-R3, R12, LR, PC, xPSR)
- ARM calling convention (R0-R3 = args, R11 = FP, R14 = LR)

## Example

User pastes:
```
Unable to handle kernel NULL pointer dereference at virtual address 0000000000000020
...
Call trace:
 pca953x_irq_handler+0x48/0x1c0 [gpio_pca953x]
 __handle_irq_event_percpu+0x50/0x140
```

You should:
1. Recognize this as a kernel oops
2. Run `python analyzer.py` on the text
3. Look up `pca953x_irq_handler` in kernel-index
4. Provide structured analysis with crash cause and fix suggestions
