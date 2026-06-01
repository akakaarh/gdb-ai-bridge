# SSH Debugging Transport — Design Spec

## Context

GDB-AI Bridge 的所有连接目前都是 localhost：GDB HTTP API 绑定 localhost，串口是本地 COM 口。
当开发板接在远程机器上（实验室服务器、构建服务器）时，用户需要 SSH 进去操作 GDB 和串口。
本次添加 SSH transport 层，让 `debug_loop` 能通过 SSH 连接到远程机器执行 GDB 命令和读取串口。

## 设计决策

### subprocess + ssh CLI（不用 paramiko）

- 零新依赖（`subprocess` 是 stdlib，`ssh` 是系统工具）
- 自动继承 `~/.ssh/config`（ProxyJump、Agent Forwarding 等全部生效）
- 用 SSH ControlMaster 复用连接，避免每次命令都重新握手
- paramiko 仅在 Windows 无 OpenSSH 时作为备选，v1 不实现

### DebugLoop 零改动

DebugLoop 用鸭子类型，SSH 类只要实现相同方法就是 drop-in 替换：

```python
# 本地用法（不变）
loop = DebugLoop(goal="...", serial_monitor=SerialMonitor("COM3"),
                 gdb_client=GDBClient("localhost", 9999))

# SSH 用法（新增）
ssh = SSHConfig(host="192.168.1.100", user="dev")
loop = DebugLoop(goal="...",
                 serial_monitor=SSHSerialMonitor(ssh, "/dev/ttyUSB0"),
                 gdb_client=SSHGDBClient(ssh, gdb_command="gdb-multiarch",
                                          remote_file="/home/dev/firmware.elf"))
```

### Phase 1: per-command SSH（本次）

每次 GDB 命令一个 SSH 调用：`ssh host "gdb -batch -ex 'info registers' firmware.elf"`

**优点**：简单，和现有 GDBClient 接口一致
**限制**：断点状态不保持，不支持 step-through 调试
**适用**：崩溃分析（registers + backtrace + 变量读取）

### Phase 2: 持久会话（后续）

升级为持久 SSH 会话（stdin/stdout 复用），支持 step/breakpoint 跨调用保持。
Phase 1 接口设计留好扩展点，升级时不需要改 DebugLoop。

## 新增文件

| 文件 | 用途 |
|------|------|
| `debug_loop/ssh_config.py` | SSHConfig 数据类，生成 `ssh` 命令前缀 |
| `debug_loop/ssh_gdb_client.py` | SSHGDBClient — per-command 模式 |
| `debug_loop/ssh_serial_monitor.py` | SSHSerialMonitor — 持久 SSH 进程读取远程串口 |
| `debug_loop/__init__.py` | 添加工厂函数 `create_debug_loop()` |
| `tests/test_ssh_config.py` | SSHConfig 单元测试 |
| `tests/test_ssh_gdb_client.py` | SSHGDBClient 测试（mock subprocess） |
| `tests/test_ssh_serial.py` | SSHSerialMonitor 测试（mock subprocess） |

## 接口定义

### SSHConfig (`ssh_config.py`)

```python
@dataclasses.dataclass
class SSHConfig:
    host: str                           # 必填：主机名或 IP
    user: str = ""                      # 可选：默认当前用户
    port: int = 22                      # 可选
    key_file: str = ""                  # 可选：私钥路径
    options: dict = dataclasses.field(default_factory=dict)  # 额外 SSH -o 选项
    connect_timeout: int = 10           # SSH 连接超时（秒）
    control_master: bool = True         # 使用 ControlMaster 复用连接
    control_path: str = ""              # 自动生成

    def ssh_prefix(self) -> list[str]:
        """生成 ssh 命令前缀，如 ['ssh', '-l', 'dev', '192.168.1.100']"""
        ...
```

### SSHGDBClient (`ssh_gdb_client.py`)

实现和 `GDBClient` 相同的鸭子类型接口：

```python
class SSHGDBClient:
    def __init__(self, ssh_config: SSHConfig, gdb_command: str = "gdb",
                 gdb_args: list[str] | None = None, remote_file: str = ""):
        ...

    def execute(self, command: str) -> str | None:
        """执行 GDB 命令，返回输出。失败返回 None。"""
        # ssh host "gdb -batch -ex 'command' remote_file"
        ...

    def get_state(self) -> dict:
        """获取当前 GDB/目标状态。"""
        # 批量执行多个 GDB 命令，减少 SSH 往返
        ...

    # 便捷方法（和 GDBClient 一致）
    def read_register(self, name) -> str | None: ...
    def read_all_registers(self) -> str | None: ...
    def read_variable(self, name) -> str | None: ...
    def read_memory(self, addr, count=1) -> str | None: ...
    def set_breakpoint(self, location) -> str | None: ...
    def delete_breakpoint(self, number) -> str | None: ...
    def step(self) -> str | None: ...
    def next(self) -> str | None: ...
    def continue_exec(self) -> str | None: ...
    def backtrace(self) -> str | None: ...
    def info_locals(self) -> str | None: ...
    def finish(self) -> str | None: ...
```

### SSHSerialMonitor (`ssh_serial_monitor.py`)

实现和 `SerialMonitor` 相同的鸭子类型接口：

```python
class SSHSerialMonitor:
    def __init__(self, ssh_config: SSHConfig, port: str, baudrate: int = 115200):
        ...

    def start(self):
        """启动远程串口监控。"""
        # ssh host "stty -F /dev/ttyUSB0 115200 raw -echo && cat /dev/ttyUSB0"
        ...

    def stop(self):
        """停止监控。"""
        ...

    def read_new_lines(self) -> str:
        """非阻塞读取新行。"""
        ...

    def read_output(self, timeout=3) -> str:
        """阻塞读取，最多等待 timeout 秒。"""
        ...

    def write(self, data):
        """向远程串口写入数据。"""
        # ssh host "printf '%s' 'data' > /dev/ttyUSB0"
        ...
```

### 工厂函数 (`__init__.py`)

```python
def create_debug_loop(goal, expected, transport="local",
                      serial_port=None, baudrate=115200,
                      gdb_host="localhost", gdb_port=9999,
                      ssh_config=None, remote_serial=None,
                      gdb_command="gdb", remote_elf=""):
    """创建 DebugLoop，支持 local 和 ssh 两种传输。"""
    if transport == "local":
        serial = SerialMonitor(serial_port, baudrate)
        gdb = GDBClient(gdb_host, gdb_port)
    elif transport == "ssh":
        serial = SSHSerialMonitor(ssh_config, remote_serial, baudrate)
        gdb = SSHGDBClient(ssh_config, gdb_command, remote_file=remote_elf)
    serial.start()
    return DebugLoop(goal, expected, serial, gdb)
```

## 错误处理

- SSH 连接失败：`execute()` 返回 `None`，匹配 `GDBClient` 行为
- SSH 连接断开：`SSHSerialMonitor._read_loop` 检测进程退出，自动停止
- 远程 GDB 崩溃：`gdb -batch` 非零退出，`execute()` 返回 `None`
- 超时：`subprocess.run(timeout=...)` 捕获 `TimeoutExpired`，返回 `None`
- ssh 不可用：`__init__` 检查 `shutil.which("ssh")`，不存在则抛出明确错误

## 测试策略

所有测试 mock `subprocess.run` 和 `subprocess.Popen`，不需要实际 SSH 连接。

### test_ssh_config.py
- `ssh_prefix()` 各种配置组合（有无 user/port/key_file/options）
- ControlMaster 路径生成
- 边界情况（空 host、port=22 不加 -p）

### test_ssh_gdb_client.py
- Mock `subprocess.run`，验证正确的远程命令字符串
- `execute()` 成功返回 stdout
- `execute()` 非零退出返回 None
- `execute()` 超时返回 None
- Shell 转义（单引号、特殊字符）
- `get_state()` 批量命令

### test_ssh_serial.py
- Mock `subprocess.Popen`，验证 `start()` 启动正确命令
- `_read_loop` 正确填充 buffer
- `read_new_lines()` 返回并清空 buffer
- `stop()` 终止进程并 join 线程
- 进程死亡检测

## 实现顺序 + 每步验收

### Step 1: `debug_loop/ssh_config.py` + `tests/test_ssh_config.py`

**实现**：SSHConfig 数据类，`ssh_prefix()` 方法
**验收**：
- [ ] `pytest tests/test_ssh_config.py -v` 全部通过
- [ ] `ssh_prefix()` 最小配置生成 `['ssh', '-o', 'ConnectTimeout=10', 'host']`
- [ ] 完整配置包含 `-l user -p 2222 -i key -o ControlMaster=auto ...`
- [ ] `port=22` 时不生成 `-p` 参数
- [ ] `control_path` 自动生成格式正确

### Step 2: `debug_loop/ssh_gdb_client.py` + `tests/test_ssh_gdb_client.py`

**实现**：SSHGDBClient，实现 `execute()` 和全部便捷方法
**验收**：
- [ ] `pytest tests/test_ssh_gdb_client.py -v` 全部通过
- [ ] `execute("info registers")` 生成正确的 `ssh host "gdb -batch -ex 'info registers' file"` 命令
- [ ] 成功时返回 stdout 字符串
- [ ] 非零退出返回 `None`（匹配 GDBClient 行为）
- [ ] 超时返回 `None`
- [ ] 单引号转义正确（`it's` → `it'\''s`）
- [ ] `get_state()` 批量执行多个 GDB 命令（一次 SSH 调用）
- [ ] 所有便捷方法（read_register, backtrace 等）调用 `execute()` 并返回结果

### Step 3: `debug_loop/ssh_serial_monitor.py` + `tests/test_ssh_serial.py`

**实现**：SSHSerialMonitor，实现 `start()`/`stop()`/`read_new_lines()`/`read_output()`/`write()`
**验收**：
- [ ] `pytest tests/test_ssh_serial.py -v` 全部通过
- [ ] `start()` 启动 `ssh host "stty -F /dev/ttyUSB0 115200 raw -echo && cat /dev/ttyUSB0"` 子进程
- [ ] `_read_loop` 逐字节读取，按 `\n` 分行，存入 buffer
- [ ] `read_new_lines()` 非阻塞返回并清空 buffer
- [ ] `read_output(timeout)` 阻塞等待最多 timeout 秒
- [ ] `stop()` 终止进程 + join 线程
- [ ] 进程死亡时 `_read_loop` 自动退出（不死循环）
- [ ] `write()` 通过单独 SSH 调用写入远程串口

### Step 4: `debug_loop/__init__.py`

**实现**：添加 `create_debug_loop()` 工厂函数，导出 SSH 类
**验收**：
- [ ] `from debug_loop import SSHConfig, SSHGDBClient, SSHSerialMonitor, create_debug_loop` 成功
- [ ] `create_debug_loop(transport="local", ...)` 返回使用本地 transport 的 DebugLoop
- [ ] `create_debug_loop(transport="ssh", ssh_config=..., ...)` 返回使用 SSH transport 的 DebugLoop
- [ ] 无效 transport 值抛出 `ValueError`

### Step 5: 全量回归

**验收**：
- [ ] `python -m pytest tests/ -v` 全部通过（新增测试 + 原有 187 个测试不回归）
- [ ] 原有 `gdb_client.py`、`serial_monitor.py`、`loop.py` 未被修改
- [ ] `python -c "from debug_loop import SSHConfig, SSHGDBClient, SSHSerialMonitor"` 无报错

### Step 6: 更新 README.md

**验收**：
- [ ] README 包含 SSH transport 使用示例代码
- [ ] 文档说明 SSH 依赖（系统 ssh 命令，无额外 Python 包）
- [ ] 文档说明 Phase 1 限制（per-command 模式，不支持 step-through）
