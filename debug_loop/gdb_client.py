"""GDB HTTP client — communicates with GDB's ai serve HTTP API."""

import json
import urllib.request
import urllib.error


class GDBClient:
    def __init__(self, host="localhost", port=9999):
        self.base_url = f"http://{host}:{port}"

    def _request(self, path, data=None, timeout=30):
        url = f"{self.base_url}{path}"
        if data:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
            )
        else:
            req = urllib.request.Request(url)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    def health(self):
        return self._request("/health")

    def execute(self, command):
        """Execute a GDB command, return output."""
        result = self._request("/execute", {"command": command})
        if "error" in result:
            return None
        return result.get("output", "")

    def get_state(self):
        """Get current GDB/target state."""
        return self._request("/state")

    # High-level convenience methods

    def read_register(self, name):
        output = self.execute(f"print ${name}")
        if output:
            return output.strip()
        return None

    def read_all_registers(self):
        return self.execute("info registers")

    def read_variable(self, name):
        output = self.execute(f"print {name}")
        if output:
            return output.strip()
        return None

    def read_memory(self, addr, count=1):
        return self.execute(f"x/{count}wx {addr}")

    def set_breakpoint(self, location):
        return self.execute(f"break {location}")

    def delete_breakpoint(self, number):
        return self.execute(f"delete {number}")

    def step(self):
        return self.execute("step")

    def next(self):
        return self.execute("next")

    def continue_exec(self):
        return self.execute("continue")

    def backtrace(self):
        return self.execute("backtrace")

    def info_locals(self):
        return self.execute("info locals")

    def finish(self):
        return self.execute("finish")
