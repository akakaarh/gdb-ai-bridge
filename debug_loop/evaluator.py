"""Success/failure evaluator for the debug loop."""


class Evaluator:
    def __init__(self, expected):
        """
        expected = {
            "serial_contains": "Temperature: 25",  # 串口输出包含此内容
            "variable": {"name": "sensor_value", "value": 25},  # 变量值
            "no_crash": True,  # 不应崩溃
        }
        """
        self.expected = expected
        self.stagnation_count = 0
        self.last_state_hash = None

    def check(self, serial_output, gdb_state):
        """判断是否达到预期。
        Returns: (success: bool, reason: str)
        """
        # 检查崩溃
        if gdb_state.get("crash"):
            return False, f"Crash: {gdb_state['crash']}"

        # 检查串口
        if self.expected.get("serial_contains"):
            if self.expected["serial_contains"] in serial_output:
                return True, "Expected output found"

        # 检查变量
        if self.expected.get("variable"):
            var = self.expected["variable"]
            if gdb_state.get("variables", {}).get(var["name"]) == var["value"]:
                return True, f"Variable {var['name']} = {var['value']}"

        # 置信度下降检测
        state_hash = hash(str(serial_output) + str(gdb_state))
        if state_hash == self.last_state_hash:
            self.stagnation_count += 1
        else:
            self.stagnation_count = 0
        self.last_state_hash = state_hash

        return False, "Not yet achieved"

    @property
    def is_stagnant(self):
        return self.stagnation_count >= 3
