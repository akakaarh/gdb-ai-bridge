# GDB-AI Bridge

将 GDB 调试会话与 AI 分析能力桥接，实现嵌入式崩溃的自动采集、分析和诊断。

## 快速开始

### 前置条件

- Python 3.10+
- xPack OpenOCD（已安装）
- xPack arm-none-eabi-gcc（含 GDB Python 支持）
- STM32MP157 开发板 + CMSIS-DAP/SWD 调试器

### Phase 1：离线分析（手动粘贴 oops log）

```bash
# 解析 oops log，生成 AI 分析 prompt
python analyzer.py tests/fixtures/sample_oops.txt

# 解析 GDB bridge JSON
python analyzer.py tests/fixtures/m4_hardfault.json
```

### Phase 2：GDB 自动采集

启动 OpenOCD：
```bash
openocd -f scripts/openocd_m4.cfg
```

连接 GDB 并加载 bridge：
```gdb
arm-none-eabi-gdb-py3
(gdb) target remote localhost:3334
(gdb) source gdb_bridge/gdb_bridge.py
(gdb) ai config arch arm target baremetal
(gdb) file your_firmware.elf
(gdb) load
```

手动采集：
```gdb
(gdb) ai collect              # 打印到控制台
(gdb) ai dump crash.json      # 保存到文件
(gdb) ai report crash.json    # 在 GDB 中显示报告
```

### Phase 3：崩溃自动采集

```gdb
(gdb) ai auto on --dir ./crashes    # 启用自动采集
(gdb) continue                       # 运行程序
# 崩溃时自动采集 → 保存 JSON → 打印报告
(gdb) ai auto off                    # 禁用
```

### Phase 4：AI 双向控制

启动 GDB HTTP API：
```gdb
(gdb) ai serve 9999    # 启动 HTTP server
(gdb) ai exec info registers  # 执行 GDB 命令
```

HTTP API 端点：

| 方法 | 路径 | 说明 | 请求体 |
|------|------|------|--------|
| GET | `/health` | 健康检查 | — |
| GET | `/state` | 获取 GDB 状态 | — |
| POST | `/execute` | 执行 GDB 命令 | `{"command": "info registers"}` |

Python 客户端调用：
```python
from debug_loop.gdb_client import GDBClient

client = GDBClient(port=9999)
print(client.health())           # {'ok': True}
print(client.get_state())        # {'status': 'stopped', 'pc': '0x...', 'arch': 'armv7e-m'}
print(client.read_all_registers())
print(client.execute("backtrace"))
print(client.backtrace())        # 便捷方法，等价于 execute("backtrace")
```

串口监听：
```python
from debug_loop.serial_monitor import SerialMonitor

mon = SerialMonitor('COM3', 115200)
mon.start()
# ... 运行程序 ...
output = mon.read_new_lines()  # 读取 UART 输出
mon.stop()
```

完整调试循环：
```python
from debug_loop.loop import DebugLoop

loop = DebugLoop(
    goal="从 I2C 传感器读取温度",
    expected={"serial_contains": "Temperature:"},
    serial_monitor=mon,
    gdb_client=client,
)
result = loop.run()
# {'status': 'success', 'reason': 'Expected output found', 'iterations': 3}
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

## 文件结构

```
gdb-ai-bridge/
├── parser.py              # oops log 解析器（ARM32/ARM64）
├── enricher.py            # kernel-index 符号查询
├── analyzer.py            # AI 分析 prompt 构建
├── gdb_bridge/            # GDB Python 扩展
│   ├── gdb_bridge.py      # GDB 命令注册
│   ├── collector.py       # 分层采集器
│   ├── output.py          # JSON 输出
│   ├── arch/              # 架构适配器
│   └── target/            # 目标适配器
├── debug_loop/            # AI 调试循环
│   ├── loop.py            # 主循环
│   ├── serial_monitor.py  # UART 监听
│   ├── gdb_client.py      # GDB HTTP 客户端
│   ├── evaluator.py       # 成功/失败判断
│   ├── safety.py          # 安全限制
│   └── actions.py         # 结构化动作
├── scripts/               # GDB/OpenOCD 脚本
└── tests/                 # 170 个测试
```

## 支持的目标类型

| 架构 | 适配器 | 特性 |
|------|--------|------|
| ARM Cortex-M | `arch/arm` | SCB/CFSR/HFSR 解码、异常帧、寄存器角色 |
| ARM Cortex-A 32-bit | `arch/arm` | 寄存器读取、栈回溯 |
| ARM Cortex-A 64-bit | `arch/arm64` | X0-X30、PSTATE |
| 裸机 | `target/baremetal` | FP 链遍历、ELF 符号 |
| Linux 内核 | `target/linux` | bt 解析、kallsyms |

## 测试

```bash
python -m pytest tests/ -v    # 运行所有 170 个测试
```
