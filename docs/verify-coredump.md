# Core Dump 实板验证指南

在 STM32MP157 Cortex-M4 上验证 ELF core dump 功能。

## 前置条件

- STM32MP157 开发板，M4 核运行固件
- OpenOCD 已配置并运行
- `arm-none-eabi-gdb-py3` 或 `gdb-multiarch`（带 Python 支持）
- 固件 ELF 文件（含调试信息）

## 验证步骤

### Step 1: 启动调试环境

终端 1 — 启动 OpenOCD：
```bash
openocd -f /usr/share/openocd/scripts/board/stm32mp15x_m4_ev1.cfg
# 或你的自定义配置
```

终端 2 — 启动 GDB 并连接：
```bash
arm-none-eabi-gdb-py3 your_firmware.elf
(gdb) target remote :3333
(gdb) monitor reset halt
(gdb) continue
# 等固件运行到某个状态后 Ctrl+C 中断
```

### Step 2: 加载 GDB Bridge

```gdb
(gdb) source /path/to/gdb-ai-bridge/gdb_bridge/gdb_bridge.py
(gdb) ai config arch arm target baremetal
```

### Step 3: 手动 Core Dump

```gdb
(gdb) ai coredump test.core
# 预期输出：status ok, 文件已保存
```

### Step 4: 验证 Core Dump 文件

退出 GDB，用 core dump 重新打开：

```bash
arm-none-eabi-gdb-py3 your_firmware.elf -c test.core
```

在 GDB 中验证：

```gdb
(gdb) bt
# 预期：显示栈回溯，应该和中断时一致

(gdb) info registers
# 预期：显示寄存器值，PC/SP/LR 合理

(gdb) print some_variable
# 预期：能读取变量值（如果在 .data/.bss 段中）
```

### Step 5: 验证 --all 模式

重新连接板子：

```gdb
(gdb) ai coredump test_all.core --all
# 预期：dump 所有 RAM 区域
```

验证文件大小合理（不应超过芯片 RAM 总量）：
```bash
ls -lh test_all.core
# STM32MP157 M4: SRAM 256KB，文件应在 300KB 左右
```

### Step 6: 验证自动 Core Dump

```gdb
(gdb) ai auto on --coredump --dir ./crashes
# 预期：auto mode enabled, coredump=true

(gdb) continue
# 让固件崩溃（或手动触发 HardFault）

# 崩溃后检查：
(gdb) ai auto status
# 预期：显示已采集次数

(gdb) shell ls ./crashes/
# 预期：看到 auto_00001_*.json 和 auto_00001_*.core
```

用 core dump 验证：
```bash
arm-none-eabi-gdb-py3 your_firmware.elf -c ./crashes/auto_00001_*.core
(gdb) bt
(gdb) info registers
```

## 检查清单

逐项确认：

- [ ] `ai coredump test.core` 生成文件，无报错
- [ ] GDB 能用 `-c test.core` 正常打开
- [ ] `bt` 显示正确的栈回溯
- [ ] `info registers` 寄存器值合理（PC 在代码范围内，SP 在 RAM 范围内）
- [ ] `print` 能读取全局变量
- [ ] `--all` 模式文件大小合理
- [ ] `ai auto --coredump` 崩溃后自动生成 `.core` 文件
- [ ] 自动 dump 的 core 文件能被 GDB 正常打开

## 常见问题

**GDB 报 "not a core dump"**
- 检查文件是否为空：`ls -l test.core`
- 检查 ELF magic bytes：`xxd test.core | head -1`（应以 `7f 45 4c 46` 开头）

**寄存器值全为零**
- 可能是采集时目标未 halt，先 `monitor halt` 再 dump

**栈回溯不完整**
- 正常现象，core dump 只保存了从 SP 到栈顶的内容
- 如果 SP 被破坏，回溯会截断

**文件特别大（>10MB）**
- `--all` 模式会 dump 所有 RAM，检查 `--max-size` 限制
- 默认模式只 dump 栈+.data+.bss，不应该很大
