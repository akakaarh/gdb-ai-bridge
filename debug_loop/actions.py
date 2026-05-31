"""Structured actions that the AI can output, translated to GDB commands."""


ACTIONS = {
    "read_register": {
        "params": ["name"],
        "gdb": "print ${name}",
    },
    "read_registers": {
        "params": [],
        "gdb": "info registers",
    },
    "read_variable": {
        "params": ["name"],
        "gdb": "print {name}",
    },
    "read_memory": {
        "params": ["addr", "count"],
        "gdb": "x/{count}wx {addr}",
    },
    "set_breakpoint": {
        "params": ["location"],
        "gdb": "break {location}",
    },
    "delete_breakpoint": {
        "params": ["number"],
        "gdb": "delete {number}",
    },
    "step": {
        "params": [],
        "gdb": "step",
    },
    "next": {
        "params": [],
        "gdb": "next",
    },
    "continue_exec": {
        "params": [],
        "gdb": "continue",
    },
    "backtrace": {
        "params": [],
        "gdb": "backtrace",
    },
    "info_locals": {
        "params": [],
        "gdb": "info locals",
    },
    "finish": {
        "params": [],
        "gdb": "finish",
    },
}


def validate_action(action_dict):
    """Validate that an action dict is well-formed.

    Args:
        action_dict: Must contain "action" (str) and "params" (dict) keys.

    Returns:
        (True, None) on success, (False, error_message) on failure.
    """
    if not isinstance(action_dict, dict):
        return False, "action must be a dict"

    action_name = action_dict.get("action")
    if not action_name:
        return False, "missing 'action' field"
    if action_name not in ACTIONS:
        return False, f"unknown action: {action_name!r}"

    params = action_dict.get("params", {})
    if not isinstance(params, dict):
        return False, "'params' must be a dict"

    spec = ACTIONS[action_name]
    required = spec["params"]
    provided = set(params.keys())
    expected = set(required)

    missing = expected - provided
    if missing:
        return False, f"missing params: {sorted(missing)}"

    extra = provided - expected
    if extra:
        return False, f"unexpected params: {sorted(extra)}"

    return True, None


def translate_action(action_dict):
    """Translate a validated action dict into a GDB command string.

    Args:
        action_dict: A dict with "action" and "params" keys.

    Returns:
        (gdb_command, None) on success, (None, error_message) on failure.
    """
    ok, err = validate_action(action_dict)
    if not ok:
        return None, err

    action_name = action_dict["action"]
    params = action_dict.get("params", {})
    gdb_template = ACTIONS[action_name]["gdb"]

    command = gdb_template.format(**params)
    return command, None


def get_available_actions():
    """Return a list of all available actions with their parameter specs.

    This is meant to be included in the AI system prompt so it knows
    what structured actions it can emit.
    """
    result = []
    for name, spec in ACTIONS.items():
        result.append({
            "action": name,
            "params": spec["params"],
        })
    return result
