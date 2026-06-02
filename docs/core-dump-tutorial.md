# Core Dump 使用教程

## 什么是 Core Dump

Core dump 是进程/程序在某一时刻的内存快照。在嵌入式调试中，core dump 保存了崩溃瞬间的：

- **寄存器状态** — PC、SP、LR 等，定位崩溃位置
- **栈内存** — 函数调用链、局部变量
- **数据段** — 全局变量、静态变量的值

### Core Dump vs JSON

| 特性 | JSON (`ai dump`) | Core Dump (`ai coredump`) |
|------|-----------------|--------------------------|
| 内容 | 结构化摘要（寄存器、栈回溯、变量） | 原始内存快照 |
| 分析方式 | AI 直接读取 | GDB 打开，可交互 |
| 离线调试 | 只能看摘要 | 完整调试体验 |
| 文件大小 | 小（几 KB） | 大（几十~几百 KB） |
| 适用场景 | AI 自动分析 | 人工深入调试、分享给同事 |

**推荐工作流**：两者同时生成，JSON 给 AI 快速分析，core dump 给人深入调试。

## 快速上手

### 1. 生成 Core Dump

```gdb
(gdb) source gdb_bridge/gdb_bridge.py
(gdb) ai config arch arm target baremetal
(gdb) ai coredump crash.core
```

### 2. 离线分析

```bash
arm-none-eabi-gdb-py3 firmware.elf -c crash.core
(gdb) bt                    # 栈回溯
(gdb) info registers        # 寄存器
(gdb) print my_variable     # 读变量
```

### 3. 自动 Dump

```gdb
(gdb) ai auto on --coredump --dir ./crashes
# 崩溃时自动生成 .json + .core 文件
```

## 命令参考

### ai coredump

```
ai coredump <file> [--all] [--max-size N]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `<file>` | 输出文件路径（必填） | — |
| `--all` | dump 所有 RAM 区域 | 否（只 dump 栈+数据段） |
| `--max-size` | 最大文件大小（字节） | 67108864 (64MB) |

**默认模式**（不加 `--all`）：
- 栈内存（从 SP 到栈顶）
- `.data` 段
- `.bss` 段

**`--all` 模式**：
- dump 所有已知 RAM 区域
- 受 `--max-size` 限制，超出则拒绝

示例：
```gdb
(gdb) ai coredump crash.core                    # 默认：栈+数据段
(gdb) ai coredump crash.core --all              # 所有 RAM
(gdb) ai coredump crash.core --all --max-size 10485760  # 限制 10MB
```

### ai auto --coredump

```
ai auto on [--dir <path>] [--filter crash|all] [--coredump]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--dir` | 输出目录 | `.` |
| `--filter` | 触发条件 | `crash` |
| `--coredump` | 同时生成 core dump | 否 |

文件命名规则：
```
{dir}/auto_{count:05d}_{YYYYMMDD_HHMMSS}.json
{dir}/auto_{count:05d}_{YYYYMMDD_HHMMSS}.core
```

示例：
```gdb
(gdb) ai auto on --coredump --dir ./crashes
(gdb) ai auto on --coredump --filter all       # 所有 stop 事件都 dump
(gdb) ai auto off
(gdb) ai auto status
```

## 离线分析

### 基本用法

```bash
# 用固件 ELF + core dump 启动 GDB
arm-none-eabi-gdb-py3 firmware.elf -c crash.core

# 查看栈回溯
(gdb) bt

# 查看寄存器
(gdb) info registers

# 读取变量
(gdb) print global_counter
(gdb) print *task_handle

# 查看内存
(gdb) x/16x $sp
```

### 远程分析（分享给同事）

同事没有板子，只需要：
1. `firmware.elf`（编译产物，可从 CI 获取）
2. `crash.core`（你生成的 core dump）

```bash
# 同事的机器上
arm-none-eabi-gdb-py3 firmware.elf -c crash.core
(gdb) bt
# 就能看到崩溃现场
```

## AI 辑助分析

### 方式 1：MCP Server

如果你在 Claude Code 中配置了 MCP server：

```
# 直接让 AI 分析
请分析这个 core dump：crash.core
```

AI 会调用 `analyze_crash` 工具，结合 JSON 报告给出分析。

### 方式 2：离线分析 + AI

```bash
# 1. 生成 JSON 报告
(gdb) ai dump crash.json

# 2. 让 AI 分析
python analyzer.py crash.json
```

### 方式 3：GDB batch 模式提取信息

```bash
# 提取 core dump 信息给 AI
arm-none-eabi-gdb-py3 firmware.elf -c crash.core -batch \
  -ex "info registers" \
  -ex "bt" \
  -ex "info locals"
```

## 常见问题

### 文件太大

**问题**：core dump 文件几百 MB

**原因**：用了 `--all` 模式，dump 了整个 RAM

**解决**：
- 默认模式就够用（只 dump 栈+数据段）
- 用 `--max-size` 限制：`ai coredump crash.core --all --max-size 10485760`

### GDB 打不开

**问题**：`not a core dump file`

**原因**：文件损坏或格式错误

**排查**：
```bash
# 检查文件是否存在且非空
ls -lh crash.core

# 检查 ELF magic bytes
xxd crash.core | head -1
# 应该看到：7f 45 4c 46 01 01 01 00 ...
```

### 寄存器值不对

**问题**：寄存器全是零或明显不合理

**原因**：dump 时目标未 halt

**解决**：确保 dump 前目标已暂停：
```gdb
(gdb) monitor halt
(gdb) ai coredump crash.core
```

### 栈回溯不完整

**问题**：`bt` 只显示几帧就停了

**原因**：core dump 只保存了 SP 到栈顶的内容，如果调用链更深则丢失

**解决**：这是正常限制。如果需要完整回溯，在崩溃前用 `ai dump` 保存 JSON（GDB 实时采集时回溯更完整）。

### 变量读不到

**问题**：`print my_var` 报 "No symbol"

**原因**：变量被优化掉了，或不在 `.data`/`.bss` 段

**解决**：
- 编译时加 `-O0` 保留所有变量
- 用 `--all` 模式 dump 更多内存
