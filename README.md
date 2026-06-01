# GDB-AI Bridge

将 GDB 调试会话与 AI 分析能力桥接，实现嵌入式崩溃的自动采集、分析和诊断。

## 快速开始

### 安装

```bash
pip install pyserial     # 串口监听（Phase 4 需要）
```

### 前置条件

- Python 3.10+
- 带 Python 支持的 GDB（`arm-none-eabi-gdb-py3` 或 `gdb-multiarch`）
- 调试服务器（OpenOCD / J-Link GDB Server / pyOCD）
- 目标板 + 调试器

## 配置指南

### 调试服务器配置

本项目通过 GDB 连接目标板，不绑定特定调试服务器。以下是常见配置：

#### OpenOCD（推荐，开源免费）

```bash
# 安装（选择其一）
winget install xPack.openocd           # Windows xPack
sudo apt install openocd               # Linux
brew install open-ocd                  # macOS
```

配置文件按芯片写，示例：

```cfg
# stm32f4.cfg — STM32F4 系列
adapter driver cmsis-dap    # 或 stlink、jlink
transport select swd
adapter speed 4000
source [find target/stm32f4x.cfg]
```

启动：
```bash
openocd -f your_config.cfg
# 成功后监听 GDB 端口（默认 3333）
```

#### J-Link GDB Server

```bash
JLinkGDBServer -device STM32F407VG -if SWD -speed 4000
# 默认监听端口 2331
```

#### pyOCD

```bash
pip install pyocd
pyocd gdbserver --target stm32f407vg --frequency 4000000
# 默认监听端口 3333
```

### GDB 配置

| 工具链 | GDB 命令 | Python 支持 | 安装 |
|--------|----------|-------------|------|
| xPack ARM GCC | `arm-none-eabi-gdb-py3` | Python 3.13 | `winget install xPack.arm-none-eabi-gcc` |
| ARM 官方 | `arm-none-eabi-gdb` | 看版本 | https://developer.arm.com/downloads/-/gnu-rm |
| STM32CubeIDE | 内置 GDB | 自带 | 随 IDE 安装 |
| gdb-multiarch | `gdb-multiarch` | 看系统 | `sudo apt install gdb-multiarch` |

验证 Python 支持：
```bash
arm-none-eabi-gdb-py3 --batch -ex "python print('OK')"
# 输出 OK 表示可用
```

### 芯片适配

```gdb
# 1. 启动调试服务器
# 2. 连接 GDB
(gdb) target remote localhost:3333
# 3. 加载 bridge
(gdb) source /path/to/gdb_bridge/gdb_bridge.py
# 4. 配置架构和目标类型
(gdb) ai config arch arm target baremetal
# 5. 加载符号文件
(gdb) file your_firmware.elf
# 6. 使用
(gdb) ai collect
(gdb) ai dump crash.json
```

| 架构 | 适用芯片 | 特殊功能 |
|------|----------|----------|
| `arm` | Cortex-M/A (32-bit) | SCB/CFSR/HFSR 解码 |
| `arm64` | Cortex-A (64-bit) | — |

| 目标 | 说明 |
|------|------|
| `baremetal` | 裸机 / RTOS |
| `linux` | Linux 内核 |

## 使用方式

### Phase 1：离线分析

```bash
python analyzer.py oops.txt
python analyzer.py crash.json
```

### Phase 2：GDB 自动采集

```gdb
(gdb) ai config arch arm target baremetal
(gdb) ai collect
(gdb) ai dump crash.json
(gdb) ai report crash.json
```

### Phase 3：崩溃自动采集

```gdb
(gdb) ai auto on --dir ./crashes
(gdb) continue
# 崩溃时自动：采集 → 保存 JSON → 打印报告
(gdb) ai auto off
```

### Phase 4：AI 双向控制

```gdb
(gdb) ai serve 9999
```

```python
from debug_loop.gdb_client import GDBClient
from debug_loop.serial_monitor import SerialMonitor
from debug_loop.loop import DebugLoop

client = GDBClient(port=9999)
mon = SerialMonitor("COM3", 115200)
mon.start()

loop = DebugLoop(
    goal="从 I2C 传感器读取温度",
    expected={"serial_contains": "Temperature:"},
    serial_monitor=mon,
    gdb_client=client,
)
result = loop.run()
```

### SSH 远程调试

当开发板接在远程机器上（实验室服务器、STM32MP157 A7 核等），通过 SSH 远程执行 GDB 命令和读取串口。

**依赖**：系统 `ssh` 命令（Windows 10+ 自带 OpenSSH，Linux/macOS 默认安装）。无额外 Python 包。

```python
from debug_loop.ssh_config import SSHConfig
from debug_loop import create_debug_loop

ssh = SSHConfig(host="192.168.1.100", user="root")

loop = create_debug_loop(
    goal="分析 M4 崩溃",
    transport="ssh",
    ssh_config=ssh,
    remote_serial="/dev/ttySTM1",
    gdb_command="gdb-multiarch",
    remote_elf="/home/root/firmware.elf",
)
result = loop.run()
```

**STM32MP157 示例**（A7 核 SSH 调试 M4）：

```python
ssh = SSHConfig(host="192.168.1.100", user="root")
loop = create_debug_loop(
    goal="M4 HardFault 诊断",
    transport="ssh",
    ssh_config=ssh,
    remote_serial="/dev/ttySTM1",
    gdb_command="gdb-multiarch",
    remote_elf="/home/root/m4_firmware.elf",
)
```

**SSH 配置选项**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `host` | 远程主机（必填） | — |
| `user` | SSH 用户名 | 当前用户 |
| `port` | SSH 端口 | 22 |
| `key_file` | 私钥路径 | `~/.ssh/id_rsa` |
| `connect_timeout` | 连接超时（秒） | 10 |
| `control_master` | 连接复用 | True |
| `options` | 额外 SSH 选项 | `{}` |

SSH 自动继承 `~/.ssh/config`，支持 ProxyJump、Agent Forwarding 等。

**Phase 1 限制**：当前为 per-command 模式（每次 GDB 命令一个 SSH 调用），适合崩溃分析场景。断点状态不跨调用保持，不支持 step-through 调试。

## GDB 命令参考

| 命令 | 说明 |
|------|------|
| `ai info` | 显示当前配置 |
| `ai config arch <arch> target <target>` | 配置架构和目标类型 |
| `ai collect [--full]` | 手动采集上下文 |
| `ai dump <file> [--full]` | 采集并保存到 JSON |
| `ai report <file>` | 在 GDB 中显示崩溃报告 |
| `ai auto on\|off\|status` | 崩溃自动采集开关 |
| `ai serve [port]` | 启动 HTTP API（默认 9999） |
| `ai exec <command>` | 执行 GDB 命令 |

## HTTP API 端点

| 方法 | 路径 | 说明 | 请求体 |
|------|------|------|--------|
| GET | `/health` | 健康检查 | — |
| GET | `/state` | 获取 GDB 状态 | — |
| POST | `/execute` | 执行 GDB 命令 | `{"command": "info registers"}` |

## MCP Server

```bash
python mcp_server.py
```

| 工具 | 说明 |
|------|------|
| `parse_oops` | 解析 oops log / GDB JSON |
| `analyze_crash` | 完整分析管线 |
| `list_actions` | 列出调试动作（12 种） |
| `translate_action` | 动作 → GDB 命令 |
| `get_system_prompt` | 目标类型系统提示 |

## 文件结构

```
gdb-ai-bridge/
├── parser.py                  # oops log 解析器
├── enricher.py                # 符号查询
├── analyzer.py                # prompt 构建
├── mcp_server.py              # MCP server
├── gdb_bridge/                # GDB Python 扩展
│   ├── gdb_bridge.py          # 命令 + HTTP API
│   ├── collector.py           # 分层采集器
│   ├── arch/                  # 架构适配器
│   └── target/                # 目标适配器
├── debug_loop/                # AI 调试循环
│   ├── loop.py                # 主循环
│   ├── serial_monitor.py      # 本地串口
│   ├── gdb_client.py          # HTTP 客户端
│   ├── ssh_config.py          # SSH 配置
│   ├── ssh_gdb_client.py      # SSH GDB 客户端
│   ├── ssh_serial_monitor.py  # SSH 远程串口
│   ├── evaluator.py           # 成功判断
│   ├── safety.py              # 安全限制
│   └── actions.py             # 结构化动作
├── skills/                    # Claude Code skills
└── tests/                     # 220 个测试
```

## 测试

```bash
python -m pytest tests/ -v    # 运行所有 220 个测试
```

## 常见问题

**Q: SSH 连接慢怎么办？**
A: SSHConfig 默认开启 ControlMaster 连接复用，首次连接后后续命令几乎零开销。

**Q: SSH 远程调试支持断点吗？**
A: Phase 1 为 per-command 模式，断点不跨调用保持。适合崩溃分析。交互式调试将在 Phase 2 支持。

**Q: 换芯片需要改代码吗？**
A: 不需要。只改 OpenOCD 配置和 `ai config arch/target` 即可。
