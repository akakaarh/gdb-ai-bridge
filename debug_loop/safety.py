"""Safety checker: command whitelist and loop guards for the debug loop."""

ALLOWED_COMMANDS = [
    "info", "print", "x/", "backtrace", "bt",
    "step", "next", "continue", "break", "finish",
    "info registers", "info locals", "info breakpoints",
    "info all-registers",
]

SAFETY = {
    "max_iterations": 50,
    "stagnation_limit": 3,
    "oscillation_window": 5,
    "timeout_per_step": 30,
}


class SafetyChecker:
    def __init__(self):
        self.iteration = 0
        self.state_history = []

    def is_allowed(self, gdb_command):
        """检查命令是否在白名单中"""
        cmd = gdb_command.strip()
        for allowed in ALLOWED_COMMANDS:
            if cmd == allowed or cmd.startswith(allowed + " "):
                return True
            # Handle prefixes like "x/" where the modifier follows directly
            # e.g. "x/4wx 0x20000000" starts with "x/"
            if allowed.endswith("/") and cmd.startswith(allowed):
                return True
        return False

    def check_iteration_limit(self):
        self.iteration += 1
        return self.iteration < SAFETY["max_iterations"]

    def check_oscillation(self, state_hash):
        """检测最近 N 轮是否振荡（状态重复）"""
        self.state_history.append(state_hash)
        if len(self.state_history) > SAFETY["oscillation_window"]:
            self.state_history.pop(0)
        if len(self.state_history) >= SAFETY["oscillation_window"]:
            unique = set(self.state_history)
            if len(unique) <= 2:  # 只有 1-2 个不同状态
                return True  # 振荡
        return False
