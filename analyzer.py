"""Analyzer — assembles enriched context into an analysis prompt for AI review."""

import sys
from pathlib import Path

from parser import parse, OopsInfo
from enricher import enrich, EnrichedContext, context_to_text


SYSTEM_PROMPTS = {
    "generic": """你是一个嵌入式系统调试专家，擅长分析 ARM Cortex-M/A 处理器崩溃报告。你的分析应该：

1. 结合寄存器状态和代码逻辑推理崩溃原因
2. 从调用链推断执行路径和出错环节
3. 给出具体可操作的排查建议
4. 如果能判断修复方向，给出建议

回答要简洁、有依据、可操作。不要泛泛而谈。""",

    "linux": """你是一个 Linux 内核调试专家，擅长分析内核崩溃报告。你的分析应该：

1. 结合寄存器状态和代码逻辑推理崩溃原因
2. 从调用链推断执行路径和出错环节
3. 给出具体可操作的排查建议
4. 如果能判断修复方向，给出建议

回答要简洁、有依据、可操作。不要泛泛而谈。""",

    "baremetal": """你是一个嵌入式系统调试专家，擅长分析 Cortex-M 处理器 HardFault 崩溃报告。

你了解以下知识：
- Cortex-M 异常模型：HardFault、MemManage、BusFault、UsageFault
- CFSR 位域含义（IACCVIOL、DACCVIOL、MMARVALID、PRECISERR 等）
- 异常帧结构：硬件自动压栈的 R0-R3, R12, LR, PC, xPSR
- ARM 调用约定：R0-R3 是参数，R11=FP，R14=LR，R15=PC
- NULL 指针解引用模式：fault_address = NULL + 小偏移（结构体成员访问）
- 栈溢出模式：SP 落在栈区域之外
- 野指针模式：PC/LR 指向非法地址

你的分析应该：
1. 结合寄存器状态和代码逻辑推理崩溃原因
2. 从 CFSR/HFSR 位域精确判断故障类型
3. 给出具体可操作的排查建议
4. 如果能判断修复方向，给出建议

回答要简洁、有依据、可操作。不要泛泛而谈。""",
}


def get_system_prompt(target_type: str = "generic") -> str:
    """Get the appropriate system prompt for the target type."""
    return SYSTEM_PROMPTS.get(target_type, SYSTEM_PROMPTS["generic"])


def build_prompt_from_json(data: dict) -> str:
    """Build analysis prompt from GDB bridge JSON output."""
    config = data.get("config", {})
    target_type = config.get("target", "generic")
    arch = config.get("arch", "unknown")

    parts = [get_system_prompt(target_type), ""]

    # Config info
    parts.append("## 调试环境")
    parts.append(f"- 架构: {arch}")
    parts.append(f"- 目标类型: {target_type}")
    if config.get("elf_file"):
        parts.append(f"- ELF 文件: {config['elf_file']}")
    parts.append("")

    # Layer 0: registers + fault
    layer0 = data.get("layer0", {})
    if layer0.get("status") == "ok":
        # Crash info
        if layer0.get("crash_type") and layer0["crash_type"] != "unknown":
            parts.append("## 崩溃信息")
            parts.append(f"- 类型: {layer0['crash_type']}")
            if layer0.get("crash_reason"):
                parts.append(f"- 原因: {layer0['crash_reason']}")
            parts.append("")

        # Fault registers
        fault = layer0.get("fault_registers", {})
        if fault and not isinstance(fault.get("status"), str):
            parts.append("## 故障寄存器")
            for name, val in fault.items():
                parts.append(f"  {name} = {val}")
            parts.append("")

        # Registers
        regs = layer0.get("registers", {})
        if regs and not isinstance(regs.get("status"), str):
            parts.append("## 寄存器")
            for name, info in regs.items():
                if isinstance(info, dict):
                    val = info.get("value", info.get("raw", "?"))
                    role = info.get("role", "")
                    role_str = f" ({role})" if role else ""
                    parts.append(f"  {name} = {val}{role_str}")
            parts.append("")

    # Layer 1: exception frame + stack trace
    layer1 = data.get("layer1", {})
    if layer1.get("status") == "ok":
        # Exception frame
        exc_frame = layer1.get("exception_frame", {})
        if exc_frame and not isinstance(exc_frame.get("status"), str):
            parts.append("## 异常帧（硬件自动压栈）")
            for name, val in exc_frame.items():
                parts.append(f"  {name} = {val}")
            parts.append("")

        # Stack trace
        trace = layer1.get("stack_trace", [])
        if trace and isinstance(trace, list):
            parts.append("## 栈回溯")
            for i, frame in enumerate(trace[:20]):
                if isinstance(frame, dict):
                    func = frame.get("function", "??")
                    f = frame.get("file", "")
                    line = frame.get("line", 0)
                    conf = frame.get("confidence", "")
                    loc = f" at {f}:{line}" if f and line else ""
                    conf_str = f" [{conf}]" if conf else ""
                    parts.append(f"  #{i:02d} {func}{loc}{conf_str}")
            parts.append("")

        # Local variables
        locals_ = layer1.get("local_variables", {})
        if locals_ and not isinstance(locals_.get("status"), str):
            parts.append("## 局部变量")
            for name, info in locals_.items():
                if isinstance(info, dict):
                    val = info.get("value", "?")
                    typ = info.get("type", "")
                    parts.append(f"  {name} = {val} ({typ})")
            parts.append("")

    # Errors
    errors = data.get("errors", [])
    if errors:
        parts.append("## 采集错误")
        for e in errors:
            parts.append(f"  - {e}")
        parts.append("")

    # Task
    parts.append("## 分析任务")
    if target_type == "baremetal":
        parts.append("请分析以上嵌入式系统崩溃报告，输出：")
        parts.append("1. **崩溃原因**：结合 CFSR/HFSR 和寄存器值判断（HardFault/MemManage/BusFault/UsageFault）")
        parts.append("2. **崩溃位置**：具体函数和代码行的作用")
        parts.append("3. **调用路径分析**：从栈回溯推断执行路径")
        parts.append("4. **排查建议**：具体可操作的步骤")
        parts.append("5. **可能的修复方向**")
    else:
        parts.append("请分析以上崩溃报告，输出：")
        parts.append("1. **崩溃原因**：结合寄存器值和代码逻辑判断")
        parts.append("2. **崩溃位置**：具体函数和代码行的作用")
        parts.append("3. **调用路径分析**：从栈回溯推断执行路径")
        parts.append("4. **排查建议**：具体可操作的步骤")
        parts.append("5. **可能的修复方向**")

    return "\n".join(parts)


def analyze_json(filepath: str) -> str:
    """Build prompt from GDB bridge JSON file."""
    import json
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    return build_prompt_from_json(data)


def build_prompt(oops: OopsInfo, ctx: EnrichedContext, target_type: str = "generic") -> str:
    """Build a complete analysis prompt from oops info and enriched context."""
    parts = [get_system_prompt(target_type), ""]

    # Raw oops summary
    parts.append("## 崩溃摘要")
    parts.append(f"- 错误类型: {oops.error_type}")
    if oops.fault_address:
        parts.append(f"- 故障地址: {oops.fault_address}")
    parts.append(f"- 崩溃函数: {oops.crash_function}")
    if oops.crash_module:
        parts.append(f"- 所属模块: {oops.crash_module}")
    parts.append(f"- 架构: {oops.arch or 'unknown'}")
    parts.append("")

    # Key registers
    if oops.registers:
        parts.append("## 关键寄存器")
        interesting = _pick_interesting_registers(oops)
        for name, val in interesting:
            parts.append(f"  {name} = {val}")
        parts.append("")

    # Stack trace
    if oops.stack_trace:
        parts.append("## 栈回溯")
        for i, frame in enumerate(oops.stack_trace[:20]):
            mod = f" [{frame.module}]" if frame.module else ""
            offset = f"+{frame.offset}" if frame.offset else ""
            size = f"/{frame.size}" if frame.size else ""
            parts.append(f"  #{i:02d} {frame.function}{offset}{size}{mod}")
        parts.append("")

    # Enriched context
    enriched_text = context_to_text(ctx)
    if enriched_text:
        parts.append(enriched_text)
        parts.append("")

    # Task
    parts.append("## 分析任务")
    parts.append("请分析以上内核崩溃报告，输出：")
    parts.append("1. **崩溃原因**：结合寄存器值和代码逻辑判断")
    parts.append("2. **崩溃位置**：具体函数和代码行的作用")
    parts.append("3. **调用路径分析**：从栈回溯推断执行路径")
    parts.append("4. **排查建议**：具体可操作的步骤")
    parts.append("5. **可能的修复方向**")

    return "\n".join(parts)


def _pick_interesting_registers(oops: OopsInfo) -> list[tuple[str, str]]:
    """Pick registers most useful for crash analysis."""
    regs = oops.registers
    interesting = []

    # Always show these if present
    for name in ["pc", "lr", "sp", "pstate", "cpsr"]:
        if name in regs:
            interesting.append((name, regs[name]))

    # Show x0-x7/r0-r7 (function arguments)
    for i in range(8):
        for prefix in ["x", "r"]:
            key = f"{prefix}{i}"
            if key in regs:
                interesting.append((key, regs[key]))

    return interesting


def analyze_file(filepath: str, db_path: str = None) -> str:
    """Parse, enrich, and build prompt for an oops log file."""
    text = Path(filepath).read_text()
    oops = parse(text)

    kwargs = {}
    if db_path:
        kwargs["db_path"] = db_path
    ctx = enrich(oops, **kwargs)

    return build_prompt(oops, ctx)


if __name__ == "__main__":
    import argparse
    import json as _json

    argparser = argparse.ArgumentParser(description="Crash analyzer — builds analysis prompt")
    argparser.add_argument("file", help="Path to oops log file or GDB bridge JSON")
    argparser.add_argument("--db", default=None, help="Path to kernel-index SQLite database")
    argparser.add_argument("--output", "-o", default=None, help="Write prompt to file instead of stdout")
    args = argparser.parse_args()

    # Auto-detect input format
    filepath = Path(args.file)
    try:
        data = _json.loads(filepath.read_text(encoding="utf-8"))
        if "layer0" in data or "config" in data:
            prompt = build_prompt_from_json(data)
        else:
            prompt = analyze_file(args.file, args.db)
    except (_json.JSONDecodeError, UnicodeDecodeError):
        prompt = analyze_file(args.file, args.db)

    if args.output:
        Path(args.output).write_text(prompt, encoding="utf-8")
        print(f"Prompt written to {args.output}")
    else:
        print(prompt)
