"""Forge CLI entry point (Phase 4B).

    uv run main.py "task..."          # run a task (live TUI), persisting the toolbox
    uv run main.py demo/task.txt      # a path is read as the task
    uv run main.py "task" --fresh     # wipe the toolbox first
    uv run main.py --replay runs/X.jsonl   # replay a recorded run through the TUI
    uv run main.py "task" --no-tui    # headless: plain-text trace (no terminal UI)

The toolbox persists across runs by default (`--keep`) — that persistence is
what makes the run-2 reuse beat work. `--fresh` wipes it.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from forge import events, llm
from forge.registry import Registry

TOOLS_DIR = "forge/tools"
RUNS_DIR = "runs"


def _read_task(arg: str) -> str:
    path = Path(arg)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return arg


def _print_summary(bus: events.EventBus, result) -> None:
    from forge.tui import ViewModel

    vm = ViewModel()
    for e in bus.events:
        vm.ingest(e)
    promoted = [n for n, t in vm.tools.items() if t["status"] == "promoted"]
    failed = [n for n, t in vm.tools.items() if t["status"] == "failed"]
    print("\n" + "=" * 64)
    print(f"halt: {result.halt_reason}   turns: {result.turns}   model: {vm.model}")
    print(f"toolbox: {len(promoted)} promoted {promoted}  ·  {len(failed)} failed {failed}")
    print(f"cost: ${vm.cost:.4f}   ({vm.llm_calls} LLM calls, {vm.in_tokens}+{vm.out_tokens} tok)")
    print(f"run log: {bus.path}")
    if result.final_answer:
        print("\n--- final answer ---")
        print(result.final_answer)
    print("=" * 64)


def _run_headless(bus: events.EventBus, task: str, registry: Registry):
    """No terminal UI — run to completion, then dump a plain-text event trace."""
    from forge import loop

    result = loop.run(task, registry)
    for e in bus.events:
        t = e["type"]
        if t in ("gap_detected", "tool_promoted", "tool_failed", "verification_failed", "tool_used", "halted", "plan_updated", "secret_resolved", "secret_denied"):
            print(f"  · {t}: {({k: v for k, v in e.items() if k not in ('ts', 'type', 'stderr', 'stdout')})}")
    return result


def cmd_run(task: str, fresh: bool, no_tui: bool) -> int:
    bus = events.EventBus(run_dir=RUNS_DIR)
    events.set_active(bus)
    registry = Registry(TOOLS_DIR)
    if fresh:
        registry.reset()

    try:
        if no_tui:
            result = _run_headless(bus, task, registry)
        else:
            from forge import loop, tui

            holder: dict[str, object] = {}

            def work() -> None:
                try:
                    holder["result"] = loop.run(task, registry)
                except Exception as exc:  # noqa: BLE001 — surface loop errors after UI stops
                    holder["error"] = exc

            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            tui.run_live(bus, is_done=lambda: not worker.is_alive())
            worker.join()
            if "error" in holder:
                raise holder["error"]  # type: ignore[misc]
            result = holder["result"]

        _print_summary(bus, result)
        return 0
    finally:
        bus.close()


def cmd_shell(fresh: bool) -> int:
    """Launch the interactive shell (Workstream B) — keep prompting a persistent
    session; toolbox + conversation persist across prompts."""
    bus = events.EventBus(run_dir=RUNS_DIR)
    events.set_active(bus)
    registry = Registry(TOOLS_DIR)
    if fresh:
        registry.reset()
    try:
        from forge import shell

        shell.run_shell(bus, registry)
        return 0
    finally:
        bus.close()


def cmd_replay(path: str, speed: float) -> int:
    from forge import tui

    recorded = events.load_run(path)
    if not recorded:
        print(f"no events in {path}", file=sys.stderr)
        return 1
    tui.run_replay(recorded, speed=speed)
    # quick textual summary of the replayed run
    vm = tui.ViewModel()
    for e in recorded:
        vm.ingest(e)
    promoted = [n for n, t in vm.tools.items() if t["status"] == "promoted"]
    print(f"\nreplayed {len(recorded)} events from {path}")
    print(f"halt: {vm.halted}  ·  promoted: {promoted}  ·  cost: ${vm.cost:.4f}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="forge", description="A self-extending agent harness.")
    parser.add_argument("task", nargs="?", help="task text, or a path to a file containing it")
    parser.add_argument("--replay", metavar="PATH", help="replay a recorded runs/*.jsonl through the TUI")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier (default 1.0)")
    parser.add_argument("--fresh", action="store_true", help="wipe the toolbox before running")
    parser.add_argument("--keep", action="store_true", help="persist the toolbox (default behavior)")
    parser.add_argument("--no-tui", action="store_true", help="headless: plain-text trace, no terminal UI")
    parser.add_argument("--shell", action="store_true", help="interactive shell: keep prompting a persistent session (TUI left, chat right)")
    args = parser.parse_args(argv)

    if args.shell:
        print(f"forge shell — model={llm.DEFAULT_MODEL}\n")
        return cmd_shell(fresh=args.fresh)
    if args.replay:
        return cmd_replay(args.replay, args.speed)
    if not args.task:
        parser.print_help()
        return 2
    print(f"forge — model={llm.DEFAULT_MODEL}\n")
    return cmd_run(_read_task(args.task), fresh=args.fresh, no_tui=args.no_tui)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
