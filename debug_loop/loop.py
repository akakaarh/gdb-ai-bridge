"""AI Debug Loop — orchestrates serial monitoring, GDB control, and AI decision-making."""

import json
import time
from .actions import translate_action, validate_action
from .evaluator import Evaluator
from .safety import SafetyChecker


class DebugLoop:
    def __init__(self, goal, expected, serial_monitor, gdb_client):
        """
        Args:
            goal: description of what we're trying to achieve
            expected: dict defining success criteria
                {"serial_contains": "...", "variable": {"name": "...", "value": ...}}
            serial_monitor: SerialMonitor instance
            gdb_client: GDBClient instance
        """
        self.goal = goal
        self.serial = serial_monitor
        self.gdb = gdb_client
        self.evaluator = Evaluator(expected)
        self.safety = SafetyChecker()
        self.history = []

    def run(self):
        """Run the debug loop until success, stagnation, or max iterations."""
        for i in range(50):
            # 1. Read state
            serial_output = self.serial.read_new_lines()
            gdb_state = self._get_gdb_state()

            # 2. Check success
            success, reason = self.evaluator.check(serial_output, gdb_state)
            if success:
                return {"status": "success", "reason": reason, "iterations": i}

            # 3. Check safety limits
            if not self.safety.check_iteration_limit():
                return {"status": "max_iterations", "history": self.history}

            if self.evaluator.is_stagnant:
                return {"status": "stagnation", "reason": "No progress for 3 iterations"}

            state_hash = self._state_hash(serial_output, gdb_state)
            if self.safety.check_oscillation(state_hash):
                return {"status": "oscillation", "reason": "State oscillating"}

            # 4. Build context for AI
            context = self._build_context(serial_output, gdb_state, reason)

            # 5. Get AI action (returns dict or None)
            action = self._get_ai_action(context)
            if action is None:
                continue

            # 6. Validate and execute
            if not validate_action(action):
                self.history.append({"action": action, "error": "invalid action"})
                continue

            gdb_cmd, err = translate_action(action)
            if err:
                self.history.append({"action": action, "error": err})
                continue
            if not self.safety.is_allowed(gdb_cmd):
                self.history.append({"action": action, "error": "blocked by safety"})
                continue

            result = self.gdb.execute(gdb_cmd)
            self.history.append({
                "iteration": i,
                "action": action,
                "gdb_command": gdb_cmd,
                "result": result,
            })

        return {"status": "max_iterations", "history": self.history}

    def _get_gdb_state(self):
        """Read current GDB state."""
        state = {}
        try:
            state_info = self.gdb.get_state()
            state.update(state_info)
        except Exception:
            state["error"] = "Failed to get GDB state"

        # Read registers
        try:
            regs_output = self.gdb.read_all_registers()
            state["registers_raw"] = regs_output
        except Exception:
            pass

        # Check for crash signals
        if state.get("signal") in ("SIGSEGV", "SIGABRT", "SIGBUS", "SIGFPE", "SIGILL"):
            state["crash"] = state["signal"]

        return state

    def _build_context(self, serial_output, gdb_state, reason):
        """Build context string for AI decision."""
        parts = [
            f"## Goal\n{self.goal}",
            f"## Current Status\n{reason}",
        ]

        if serial_output:
            parts.append(f"## Serial Output\n{serial_output}")

        if gdb_state.get("registers_raw"):
            parts.append(f"## Registers\n{gdb_state['registers_raw']}")

        if gdb_state.get("crash"):
            parts.append(f"## CRASH: {gdb_state['crash']}")

        if self.history:
            last = self.history[-3:]
            parts.append("## Recent History")
            for h in last:
                parts.append(f"  Action: {h.get('action', '?')} → {h.get('result', '?')[:200]}")

        return "\n\n".join(parts)

    def _get_ai_action(self, context):
        """Get next action from AI. Returns action dict or None.

        Override this method to integrate with Claude Code subagent or API.
        Default implementation returns None (manual mode).
        """
        return None

    def _state_hash(self, serial_output, gdb_state):
        """Hash current state for oscillation detection."""
        key = f"{serial_output}|{gdb_state.get('pc', '')}|{gdb_state.get('registers_raw', '')[:100]}"
        return hash(key)
