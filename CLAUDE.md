# GDB-AI Bridge — GDB 调试与 AI 的桥接系统

## 项目目标

将 GDB 调试会话的上下文（寄存器、栈回溯、变量、内存）实时传递给 LLM，让 AI 能够辅助分析内核崩溃、驱动异常等调试场景。

## 核心问题

嵌入式调试高度依赖 GDB + JTAG/SWD，但 GDB 的输出是纯文本，信息密度高但人类阅读慢。AI 擅长分析结构化数据，但目前无法直接接入调试会话。

## 要解决的问题

- 内核 oops 了 → AI 分析栈回溯，定位到具体驱动和代码行
- HardFault → AI 结合寄存器状态判断是内存越界、空指针还是权限问题
- 变量值异常 → AI 追踪变量的赋值链路，找到异常来源
- 中断丢失 → AI 分析中断控制器寄存器状态

## 技术方向

1. **GDB Python 扩展**：编写 GDB Python 脚本，在断点/oops 时自动采集调试上下文
2. **上下文序列化**：将寄存器、栈帧、变量、内存 dump 结构化为 JSON
3. **LLM 分析**：将结构化上下文 + 源码片段发送给 Claude API 进行分析
4. **双向交互**：AI 可以通过 GDB 远程协议读取寄存器、设置断点（进阶）

## 架构设想

```
目标板 (ARM/RISC-V)
    ↕ JTAG/SWD
OpenOCD / J-Link GDB Server
    ↕ GDB Remote Protocol
GDB + Python Extension
    ↕ 采集调试上下文
Bridge Server (Python)
    ↕ Claude API
AI 分析 → 自然语言报告
```

## 竞品调研（2026-05）

**结论：MCP 是主流路径，但都不够嵌入式。**

已有的 GDB MCP 项目：
- **yywz1999/gdb-mcp-server** (83 star) — 最热门的 GDB MCP server
  - https://github.com/yywz1999/gdb-mcp-server
- **pansila/mcp_server_gdb** (65 star) — Rust 实现，稳定
  - https://github.com/pansila/mcp_server_gdb
- **smadi0x86/MDB-MCP** (61 star) — 支持 GDB + LLDB
  - https://github.com/smadi0x86/MDB-MCP
- **baidxi/mcp_for_gdbserver** (5 star) — 唯一针对远程嵌入式调试的
  - https://github.com/baidxi/mcp_for_gdbserver
- **karellen/karellen-rr-mcp** (3 star) — rr 时间回溯调试 MCP
  - https://github.com/karellen/karellen-rr-mcp

空白地带（我们的切入点）：
- 没有专门的内核 oops AI 分析器
- 没有 ARM/RISC-V 寄存器语义理解
- 没有内核 crash log 的 RAG 管道
- 没有 GDB Python 扩展专门预处理调试上下文供 LLM 消费
- 架构参考：LLM ↔ MCP server ↔ GDB/MI ↔ 目标板

## 关键文件/工具

- GDB Python API（gdb module）
- OpenOCD / J-Link GDB Server
- Claude API（Anthropic SDK）
- 现有 GDB MCP 项目（参考架构，可复用部分代码）
- Python HTTP/WebSocket server 做中间桥接

## 阶段规划

### Phase 1：离线分析 MVP
- 手动粘贴 oops log / GDB 输出
- AI 解析栈回溯，结合代码库给出分析
- 这一步不需要 GDB 扩展，纯 prompt engineering

### Phase 2：GDB Python 扩展
- 编写 GDB Python 脚本，自动采集上下文
- 在断点命中 / oops 发生时触发采集
- 输出结构化 JSON

### Phase 3：实时桥接
- Bridge Server 连接 GDB 和 Claude API
- 支持自动触发分析
- AI 的分析结果回传到 GDB 注释中

### Phase 4：双向控制（进阶）
- AI 可以发送 GDB 命令（读寄存器、设断点、step）
- 实现 AI 驱动的自动化调试流程
- 需要严格的安全限制，防止 AI 执行破坏性命令

## 验证方式

- 能解析一个真实的内核 oops log 并给出准确的崩溃位置
- GDB 扩展能在断点处自动采集上下文并输出 JSON
- AI 分析结果包含：崩溃原因、涉及的代码路径、建议的排查方向
