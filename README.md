# GDB-AI Bridge

> 将 GDB 调试会话与 AI 分析能力桥接，实现嵌入式崩溃的自动采集、分析和诊断。

**做什么**：GDB 采集崩溃上下文（寄存器、栈回溯、变量）→ 交给 AI → 自动分析崩溃原因。

**适用场景**：ARM Cortex-M/A 内核崩溃、HardFault 故障诊断、Linux 内核 oops、驱动 probe 失败。

**核心能力**：
- 离线分析：粘贴 oops log，AI 输出分析报告
- GDB 扩展：`ai collect` / `ai dump` / `ai auto` / `ai coredump` 命令自动采集崩溃上下文
- Core Dump：崩溃时保存内存快照为标准 ELF 文件，GDB 直接打开离线调试
- AI 调试循环：GDB + 串口 + AI 联动，自动诊断嵌入式故障
- SSH 远程调试：开发板在远程机器上，通过 SSH 执行 GDB 和读取串口
- MCP Server：AI agent 直接调用分析工具

## 快速开始

```bash
# 1. 安装（串口功能需要 pyserial，其他无依赖）
pip install pyserial

# 2. 离线分析 oops log
python analyzer.py oops.txt

# 3. GDB 中使用
(gdb) source gdb_bridge/gdb_bridge.py
(gdb) ai config arch arm target baremetal
(gdb) ai collect
(gdb) ai dump crash.json
```

### 前置条件

- Python 3.10+
- GDB（`arm-none-eabi-gdb-py3` 或 `gdb-multiarch`，需带 Python 支持）
- 调试服务器（OpenOCD / J-Link / pyOCD，用于连接目标板）

验证 GDB Python 支持：
```bash
arm-none-eabi-gdb-py3 --batch -ex "python print('OK')"
# 输出 OK 表示可用
```

## 使用方式

### 离线分析

分析 oops log 或 GDB bridge JSON，输出 AI 分析 prompt：

```bash
python analyzer.py oops.txt            # 分析 oops log
python analyzer.py crash.json          # 分析 GDB JSON
python analyzer.py oops.txt -o prompt.txt  # 输出到文件
```

### GDB 扩展命令

在 GDB 中加载 bridge 后，使用 `ai` 命令：

```gdb
(gdb) source gdb_bridge/gdb_bridge.py
(gdb) ai config arch arm target baremetal    # 配置架构
(gdb) ai collect                    # 采集上下文（打印）
(gdb) ai dump crash.json            # 采集并保存
(gdb) ai report crash.json          # 显示崩溃报告
(gdb) ai auto on --dir ./crashes    # 崩溃时自动采集
(gdb) ai auto on --coredump         # 崩溃时同时生成 core dump
(gdb) ai coredump crash.core        # 手动生成 ELF core dump
(gdb) ai serve 9999                 # 启动 HTTP API
```

| 命令 | 说明 |
|------|------|
| `ai config arch <a> target <t>` | 配置架构（arm/arm64）和目标（baremetal/linux） |
| `ai collect [--full]` | 手动采集上下文 |
| `ai dump <file> [--full]` | 采集并保存到 JSON |
| `ai report <file>` | 在 GDB 中显示崩溃报告 |
| `ai auto on\|off\|status` | 崩溃自动采集开关 |
| `ai coredump <file> [--all] [--max-size N]` | 生成 ELF core dump（内存快照） |
| `ai serve [port]` | 启动 HTTP API（默认 9999） |
| `ai exec <cmd>` | 执行 GDB 命令 |

### AI 调试循环

GDB + 串口 + AI 联动，自动诊断：

```python
from debug_loop.gdb_client import GDBClient
from debug_loop.serial_monitor import SerialMonitor
from debug_loop.loop import DebugLoop

client = GDBClient(port=9999)           # 连接 GDB HTTP API
mon = SerialMonitor("COM3", 115200)     # 读取串口
mon.start()

loop = DebugLoop(
    goal="从 I2C 传感器读取温度",
    expected={"serial_contains": "Temperature:"},
    serial_monitor=mon,
    gdb_client=client,
)
result = loop.run()
# {'status': 'success', 'reason': '...', 'iterations': 3}
```

### SSH 远程调试

开发板在远程机器上（实验室服务器、STM32MP157 A7 核等），通过 SSH 执行 GDB 命令和读取串口。

**依赖**：系统 `ssh` 命令（Windows 10+ 自带 OpenSSH，Linux/macOS 默认安装）。无额外 Python 包。

```python
from debug_loop.ssh_config import SSHConfig
from debug_loop import create_debug_loop

ssh = SSHConfig(host="192.168.1.100", user="root")

loop = create_debug_loop(
    goal="M4 HardFault 诊断",
    transport="ssh",
    ssh_config=ssh,
    remote_serial="/dev/ttySTM1",        # 远程串口
    gdb_command="gdb-multiarch",
    remote_elf="/home/root/m4_firmware.elf",
)
result = loop.run()
```

| SSH 参数 | 说明 | 默认值 |
|----------|------|--------|
| `host` | 远程主机（必填） | — |
| `user` | SSH 用户名 | 当前用户 |
| `port` | SSH 端口 | 22 |
| `key_file` | 私钥路径 | `~/.ssh/id_rsa` |
| `connect_timeout` | 连接超时（秒） | 10 |
| `control_master` | 连接复用 | True |

SSH 自动继承 `~/.ssh/config`，支持 ProxyJump、Agent Forwarding 等。

> **Phase 1 限制**：当前为 per-command 模式，适合崩溃分析。断点不跨调用保持，不支持 step-through。

## 架构适配

### 支持的架构

| 架构 | 适用芯片 | 特殊功能 |
|------|----------|----------|
| `arm` | Cortex-M0/M3/M4/M7/M33, Cortex-A7/A9 | SCB/CFSR/HFSR 故障解码 |
| `arm64` | Cortex-A53/A72/A76 | — |

### 支持的目标类型

| 目标 | 说明 | 栈回溯方式 |
|------|------|-----------|
| `baremetal` | 裸机 / RTOS（FreeRTOS、Zephyr） | GDB frame chain |
| `linux` | Linux 内核 | GDB bt + kallsyms |

### 调试服务器

本项目不绑定调试服务器，通过 GDB `target remote` 连接。常用：

- **OpenOCD**（推荐）：`openocd -f your.cfg` → 监听 3333
- **J-Link**：`JLinkGDBServer -device <chip> -if SWD` → 监听 2331
- **pyOCD**：`pyocd gdbserver --target <chip>` → 监听 3333

详细配置见 [配置指南](docs/config-guide.md)。

### Core Dump

崩溃时保存内存快照为标准 ELF 文件，可离线用 GDB 分析：

```gdb
(gdb) ai coredump crash.core              # 手动 dump
(gdb) ai auto on --coredump               # 崩溃时自动 dump
```

离线分析：
```bash
arm-none-eabi-gdb-py3 firmware.elf -c crash.core
(gdb) bt
(gdb) info registers
```

详见 [Core Dump 教程](docs/core-dump-tutorial.md)。

## MCP Server

内置 MCP server，让 AI agent 直接调用分析工具：

```bash
python mcp_server.py
```

| 工具 | 说明 |
|------|------|
| `parse_oops` | 解析 oops log / GDB JSON → 结构化数据 |
| `analyze_crash` | 完整分析管线（解析 + 符号查询 + prompt 生成） |
| `list_actions` | 列出可用调试动作（12 种） |
| `translate_action` | 结构化动作 → GDB 命令 |
| `get_system_prompt` | 获取目标类型的系统提示 |

Claude Code 中配置（`.mcp.json`）：
```json
{
  "mcpServers": {
    "gdb-ai-bridge": {
      "command": "python",
      "args": ["mcp_server.py"]
    }
  }
}
```

## 文件结构

```
gdb-ai-bridge/
├── parser.py                  # oops log 解析器
├── enricher.py                # 符号查询
├── analyzer.py                # AI prompt 构建
├── mcp_server.py              # MCP server（5 个工具）
├── gdb_bridge/                # GDB Python 扩展
│   ├── gdb_bridge.py          # 命令注册 + HTTP API
│   ├── collector.py           # 分层采集器
│   ├── coredump.py            # ELF core dump 构建器
│   ├── arch/                  # 架构适配器（arm, arm64）
│   └── target/                # 目标适配器（baremetal, linux）
├── debug_loop/                # AI 调试循环
│   ├── loop.py                # 主循环
│   ├── gdb_client.py          # GDB HTTP 客户端
│   ├── serial_monitor.py      # 本地串口
│   ├── ssh_config.py          # SSH 配置
│   ├── ssh_gdb_client.py      # SSH GDB 客户端
│   ├── ssh_serial_monitor.py  # SSH 远程串口
│   └── actions.py             # 结构化动作（12 种）
├── skills/                    # Claude Code skills
│   └── analyze-crash.md       # 崩溃分析 skill
└── tests/                     # 416 个测试
```

## 测试

```bash
python -m pytest tests/ -v    # 运行所有 416 个测试
```

## 常见问题

**Q: GDB 没有 Python 支持？**
A: 安装 xPack ARM GCC：`winget install xPack.arm-none-eabi-gcc`

**Q: 换芯片需要改代码？**
A: 不需要。改 OpenOCD 配置 + `ai config arch/target` 即可。

**Q: SSH 连接慢？**
A: SSHConfig 默认开启 ControlMaster，首次连接后后续命令几乎零开销。

**Q: 支持 RISC-V？**
A: 架构适配器接口已定义，`arch/riscv.py` 还没实现。欢迎贡献。
