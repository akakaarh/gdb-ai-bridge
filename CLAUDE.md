# GDB-AI Bridge — GDB 调试与 AI 的桥接系统

## 项目状态：已完成 ✅

GitHub: https://github.com/akakaarh/gdb-ai-bridge
187 个测试，16 个 commit，Phase 1-4 全部实现。

## 项目目标

将 GDB 调试会话的上下文（寄存器、栈回溯、变量、内存）实时传递给 LLM，让 AI 能够辅助分析内核崩溃、驱动异常等调试场景。

## 核心能力

### Phase 1：离线分析
- `parser.py` — 解析 ARM32/ARM64 oops log，提取寄存器、栈回溯、崩溃函数
- `enricher.py` — 从 kernel-index SQLite 查询符号定义、调用链
- `analyzer.py` — 构建 AI 分析 prompt（支持 baremetal/linux/generic 三种目标）

### Phase 2：GDB 自动采集
- `gdb_bridge/gdb_bridge.py` — GDB Python 扩展，9 个命令
- `gdb_bridge/collector.py` — 分层采集（L0: 寄存器, L1: 栈/变量, L2: 完整 dump）
- `gdb_bridge/arch/arm.py` — Cortex-M SCB/CFSR/HFSR 故障解码
- `gdb_bridge/target/baremetal.py` — 裸机帧链遍历
- `gdb_bridge/target/linux.py` — Linux 内核 bt 解析

### Phase 3：结果回传
- `ai report <file>` — 在 GDB 中显示结构化崩溃报告
- `ai auto on` — 崩溃时自动采集 + 保存 JSON + 打印报告

### Phase 4：AI 双向控制
- `ai serve [port]` — GDB HTTP API（/health, /state, /execute）
- `debug_loop/` — AI 调试循环（串口监听 + GDB 控制 + 安全限制）
- 结构化动作系统（12 种动作，白名单安全机制）

### MCP Server
- `mcp_server.py` — 5 个 MCP 工具（parse_oops, analyze_crash, list_actions, translate_action, get_system_prompt）

### Skill
- `skills/analyze-crash.md` — Claude Code skill，粘贴 oops log 自动触发分析

## GDB 命令

| 命令 | 说明 |
|------|------|
| `ai config arch <a> target <t>` | 配置架构和目标类型 |
| `ai collect [--full]` | 手动采集上下文 |
| `ai dump <file>` | 采集并保存到 JSON |
| `ai report <file>` | 在 GDB 中显示崩溃报告 |
| `ai auto on\|off` | 崩溃自动采集开关 |
| `ai serve [port]` | 启动 HTTP API |
| `ai exec <cmd>` | 执行 GDB 命令 |

## 技术栈

- Python 3.10+，无第三方依赖（pyserial 可选）
- GDB Python API（需带 Python 支持的 GDB）
- OpenOCD / J-Link / pyOCD（调试服务器）
- FastMCP（MCP server）
- Claude Code（AI 分析）

## 适用场景

- ARM Cortex-M/A 内核崩溃分析
- HardFault 故障诊断（CFSR 位域解码）
- Linux 内核 oops 栈回溯解读
- 驱动 probe 失败分析
- 嵌入式调试自动化

## 不限于 STM32MP157

架构适配器模式支持任何芯片：
- 换 OpenOCD 配置 + `ai config arch target` 即可
- 已验证：STM32MP157 (Cortex-A7 + Cortex-M4)
- 支持：任何 ARM Cortex-M/A、任何 OpenOCD/J-Link 支持的芯片

## 关键文件

- `E:/projects/gdb-ai-bridge/` — 项目根目录
- `E:/projects/kernel-code-index/` — 内核符号索引（drivers/gpio 子系统）
- `E:\Wiki\embedded\` — 嵌入式知识库（90 篇文档）
