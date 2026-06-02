"""FreeRTOS task awareness — parse TCB structures from target memory.

Reads FreeRTOS global symbols (pxCurrentTCB, pxReadyTasksLists, etc.) via GDB
and walks the kernel's linked lists to enumerate all tasks with their state,
priority, and approximate stack usage.

Memory layout references: FreeRTOS v10.x on ARM 32-bit.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

# ---------------------------------------------------------------------------
# TCB / ListItem offsets (ARM 32-bit, 4-byte pointers)
# ---------------------------------------------------------------------------

# TCB_t field offsets
_TCB_PX_TOP_OF_STACK = 0x00   # StackType_t*
_TCB_X_STATE_LIST_ITEM = 0x04  # ListItem_t (20 bytes)
_TCB_UX_PRIORITY = 0x24        # UBaseType_t
_TCB_PX_STACK = 0x28           # StackType_t*
_TCB_PC_TASK_NAME = 0x2C       # char[16]

# ListItem_t field offsets (20 bytes total)
_LI_X_ITEM_VALUE = 0x00   # TickType_t (4 bytes)
_LI_PX_NEXT = 0x04        # ListItem_t*
_LI_PX_PREVIOUS = 0x08    # ListItem_t*
_LI_PV_OWNER = 0x0C       # void* — points back to TCB
_LI_PV_CONTAINER = 0x10   # void* — points to the List_t header

# MiniListItem_t field offsets (12 bytes, used as list header)
_MLI_X_ITEM_VALUE = 0x00  # TickType_t
_MLI_PX_NEXT = 0x04       # ListItem_t*
_MLI_PX_PREVIOUS = 0x08   # ListItem_t*

# Maximum linked list walk depth to prevent infinite loops on corruption
_MAX_LIST_WALK = 256


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TaskInfo:
    """Information about a single FreeRTOS task."""
    name: str
    state: str  # "Running", "Ready", "Blocked", "Suspended"
    priority: int
    stack_bottom: int  # pxStack (high address, stack base on Cortex-M)
    stack_top: int     # pxTopOfStack (current SP, low address)
    stack_size: int    # approximate: stack_bottom - stack_top
    stack_usage_pct: float  # percentage of stack used (approximate)


# ---------------------------------------------------------------------------
# FreeRTOS symbol names we look for in the ELF
# ---------------------------------------------------------------------------

_FREERTOS_SYMBOLS = [
    "pxCurrentTCB",
    "uxCurrentNumberOfTasks",
    "pxReadyTasksLists",
    "xDelayedTaskList1",
    "xDelayedTaskList2",
    "xPendingReadyList",
    "xSuspendedTaskList",
    "uxTopReadyPriority",
]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class FreeRTOSParser:
    """Parse FreeRTOS task information from target memory via GDB.

    Parameters
    ----------
    read_mem32 : callable
        ``(address: int) -> int`` — reads a 32-bit word from target memory.
        Must return 0 on failure (same contract as ``_read_mem32`` in
        ``gdb_bridge.gdb_bridge``).
    lookup_symbol : callable, optional
        ``(name: str) -> int | None`` — resolves a symbol name to its address.
        If *None*, ``_try_lookup_symbol`` is used which attempts
        ``gdb.lookup_symbol``.
    """

    def __init__(self, read_mem32, lookup_symbol=None):
        self._read = read_mem32
        self._lookup = lookup_symbol or _try_lookup_symbol

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self) -> bool:
        """Return True if FreeRTOS symbols are present in the ELF."""
        try:
            addr = self._lookup("pxCurrentTCB")
            if not addr:
                return False
            # Verify the address is actually readable
            val = self._read(addr)
            return val != 0
        except Exception:
            return False

    def parse_tasks(self) -> list[TaskInfo]:
        """Parse all FreeRTOS tasks and return a list of TaskInfo.

        Returns an empty list if FreeRTOS is not detected or parsing fails.
        """
        if not self.detect():
            return []

        tasks: list[TaskInfo] = []
        seen: set[int] = set()  # TCB addresses already collected

        # 1. Current task first — state = "Running"
        #    Must be processed before ready lists so it gets "Running" not "Ready".
        current_tcb = self._read_symbol_value("pxCurrentTCB")
        if current_tcb:
            info = self._read_tcb(current_tcb, "Running")
            if info is not None:
                tasks.append(info)
                seen.add(current_tcb)

        # 2. Collect from ready lists (known state = "Ready")
        self._collect_from_ready_lists(tasks, seen)

        # 3. Delayed lists
        self._collect_from_list("xDelayedTaskList1", "Blocked", tasks, seen)
        self._collect_from_list("xDelayedTaskList2", "Blocked", tasks, seen)

        # 4. Pending ready list
        self._collect_from_list("xPendingReadyList", "Ready", tasks, seen)

        # 5. Suspended list
        self._collect_from_list("xSuspendedTaskList", "Suspended", tasks, seen)

        # Sort by priority (descending), then name
        tasks.sort(key=lambda t: (-t.priority, t.name))
        return tasks

    # ------------------------------------------------------------------
    # Internal — list walking
    # ------------------------------------------------------------------

    def _collect_from_ready_lists(
        self, tasks: list[TaskInfo], seen: set[int]
    ) -> None:
        """Walk pxReadyTasksLists[0..uxTopReadyPriority] and collect tasks.

        ``pxReadyTasksLists`` is a static array of ``List_t`` headers, one per
        priority level.  The symbol address is the array base directly.
        """
        ready_base = self._lookup_symbol_addr("pxReadyTasksLists")
        if not ready_base:
            return

        top_prio = self._read_symbol_value("uxTopReadyPriority")
        # Sanity: configMAX_PRIORITIES is typically <= 56
        if top_prio > 100:
            top_prio = 100

        for prio in range(top_prio + 1):
            # Each List_t (MiniListItem_t) is 12 bytes, not a pointer
            list_addr = ready_base + prio * 12
            if not list_addr:
                continue
            self._walk_list(
                list_addr, "Ready", tasks, seen, override_priority=prio,
            )

    def _collect_from_list(
        self,
        symbol: str,
        state: str,
        tasks: list[TaskInfo],
        seen: set[int],
    ) -> None:
        """Walk the FreeRTOS list named *symbol* and collect tasks.

        FreeRTOS list variables (xDelayedTaskList1, xSuspendedTaskList, etc.)
        are static ``List_t`` — the symbol address IS the list header.
        """
        list_addr = self._lookup_symbol_addr(symbol)
        if not list_addr:
            return
        self._walk_list(list_addr, state, tasks, seen)

    def _walk_list(
        self,
        list_header: int,
        state: str,
        tasks: list[TaskInfo],
        seen: set[int],
        override_priority: int | None = None,
    ) -> None:
        """Walk a FreeRTOS doubly-linked list starting at *list_header*.

        The list header is a MiniListItem_t whose ``pxNext`` points to the
        first ListItem_t.  The list is circular — when ``pxNext`` equals
        *list_header* we have reached the end.

        Parameters
        ----------
        override_priority : int | None
            If set, override the TCB's uxPriority with this value (used for
            ready lists where all tasks share the same priority).
        """
        if not list_header:
            return

        first_next = self._read_pointer(list_header + _MLI_PX_NEXT)
        if not first_next or first_next == list_header:
            return  # empty list

        current = first_next
        for _ in range(_MAX_LIST_WALK):
            if not current or current == list_header:
                break

            owner = self._read_pointer(current + _LI_PV_OWNER)
            if owner and owner not in seen:
                info = self._read_tcb(owner, state, override_priority)
                if info is not None:
                    tasks.append(info)
                    seen.add(owner)

            current = self._read_pointer(current + _LI_PX_NEXT)

    # ------------------------------------------------------------------
    # Internal — TCB reading
    # ------------------------------------------------------------------

    def _read_tcb(
        self,
        tcb_addr: int,
        state: str,
        override_priority: int | None = None,
    ) -> TaskInfo | None:
        """Read TCB fields and return a TaskInfo, or None on failure."""
        if not tcb_addr:
            return None

        try:
            # Task name — 16 bytes, null-terminated
            name = self._read_string(tcb_addr + _TCB_PC_TASK_NAME, 16)

            priority = (
                override_priority
                if override_priority is not None
                else self._read_word(tcb_addr + _TCB_UX_PRIORITY)
            )

            stack_bottom = self._read_pointer(tcb_addr + _TCB_PX_STACK)
            stack_top = self._read_pointer(tcb_addr + _TCB_PX_TOP_OF_STACK)

            # Stack grows downward on Cortex-M:
            #   pxStack (bottom) is the HIGH address
            #   pxTopOfStack (top) is the LOW address
            stack_size = max(stack_bottom - stack_top, 0) if stack_bottom and stack_top else 0

            # Approximate usage: ratio of used space.
            # This is a rough estimate — doesn't account for guard patterns
            # or the initial stack frame that was pre-filled.
            usage_pct = 0.0
            if stack_size > 0:
                # Heuristic: assume max possible is 2 * current usage (crude)
                # Better: read pxEndOfStack from TCB if available (offset varies).
                # For now, report raw size info.
                usage_pct = 0.0  # Without pxEndOfStack we can't compute real %

            return TaskInfo(
                name=name if name else "(unknown)",
                state=state,
                priority=priority,
                stack_bottom=stack_bottom,
                stack_top=stack_top,
                stack_size=stack_size,
                stack_usage_pct=usage_pct,
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal — memory helpers
    # ------------------------------------------------------------------

    def _read_symbol_value(self, name: str) -> int:
        """Resolve a symbol name and read the 32-bit value at that address.

        ``_lookup`` returns the *address* of the global variable.
        This method dereferences it to get the variable's value.
        Returns 0 if the symbol is not found or the read fails.
        """
        try:
            addr = self._lookup(name)
            if not addr:
                return 0
            return self._read(addr)
        except Exception:
            return 0

    def _read_word(self, address: int) -> int:
        """Read a 32-bit word, return 0 on failure."""
        try:
            return self._read(address)
        except Exception:
            return 0

    def _read_pointer(self, address: int) -> int:
        """Read a 32-bit pointer, return 0 on failure."""
        return self._read_word(address)

    def _read_string(self, address: int, max_len: int) -> str:
        """Read a null-terminated string from target memory.

        Falls back to byte-by-byte reading if the memory interface
        doesn't support direct string access.
        """
        chars: list[str] = []
        for offset in range(max_len):
            try:
                byte_val = self._read(address + offset) & 0xFF
            except Exception:
                break
            if byte_val == 0:
                break
            if 0x20 <= byte_val < 0x7F:
                chars.append(chr(byte_val))
            else:
                break
        return "".join(chars)

    def _lookup_symbol_addr(self, name: str) -> int:
        """Resolve a symbol name to its address, return 0 if not found."""
        try:
            addr = self._lookup(name)
            return addr if addr else 0
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Default symbol lookup (uses GDB Python API)
# ---------------------------------------------------------------------------

def _try_lookup_symbol(name: str) -> int | None:
    """Try to look up a symbol using GDB's Python API.

    Returns the symbol's address or None if not found.
    This is the default lookup used when no custom one is provided.
    """
    try:
        import gdb
        sym = gdb.lookup_symbol(name)
        if sym and sym[0] is not None:
            return int(sym[0].value().address)
        # Alternative: try minimal_symbol (works for C symbols without debug info)
        msym = gdb.lookup_minimal_symbol(name)
        if msym and msym.value() is not None:
            addr = msym.value().address
            return int(addr) if addr else None
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_task_table(tasks: list[TaskInfo]) -> str:
    """Format a list of TaskInfo as a human-readable table.

    Example output::

        FreeRTOS Tasks (3 total)
        ──────────────────────────────────────────────────────
        Name          State     Priority  Stack Size  Stack Top
        ──────────────────────────────────────────────────────
        IDLE          Ready          0      256 bytes  0x20003f00
        Sensors       Blocked        2      512 bytes  0x20003d00
        Comms         Running        3      1024 bytes 0x20003900
        ──────────────────────────────────────────────────────
    """
    if not tasks:
        return "No FreeRTOS tasks found.\n"

    header = f"FreeRTOS Tasks ({len(tasks)} total)"
    separator = "-" * 72

    lines = [
        "",
        header,
        separator,
        f"{'Name':<16} {'State':<10} {'Priority':>8}  {'Stack Size':>10}  {'Stack Top':<12}",
        separator,
    ]

    for t in tasks:
        size_str = f"{t.stack_size} bytes" if t.stack_size else "n/a"
        top_str = f"0x{t.stack_top:08x}" if t.stack_top else "n/a"
        lines.append(
            f"{t.name:<16} {t.state:<10} {t.priority:>8}  {size_str:>10}  {top_str:<12}"
        )

    lines.append(separator)
    lines.append("")
    return "\n".join(lines)


def tasks_to_dicts(tasks: list[TaskInfo]) -> list[dict]:
    """Convert a list of TaskInfo to a list of dicts for JSON serialization."""
    return [asdict(t) for t in tasks]
