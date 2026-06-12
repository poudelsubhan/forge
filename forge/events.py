"""Event bus + JSONL log (Phase 1E).

`emit(event_type, **payload)` appends to an in-memory deque (the TUI consumes
it) and to ``runs/{timestamp}.jsonl`` (observability + demo replay). The JSONL
log is the single source of truth — both `scripts/stats.py` and `--replay`
read from it.

A module-level "active" bus lets any component (llm, registry, sandbox,
synthesis, loop) emit without threading the bus through every call. `main.py`
creates an `EventBus` and calls `set_active`.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

# The canonical set of event types. Not enforced (emit accepts any string), but
# documents the demo's narrative arc and guards against typos during review.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run_start",
        "turn_start",
        "gap_detected",
        "tool_drafted",
        "test_drafted",
        "verification_run",
        "verification_failed",
        "tool_revised",
        "tool_promoted",
        "tool_failed",
        "tool_used",
        "convergence_check",
        "halted",
        "llm_call",
        "plan_updated",
        "agent_message",
        "error",
    }
)


class EventBus:
    """Fan-out: in-memory deque (for the TUI) + append-only JSONL (for replay)."""

    def __init__(self, run_dir: Path | str = "runs", maxlen: int = 2000) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = self.run_dir / f"{stamp}.jsonl"
        # Line-buffered append handle; flushed on every emit so a crash mid-run
        # still leaves a replayable log.
        self._fh = self.path.open("a", encoding="utf-8")

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {"ts": time.time(), "type": event_type, **payload}
        self.events.append(event)
        self._fh.write(json.dumps(event, default=_json_default) + "\n")
        self._fh.flush()
        return event

    def recent(self, n: int | None = None) -> list[dict[str, Any]]:
        items = list(self.events)
        return items if n is None else items[-n:]

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


def _json_default(obj: Any) -> str:
    """Best-effort serialization for anything not JSON-native (e.g. Paths)."""
    return str(obj)


# --- module-level active bus -------------------------------------------------

_active: EventBus | None = None


def set_active(bus: EventBus | None) -> None:
    global _active
    _active = bus


def get_active() -> EventBus | None:
    return _active


def emit(event_type: str, **payload: Any) -> dict[str, Any] | None:
    """Emit to the active bus, if one is set. No-op otherwise (e.g. in tests)."""
    if _active is not None:
        return _active.emit(event_type, **payload)
    return None


def load_run(path: Path | str) -> list[dict[str, Any]]:
    """Read a recorded run's JSONL back into a list of events (for --replay)."""
    events: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def iter_run(path: Path | str) -> Iterable[dict[str, Any]]:
    """Lazily stream events from a recorded run."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
