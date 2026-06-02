"""Tests for gdb_bridge.freertos — FreeRTOS task parser."""
import pytest
from unittest.mock import MagicMock

from gdb_bridge.freertos import (
    FreeRTOSParser,
    TaskInfo,
    format_task_table,
    tasks_to_dicts,
    _TCB_PX_TOP_OF_STACK,
    _TCB_UX_PRIORITY,
    _TCB_PX_STACK,
    _TCB_PC_TASK_NAME,
    _TCB_X_STATE_LIST_ITEM,
    _LI_X_ITEM_VALUE,
    _LI_PX_NEXT,
    _LI_PX_PREVIOUS,
    _LI_PV_OWNER,
    _LI_PV_CONTAINER,
    _MLI_PX_NEXT,
    _MLI_PX_PREVIOUS,
)


# ---------------------------------------------------------------------------
# Helper: simulated FreeRTOS memory
# ---------------------------------------------------------------------------

# TCB field offsets (repeated here for test clarity)
TCB_TOP_OF_STACK = _TCB_PX_TOP_OF_STACK      # 0x00
TCB_STATE_LIST    = _TCB_X_STATE_LIST_ITEM    # 0x04
TCB_PRIORITY      = _TCB_UX_PRIORITY          # 0x24
TCB_STACK         = _TCB_PX_STACK             # 0x28
TCB_NAME          = _TCB_PC_TASK_NAME         # 0x2C

# ListItem_t offsets
LI_VALUE    = _LI_X_ITEM_VALUE   # 0x00
LI_NEXT     = _LI_PX_NEXT       # 0x04
LI_PREV     = _LI_PX_PREVIOUS   # 0x08
LI_OWNER    = _LI_PV_OWNER      # 0x0C
LI_CONT     = _LI_PV_CONTAINER  # 0x10

# MiniListItem_t offsets
MLI_VALUE   = 0x00
MLI_NEXT    = _MLI_PX_NEXT     # 0x04
MLI_PREV    = _LI_PX_PREVIOUS  # 0x08


def _encode_str(s: str, length: int) -> bytes:
    """Encode a string as null-terminated bytes of fixed length."""
    encoded = s.encode("ascii")[:length - 1]
    return encoded + b"\x00" * (length - len(encoded))


def _build_task_memory(
    task_addr: int,
    name: str,
    priority: int,
    stack_bottom: int,
    stack_top: int,
    li_addr: int,
    li_next: int,
    li_prev: int,
    li_container: int,
) -> dict[int, int]:
    """Build a simulated TCB + ListItem memory region.

    Returns a dict mapping address -> 32-bit word.
    """
    mem: dict[int, int] = {}

    # TCB fields
    mem[task_addr + TCB_TOP_OF_STACK] = stack_top
    mem[task_addr + TCB_PRIORITY] = priority
    mem[task_addr + TCB_STACK] = stack_bottom

    # The state list item lives at task_addr + TCB_STATE_LIST
    # Its owner should point back to the TCB
    tcb_li_addr = task_addr + TCB_STATE_LIST

    # Task name — 16 bytes, packed as 4 words
    name_bytes = _encode_str(name, 16)
    for i in range(0, 16, 4):
        word = int.from_bytes(name_bytes[i:i + 4], "little")
        mem[task_addr + TCB_NAME + i] = word

    # ListItem fields (embedded in TCB or at a separate address)
    # We write the embedded ListItem in the TCB
    mem[tcb_li_addr + LI_NEXT] = li_next
    mem[tcb_li_addr + LI_PREV] = li_prev
    mem[tcb_li_addr + LI_OWNER] = task_addr  # owner = TCB itself
    mem[tcb_li_addr + LI_CONT] = li_container

    return mem


def _build_list_header(
    header_addr: int,
    first_item: int,
    last_item: int,
) -> dict[int, int]:
    """Build a MiniListItem_t list header."""
    return {
        header_addr + MLI_VALUE: 0xFFFFFFFF,  # sentinel value
        header_addr + MLI_NEXT: first_item,
        header_addr + MLI_PREV: last_item,
    }


def _make_simulated_freertos():
    """Create a complete simulated FreeRTOS memory layout with 3 tasks.

    Memory layout matches real FreeRTOS on ARM 32-bit:

    Global variables (pointer/value types):
        0x20000000  pxCurrentTCB       = 0x20000300  (pointer to Comms TCB)
        0x20000004  uxCurrentNumberOfTasks = 3
        0x20000008  uxTopReadyPriority = 2

    Static arrays / list headers (symbol address IS the data):
        0x2000000C  pxReadyTasksLists[3] — array of List_t headers (12 bytes each)
            0x2000000C  Ready[0] header -> IDLE ListItem (sole item)
            0x20000018  Ready[1] header (empty, self-referencing)
            0x20000024  Ready[2] header -> Comms ListItem (sole item)

        0x20000030  xDelayedTaskList1 header -> Sensors ListItem (sole item)
        0x2000003C  xDelayedTaskList2 header (empty)
        0x20000048  xPendingReadyList header (empty)
        0x20000054  xSuspendedTaskList header (empty)

    Task TCBs (60 bytes each):
        0x20000100  TCB_IDLE
        0x20000200  TCB_SENSORS
        0x20000300  TCB_COMMS

    Symbol lookup returns the *address* where each variable lives.
    For pointer types (pxCurrentTCB), the value is read at that address.
    For static arrays (pxReadyTasksLists), the address IS the array base.
    For static List_t (xDelayedTaskList1), the address IS the list header.
    """
    # --- Symbol addresses ---
    SYM_PX_CURRENT_TCB = 0x20000000
    SYM_NUM_TASKS      = 0x20000004
    SYM_TOP_READY_PRIO = 0x20000008

    # pxReadyTasksLists is a static array of List_t — symbol address = array base
    SYM_READY_LISTS = 0x2000000C
    # Each List_t (MiniListItem_t) is 12 bytes
    # We need 4 priority levels: 0(IDLE), 1(empty), 2(Sensors-not-here), 3(Comms)
    # But Sensors is in the delayed list, so: 0(IDLE), 1(empty), 2(empty), 3(Comms)
    READY_LIST_PRIO0 = SYM_READY_LISTS           # 0x2000000C
    READY_LIST_PRIO1 = SYM_READY_LISTS + 12      # 0x20000018
    READY_LIST_PRIO2 = SYM_READY_LISTS + 24      # 0x20000024
    READY_LIST_PRIO3 = SYM_READY_LISTS + 36      # 0x20000030

    # xDelayedTaskList1 etc. are static List_t — symbol address = list header
    SYM_DELAYED_1 = 0x2000003C
    SYM_DELAYED_2 = 0x20000048
    SYM_PENDING   = 0x20000054
    SYM_SUSPENDED = 0x20000060

    # --- Task TCB addresses ---
    TCB_IDLE    = 0x20000100
    TCB_SENSORS = 0x20000200
    TCB_COMMS   = 0x20000300

    # ListItem_t embedded in each TCB at offset 0x04
    LI_IDLE    = TCB_IDLE + _TCB_X_STATE_LIST_ITEM
    LI_SENSORS = TCB_SENSORS + _TCB_X_STATE_LIST_ITEM
    LI_COMMS   = TCB_COMMS + _TCB_X_STATE_LIST_ITEM

    # --- Build memory ---
    mem: dict[int, int] = {}

    # Global pointer/value variables: symbol address holds the value
    mem[SYM_PX_CURRENT_TCB] = TCB_COMMS
    mem[SYM_NUM_TASKS] = 3
    mem[SYM_TOP_READY_PRIO] = 3

    # Static arrays / list headers: data lives at the symbol address

    # Ready list priority 0: contains IDLE (sole item, circular)
    mem.update(_build_list_header(READY_LIST_PRIO0, LI_IDLE, LI_IDLE))
    # Ready list priority 1: empty
    mem.update(_build_list_header(READY_LIST_PRIO1, READY_LIST_PRIO1, READY_LIST_PRIO1))
    # Ready list priority 2: empty
    mem.update(_build_list_header(READY_LIST_PRIO2, READY_LIST_PRIO2, READY_LIST_PRIO2))
    # Ready list priority 3: contains Comms (sole item, circular)
    mem.update(_build_list_header(READY_LIST_PRIO3, LI_COMMS, LI_COMMS))

    # Delayed list 1: contains Sensors
    mem.update(_build_list_header(SYM_DELAYED_1, LI_SENSORS, LI_SENSORS))
    # Delayed list 2: empty
    mem.update(_build_list_header(SYM_DELAYED_2, SYM_DELAYED_2, SYM_DELAYED_2))
    # Pending ready list: empty
    mem.update(_build_list_header(SYM_PENDING, SYM_PENDING, SYM_PENDING))
    # Suspended list: empty
    mem.update(_build_list_header(SYM_SUSPENDED, SYM_SUSPENDED, SYM_SUSPENDED))

    # --- Task TCBs ---

    # IDLE task: in ready list priority 0
    mem.update(_build_task_memory(
        task_addr=TCB_IDLE,
        name="IDLE",
        priority=0,
        stack_bottom=0x20001400,
        stack_top=0x20001200,
        li_addr=LI_IDLE,
        li_next=READY_LIST_PRIO0,
        li_prev=READY_LIST_PRIO0,
        li_container=READY_LIST_PRIO0,
    ))

    # Sensors task: in delayed list 1
    mem.update(_build_task_memory(
        task_addr=TCB_SENSORS,
        name="Sensors",
        priority=2,
        stack_bottom=0x20002400,
        stack_top=0x20002100,
        li_addr=LI_SENSORS,
        li_next=SYM_DELAYED_1,
        li_prev=SYM_DELAYED_1,
        li_container=SYM_DELAYED_1,
    ))

    # Comms task: in ready list priority 3 AND is the current task
    mem.update(_build_task_memory(
        task_addr=TCB_COMMS,
        name="Comms",
        priority=3,
        stack_bottom=0x20003800,
        stack_top=0x20003400,
        li_addr=LI_COMMS,
        li_next=READY_LIST_PRIO3,
        li_prev=READY_LIST_PRIO3,
        li_container=READY_LIST_PRIO3,
    ))

    # --- Symbol lookup map ---
    symbols = {
        "pxCurrentTCB": SYM_PX_CURRENT_TCB,
        "uxCurrentNumberOfTasks": SYM_NUM_TASKS,
        "uxTopReadyPriority": SYM_TOP_READY_PRIO,
        "pxReadyTasksLists": SYM_READY_LISTS,
        "xDelayedTaskList1": SYM_DELAYED_1,
        "xDelayedTaskList2": SYM_DELAYED_2,
        "xPendingReadyList": SYM_PENDING,
        "xSuspendedTaskList": SYM_SUSPENDED,
    }

    return mem, symbols


def _make_parser(mem: dict, symbols: dict) -> FreeRTOSParser:
    """Create a FreeRTOSParser backed by simulated memory.

    The ``read_mem32`` callable must behave like GDB's memory read: accept a
    byte address and return the 32-bit word at that *aligned* address.  For
    the string reader that calls ``read_mem32(addr + offset)`` byte-by-byte,
    we need to select the correct byte from the stored word.
    """
    def read_mem32(addr: int) -> int:
        # Determine if this is a sub-word (byte) access by checking alignment
        byte_offset = addr & 3
        word_addr = addr & ~3
        word = mem.get(word_addr, 0)
        if byte_offset == 0:
            return word
        # Return just the requested byte (used by _read_string)
        return (word >> (byte_offset * 8)) & 0xFF

    def lookup(name: str) -> int | None:
        return symbols.get(name)

    return FreeRTOSParser(read_mem32, lookup)


# ---------------------------------------------------------------------------
# Tests: detection
# ---------------------------------------------------------------------------

class TestDetection:
    def test_detect_returns_true_when_symbols_present(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        assert parser.detect() is True

    def test_detect_returns_false_when_px_current_tcb_missing(self):
        mem, symbols = _make_simulated_freertos()
        del symbols["pxCurrentTCB"]
        parser = _make_parser(mem, symbols)
        assert parser.detect() is False

    def test_detect_returns_false_when_px_current_tcb_is_zero(self):
        mem, symbols = _make_simulated_freertos()
        symbols["pxCurrentTCB"] = 0
        parser = _make_parser(mem, symbols)
        assert parser.detect() is False

    def test_detect_returns_false_with_empty_symbols(self):
        parser = _make_parser({}, {})
        assert parser.detect() is False


# ---------------------------------------------------------------------------
# Tests: task parsing
# ---------------------------------------------------------------------------

class TestTaskParsing:
    def test_parses_all_tasks(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        assert len(tasks) == 3

    def test_task_names(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()
        names = {t.name for t in tasks}

        assert names == {"IDLE", "Sensors", "Comms"}

    def test_task_priorities(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        assert by_name["IDLE"].priority == 0
        assert by_name["Sensors"].priority == 2
        assert by_name["Comms"].priority == 3

    def test_task_states(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        assert by_name["IDLE"].state == "Ready"
        assert by_name["Sensors"].state == "Blocked"
        assert by_name["Comms"].state == "Running"

    def test_sorted_by_priority_descending(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        priorities = [t.priority for t in tasks]
        assert priorities == sorted(priorities, reverse=True)

    def test_stack_bottom_values(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        assert by_name["IDLE"].stack_bottom == 0x20001400
        assert by_name["Sensors"].stack_bottom == 0x20002400
        assert by_name["Comms"].stack_bottom == 0x20003800

    def test_stack_top_values(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        assert by_name["IDLE"].stack_top == 0x20001200
        assert by_name["Sensors"].stack_top == 0x20002100
        assert by_name["Comms"].stack_top == 0x20003400


# ---------------------------------------------------------------------------
# Tests: stack size calculation
# ---------------------------------------------------------------------------

class TestStackSize:
    def test_stack_size_calculation(self):
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        # IDLE: 0x20001400 - 0x20001200 = 0x200 = 512
        assert by_name["IDLE"].stack_size == 512
        # Sensors: 0x20002400 - 0x20002100 = 0x300 = 768
        assert by_name["Sensors"].stack_size == 768
        # Comms: 0x20003800 - 0x20003400 = 0x400 = 1024
        assert by_name["Comms"].stack_size == 1024

    def test_stack_size_zero_when_top_is_zero(self):
        """If pxTopOfStack is 0 (corrupt TCB), stack_size should be 0."""
        mem, symbols = _make_simulated_freertos()
        # Corrupt IDLE's stack top
        mem[0x20000100 + _TCB_PX_TOP_OF_STACK] = 0
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        assert by_name["IDLE"].stack_size == 0

    def test_stack_size_zero_when_bottom_is_zero(self):
        """If pxStack is 0 (corrupt TCB), stack_size should be 0."""
        mem, symbols = _make_simulated_freertos()
        mem[0x20000100 + _TCB_PX_STACK] = 0
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        assert by_name["IDLE"].stack_size == 0

    def test_stack_usage_pct_is_zero_without_end_of_stack(self):
        """Without pxEndOfStack, usage % should always be 0 (unknown)."""
        mem, symbols = _make_simulated_freertos()
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        for t in tasks:
            assert t.stack_usage_pct == 0.0


# ---------------------------------------------------------------------------
# Tests: missing / absent lists
# ---------------------------------------------------------------------------

class TestMissingLists:
    def test_no_delayed_lists(self):
        """Tasks only in ready lists, delayed lists absent."""
        mem, symbols = _make_simulated_freertos()
        del symbols["xDelayedTaskList1"]
        del symbols["xDelayedTaskList2"]
        # Move Sensors to a ready list instead
        symbols["uxTopReadyPriority"] = 2
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        names = {t.name for t in tasks}
        # Sensors won't be found since it's in the blocked list which is gone
        assert "IDLE" in names
        assert "Comms" in names

    def test_no_suspended_list(self):
        """Suspended list absent — no crash."""
        mem, symbols = _make_simulated_freertos()
        del symbols["xSuspendedTaskList"]
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        # All 3 tasks should still be found (none are in suspended)
        assert len(tasks) == 3

    def test_no_ready_lists(self):
        """Ready lists absent — only current TCB found."""
        mem, symbols = _make_simulated_freertos()
        del symbols["pxReadyTasksLists"]
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        names = {t.name for t in tasks}
        # Only Comms (current TCB) should be found
        assert "Comms" in names
        # IDLE is only in ready lists, so it won't be found
        assert "IDLE" not in names

    def test_empty_ready_list(self):
        """Ready list points to empty list header."""
        mem, symbols = _make_simulated_freertos()
        # Make priority 0 ready list empty (self-referencing = empty)
        READY_LIST_PRIO0 = 0x2000000C  # pxReadyTasksLists + 0 * 12
        mem[READY_LIST_PRIO0 + _MLI_PX_NEXT] = READY_LIST_PRIO0
        mem[READY_LIST_PRIO0 + _MLI_PX_PREVIOUS] = READY_LIST_PRIO0
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        names = {t.name for t in tasks}
        assert "IDLE" not in names  # removed from its list


# ---------------------------------------------------------------------------
# Tests: corrupted data handling
# ---------------------------------------------------------------------------

class TestCorruptionHandling:
    def test_broken_linked_list_stops_walk(self):
        """A ListItem with pxNext = 0 should not cause infinite loop."""
        mem, symbols = _make_simulated_freertos()
        # Break IDLE's ListItem — point next to 0
        LI_IDLE = 0x20000100 + _TCB_X_STATE_LIST_ITEM
        mem[LI_IDLE + _LI_PX_NEXT] = 0
        parser = _make_parser(mem, symbols)

        # Should not hang or crash
        tasks = parser.parse_tasks()
        assert isinstance(tasks, list)

    def test_corrupt_tcb_name(self):
        """TCB with garbage name bytes should return '(unknown)'."""
        mem, symbols = _make_simulated_freertos()
        # Fill name with 0xFF
        for i in range(0, 16, 4):
            mem[0x20000100 + _TCB_PC_TASK_NAME + i] = 0xFFFFFFFF
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        by_name = {t.name: t for t in tasks}
        assert by_name["(unknown)"].priority == 0

    def test_zero_tcb_address_skipped(self):
        """A list item with owner=0 should be skipped."""
        mem, symbols = _make_simulated_freertos()
        # Corrupt the IDLE ListItem owner
        LI_IDLE = 0x20000100 + _TCB_X_STATE_LIST_ITEM
        mem[LI_IDLE + _LI_PV_OWNER] = 0
        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        names = {t.name for t in tasks}
        assert "IDLE" not in names
        assert len(tasks) == 2

    def test_memory_read_exception_handled(self):
        """If read_mem32 raises, the parser should not crash."""
        def bad_read(addr):
            raise RuntimeError("bus fault")

        def lookup(name):
            return {"pxCurrentTCB": 0x20000300}.get(name)

        parser = FreeRTOSParser(bad_read, lookup)
        # detect() should handle the exception gracefully
        assert parser.detect() is False

    def test_max_list_walk_limit(self):
        """Walk should stop at _MAX_LIST_WALK iterations (corrupt cycle)."""
        mem, symbols = _make_simulated_freertos()

        # Create a cycle: IDLE ListItem -> Sensors ListItem -> IDLE ListItem
        LI_IDLE = 0x20000100 + _TCB_X_STATE_LIST_ITEM
        LI_SENSORS = 0x20000200 + _TCB_X_STATE_LIST_ITEM

        mem[LI_IDLE + _LI_PX_NEXT] = LI_SENSORS
        mem[LI_SENSORS + _LI_PX_NEXT] = LI_IDLE
        # Fix owner pointers
        mem[LI_IDLE + _LI_PV_OWNER] = 0x20000100
        mem[LI_SENSORS + _LI_PV_OWNER] = 0x20000200

        parser = _make_parser(mem, symbols)
        tasks = parser.parse_tasks()

        # Should not hang; may find the tasks once each
        assert isinstance(tasks, list)
        assert len(tasks) <= 3


# ---------------------------------------------------------------------------
# Tests: format_task_table
# ---------------------------------------------------------------------------

class TestFormatTaskTable:
    def test_format_empty_list(self):
        result = format_task_table([])
        assert "No FreeRTOS tasks" in result

    def test_format_contains_task_names(self):
        tasks = [
            TaskInfo("IDLE", "Ready", 0, 0x20001400, 0x20001200, 512, 0.0),
            TaskInfo("Comms", "Running", 3, 0x20003800, 0x20003400, 1024, 0.0),
        ]
        result = format_task_table(tasks)
        assert "IDLE" in result
        assert "Comms" in result

    def test_format_contains_states(self):
        tasks = [
            TaskInfo("IDLE", "Ready", 0, 0x20001400, 0x20001200, 512, 0.0),
            TaskInfo("Sensors", "Blocked", 2, 0x20002400, 0x20002100, 768, 0.0),
        ]
        result = format_task_table(tasks)
        assert "Ready" in result
        assert "Blocked" in result

    def test_format_contains_priorities(self):
        tasks = [
            TaskInfo("IDLE", "Ready", 0, 0x20001400, 0x20001200, 512, 0.0),
        ]
        result = format_task_table(tasks)
        assert "0" in result

    def test_format_contains_stack_info(self):
        tasks = [
            TaskInfo("Comms", "Running", 3, 0x20003800, 0x20003400, 1024, 0.0),
        ]
        result = format_task_table(tasks)
        assert "1024 bytes" in result
        assert "0x20003400" in result

    def test_format_shows_total_count(self):
        tasks = [
            TaskInfo("A", "Ready", 0, 0, 0, 0, 0.0),
            TaskInfo("B", "Ready", 0, 0, 0, 0, 0.0),
            TaskInfo("C", "Ready", 0, 0, 0, 0, 0.0),
        ]
        result = format_task_table(tasks)
        assert "(3 total)" in result

    def test_format_with_zero_stack(self):
        """Stack size of 0 should show 'n/a'."""
        tasks = [
            TaskInfo("Bad", "Running", 0, 0, 0, 0, 0.0),
        ]
        result = format_task_table(tasks)
        assert "n/a" in result


# ---------------------------------------------------------------------------
# Tests: tasks_to_dicts
# ---------------------------------------------------------------------------

class TestTasksToDicts:
    def test_returns_list_of_dicts(self):
        tasks = [
            TaskInfo("IDLE", "Ready", 0, 0x20001400, 0x20001200, 512, 0.0),
        ]
        dicts = tasks_to_dicts(tasks)
        assert len(dicts) == 1
        assert isinstance(dicts[0], dict)
        assert dicts[0]["name"] == "IDLE"
        assert dicts[0]["state"] == "Ready"
        assert dicts[0]["priority"] == 0
        assert dicts[0]["stack_bottom"] == 0x20001400
        assert dicts[0]["stack_top"] == 0x20001200
        assert dicts[0]["stack_size"] == 512
        assert dicts[0]["stack_usage_pct"] == 0.0

    def test_empty_list(self):
        assert tasks_to_dicts([]) == []


# ---------------------------------------------------------------------------
# Tests: integration with Collector
# ---------------------------------------------------------------------------

class TestCollectorIntegration:
    """Verify that Collector._collect_freertos_tasks works correctly."""

    def test_collector_has_tasks_field(self):
        from gdb_bridge.collector import DebugContext
        ctx = DebugContext()
        assert hasattr(ctx, "tasks")
        assert ctx.tasks == []

    def test_tasks_in_to_dict(self):
        from gdb_bridge.collector import DebugContext
        ctx = DebugContext()
        ctx.tasks = [{"name": "IDLE", "state": "Ready"}]
        d = ctx.to_dict()
        assert "tasks" in d
        assert d["tasks"][0]["name"] == "IDLE"

    def test_collector_collect_with_no_freertos(self):
        """When no FreeRTOS symbols exist, tasks should be empty."""
        from gdb_bridge.collector import Collector
        from gdb_bridge.arch.base import ArchAdapter
        from gdb_bridge.target.base import TargetAdapter
        from unittest.mock import MagicMock

        arch = MagicMock(spec=ArchAdapter)
        arch.name = "arm"
        arch.get_registers.return_value = {}
        arch.annotate_registers.return_value = {}
        arch.get_fault_registers.return_value = {}
        arch.decode_crash.return_value = ("none", "")

        target = MagicMock(spec=TargetAdapter)
        target.name = "baremetal"
        target.get_stack_trace.return_value = []
        target.get_local_variables.return_value = {}

        # read_mem32 that always returns 0 (no FreeRTOS symbols readable)
        collector = Collector(arch, target, read_mem32=lambda addr: 0)
        ctx = collector.collect()

        assert ctx.tasks == []


# ---------------------------------------------------------------------------
# Tests: TaskInfo dataclass
# ---------------------------------------------------------------------------

class TestTaskInfoDataclass:
    def test_construction(self):
        t = TaskInfo(
            name="Test",
            state="Ready",
            priority=5,
            stack_bottom=0x20001000,
            stack_top=0x20000800,
            stack_size=2048,
            stack_usage_pct=50.0,
        )
        assert t.name == "Test"
        assert t.state == "Ready"
        assert t.priority == 5
        assert t.stack_bottom == 0x20001000
        assert t.stack_top == 0x20000800
        assert t.stack_size == 2048
        assert t.stack_usage_pct == 50.0

    def test_equality(self):
        t1 = TaskInfo("A", "Ready", 0, 0, 0, 0, 0.0)
        t2 = TaskInfo("A", "Ready", 0, 0, 0, 0, 0.0)
        assert t1 == t2

    def test_inequality(self):
        t1 = TaskInfo("A", "Ready", 0, 0, 0, 0, 0.0)
        t2 = TaskInfo("B", "Ready", 0, 0, 0, 0, 0.0)
        assert t1 != t2
