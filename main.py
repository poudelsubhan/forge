"""Forge CLI entry point.

Phase 1 stub — wired up fully in Phase 4 (`--replay`, `--fresh`, `--keep`).
For now this just confirms the foundation imports cleanly and reports the
configured model + toolbox state.
"""

from __future__ import annotations

import sys

from forge import events, llm
from forge.registry import Registry


def main(argv: list[str]) -> int:
    bus = events.EventBus(run_dir="runs")
    events.set_active(bus)
    try:
        registry = Registry("forge/tools")
        bus.emit("run_start", model=llm.DEFAULT_MODEL, phase="foundation")
        print(f"forge — model={llm.DEFAULT_MODEL}")
        print(f"promoted tools: {len(registry.list_promoted())}")
        print(f"toolbox manifest: {registry.manifest_path}")
        print(f"run log: {bus.path}")
        print("\n(Phase 1 foundation. Control loop arrives in Phase 3, CLI in Phase 4.)")
        return 0
    finally:
        bus.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
