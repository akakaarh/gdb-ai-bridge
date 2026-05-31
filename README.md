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

```cfg
# nrf52.cfg — Nordic nRF52 系列
adapter driver jlink
transport select swd
adapter speed 4000
source [find target/nrf52.cfg]
```

```cfg
# rp2040.cfg — Raspberry Pi Pico
adapter driver cmsis-dap
transport select swd
adapter speed 4000
source [find target/rp2040.cfg]
```

启动：
```bash
openocd -f your_config.cfg
# 成功后监听 GDB 端口（默认 3333）
```

#### J-Link GDB Server

```bash
# 启动（GUI 或命令行）
JLinkGDBServer -device STM32F407VG -if SWD -speed 4000
# 默认监听端口 2331
```

GDB 连接时用 `target remote localhost:2331`。

#### pyOCD

```bash
pip install pyocd
pyocd gdbserver --target stm32f407vg --frequency 4000000
# 默认监听端口 3333
```

### GDB 配置

#### 带 Python 支持的 GDB

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

#### 通用流程（适用所有芯片）

```gdb
# 1. 启动调试服务器（OpenOCD/J-Link/pyOCD）
# 2. 连接 GDB
(gdb) target remote localhost:3333

# 3. 加载 bridge
(gdb) source /path/to/gdb_bridge/gdb_bridge.py

# 4. 配置架构和目标类型
(gdb) ai config arch arm target baremetal    # Cortex-M 裸机
(gdb) ai config arch arm target linux         # Cortex-A Linux
(gdb) ai config arch arm64 target linux       # AArch64 Linux

# 5. 加载符号文件
(gdb) file your_firmware.elf                  # 裸机
(gdb) add-symbol-file vmlinux                 # Linux 内核

# 6. 使用
(gdb) ai collect
(gdb) ai dump crash.json
```

#### 架构选项

| 架构 | 适用芯片 | 寄存器 | 特殊功能 |
|------|----------|--------|----------|
| `arm` | Cortex-M0/M3/M4/M7/M33, Cortex-A7/A9 (32-bit) | R0-R15, xPSR | SCB/CFSR/HFSR 解码（Cortex-M） |
| `arm64` | Cortex-A53/A72/A76 (64-bit) | X0-X30, SP, PSTATE | — |

#### 目标类型选项

| 目标 | 说明 | 栈回溯方式 |
|------|------|-----------|
| `baremetal` | 裸机 / RTOS（FreeRTOS、Zephyr） | GDB frame chain 遍历 |
| `linux` | Linux 内核 | GDB bt + kallsyms |

### 串口配置（Phase 4 需要）

```python
from debug_loop.serial_monitor import SerialMonitor

# 查看可用串口
import serial.tools.list_ports
for p in serial.tools.list_ports.comports():
    print(f"{p.device}: {p.description}")

# 连接（按实际端口和波特率修改）
mon = SerialMonitor("COM3", 115200)  # Windows
mon = SerialMonitor("/dev/ttyUSB0", 115200)  # Linux
mon.start()
```

### 安全内存区域（可选）

如果需要 MMIO 保护（防止读取外设寄存器产生副作用），在 `collector.py` 中配置：

```python
# 按你的芯片修改
collector.set_safe_regions([
    (0x20000000, 0x20020000),  # SRAM
    (0x08000000, 0x08100000),  # Flash
])
```

或从 ELF 自动解析：
```python
collector.load_safe_regions_from_elf("firmware.elf")
```

## 使用方式

### Phase 1：离线分析

```bash
# 分析 oops log
python analyzer.py oops.txt

# 分析 GDB bridge JSON
python analyzer.py crash.json

# 输出到文件
python analyzer.py oops.txt -o prompt.txt
```

### Phase 2：GDB 自动采集

```gdb
(gdb) ai config arch arm target baremetal
(gdb) ai collect                    # 打印到控制台
(gdb) ai dump crash.json            # 保存到文件
(gdb) ai report crash.json          # 在 GDB 中显示报告
```

### Phase 3：崩溃自动采集

```gdb
(gdb) ai auto on --dir ./crashes    # 启用
(gdb) continue                       # 运行
# 崩溃时自动：采集 → 保存 JSON → 打印报告
(gdb) ai auto off                    # 禁用
```

### Phase 4：AI 双向控制

```gdb
(gdb) ai serve 9999                 # 启动 HTTP API
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
print(result)  # {'status': 'success', 'reason': '...', 'iterations': 3}
```

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

内置 MCP server，让 AI agent 直接调用分析工具。

```bash
# 启动（stdio 模式）
python mcp_server.py

# 或在 Claude Code 中配置（.mcp.json 已自动生成）
```

### MCP 工具

| 工具 | 说明 |
|------|------|
| `parse_oops` | 解析 oops log / GDB JSON → 结构化数据 |
| `analyze_crash` | 完整分析管线（解析 + enrich + prompt 生成） |
| `list_actions` | 列出可用的调试动作（12 种） |
| `translate_action` | 结构化动作 → GDB 命令 |
| `get_system_prompt` | 获取目标类型的系统提示 |

### 在 Claude Code 中使用

将以下内容添加到 Claude Code 的 MCP 配置（`.claude/settings.json`）：

```json
{
  "mcpServers": {
    "gdb-ai-bridge": {
      "command": "python",
      "args": ["/path/to/gdb-ai-bridge/mcp_server.py"]
    }
  }
}
```

## Skill

Claude Code 内置 skill，粘贴 oops log 自动触发分析。

文件：`skills/analyze-crash.md`

触发条件：
- 用户粘贴内核 oops log 或 panic 输出
- 提到 "kernel panic"、"HardFault"、"oops"、"crash analysis"
- 有 GDB bridge JSON 文件需要分析

## 文件结构

```
gdb-ai-bridge/
├── parser.py              # oops log 解析器（ARM32/ARM64）
├── enricher.py            # kernel-index 符号查询
├── analyzer.py            # AI 分析 prompt 构建
├── mcp_server.py          # MCP server（5 个工具）
├── gdb_bridge/            # GDB Python 扩展
│   ├── gdb_bridge.py      # GDB 命令注册 + HTTP API
│   ├── collector.py       # 分层采集器
│   ├── output.py          # JSON 输出
│   ├── arch/              # 架构适配器（arm, arm64）
│   └── target/            # 目标适配器（baremetal, linux）
├── debug_loop/            # AI 调试循环
│   ├── loop.py            # 主循环
│   ├── serial_monitor.py  # UART 监听
│   ├── gdb_client.py      # GDB HTTP 客户端
│   ├── evaluator.py       # 成功/失败判断
│   ├── safety.py          # 安全限制
│   └── actions.py         # 结构化动作
├── skills/                # Claude Code skills
│   └── analyze-crash.md   # 崩溃分析 skill
├── scripts/               # OpenOCD 配置示例
└── tests/                 # 187 个测试
```

## 测试

```bash
python -m pytest tests/ -v    # 运行所有 187 个测试
```

## 常见问题

**Q: GDB 没有 Python 支持怎么办？**
A: 安装 xPack ARM GCC（自带 gdb-py3）：`winget install xPack.arm-none-eabi-gcc`

**Q: OpenOCD 连不上目标板？**
A: 检查：1) SWD/JTAG 接线 2) 驱动（Windows 可能需要 Zadig） 3) `adapter speed` 降到 1000 试试

**Q: `ai collect` 报错 "No debug symbols"？**
A: 编译时加 `-g` 选项，然后在 GDB 中 `file your_firmware.elf` 加载符号

**Q: 换芯片需要改代码吗？**
A: 不需要。只改 OpenOCD 配置文件和 `ai config arch/target` 即可

**Q: 支持 RISC-V 吗？**
A: 架构适配器接口已定义，但 `arch/riscv.py` 还没实现。欢迎贡献
