"""Rich terminal UI (Phase 4A).

A 3-panel `rich.live.Live` layout:

  * **Left — Toolbox:** every tool, status-colored (draft=yellow, testing=blue,
    failed=red, promoted=green), with use + revision counts. Failed tools stay
    visible (the graveyard is part of the story).
  * **Right — Plan:** the current plan with per-step status, plus the
    convergence indicator (toolbox stable ✓/✗ · plan stable ✓/✗).
  * **Bottom — Event stream:** a scrolling log. Verification failures render
    prominently — a red panel with the actual sandbox stderr excerpt, so the
    caught failure is *seen*, not summarized away.

The UI is a pure function of the event stream: it reconstructs all state from
events via `ViewModel`. That unifies live mode (poll the event bus deque) and
replay mode (feed events from a recorded JSONL) behind one renderer, and it
means the loop never blocks on rendering — the two are decoupled by the deque.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from forge.events import EventBus

_STATUS_STYLE = {
    "draft": "yellow",
    "testing": "blue",
    "synthesizing": "yellow",
    "test failed": "bold red",
    "revising": "magenta",
    "failed": "red",
    "promoted": "green",
}


class ViewModel:
    """Reconstructs render state from the event stream, incrementally."""

    def __init__(self) -> None:
        self.task = ""
        self.model = ""
        self.turn = 0
        self.toolbox_version = 0
        self.tools: dict[str, dict[str, Any]] = {}
        self.plan: list[dict[str, str]] = []
        self.tickets: dict[int, dict[str, Any]] = {}
        self.log: list[dict[str, Any]] = []
        self.cost = 0.0
        self.in_tokens = 0
        self.out_tokens = 0
        self.llm_calls = 0
        self.halted: str | None = None
        self.last_failure: tuple[str, str] | None = None
        self.toolbox_stable: bool | None = None
        self.plan_stable: bool | None = None

    def _tool(self, name: str) -> dict[str, Any]:
        return self.tools.setdefault(
            name, {"status": "draft", "revisions": 0, "uses": 0, "signature": name}
        )

    def ingest(self, e: dict[str, Any]) -> None:
        t = e.get("type")
        self.log.append(e)

        if t == "run_start":
            self.task = e.get("task", "")
            self.model = e.get("model", "")
        elif t == "toolbox_snapshot":
            for record in e.get("tools", []):
                tool = self._tool(record["name"])
                tool.update(
                    status=record.get("status", "draft"),
                    revisions=record.get("revisions", 0),
                    uses=record.get("uses", 0),
                    signature=record.get("signature", record["name"]),
                )
        elif t == "turn_start":
            self.turn = e.get("turn", self.turn)
            self.toolbox_version = e.get("toolbox_version", self.toolbox_version)
        elif t == "gap_detected":
            tool = self._tool(e["name"])
            tool["status"] = "draft"
            tool["signature"] = e.get("signature", tool["signature"])
        elif t == "synthesis_requested":
            self._tool(e["name"])["status"] = "synthesizing"
        elif t == "tool_drafted":
            tool = self._tool(e["name"])
            tool["status"] = "draft"
            tool["signature"] = e.get("signature", tool["signature"])
        elif t == "verification_run":
            self._tool(e["name"])["status"] = "testing"
        elif t == "verification_failed":
            self._tool(e["name"])["status"] = "test failed"
            excerpt = e.get("stderr") or e.get("rejected_reason") or e.get("stdout") or "(no output)"
            self.last_failure = (e["name"], excerpt)
        elif t == "tool_revised":
            tool = self._tool(e["name"])
            tool["revisions"] = e.get("revision", tool["revisions"])
            tool["status"] = "draft"
        elif t == "revision_requested":
            self._tool(e["name"])["status"] = "revising"
        elif t == "tool_promoted":
            tool = self._tool(e["name"])
            tool["status"] = "promoted"
            tool["revisions"] = e.get("revisions", tool["revisions"])
            self.last_failure = None
        elif t == "tool_failed":
            tool = self._tool(e["name"])
            tool["status"] = "failed"
            tool["revisions"] = e.get("revisions", tool["revisions"])
        elif t == "tool_used":
            self._tool(e["name"])["uses"] = e.get("uses", self._tool(e["name"])["uses"] + 1)
        elif t == "plan_updated":
            self.plan = e.get("steps", [])
        elif t == "ticket_started":
            ticket_id = int(e["ticket_id"])
            self.tickets[ticket_id] = {
                "id": ticket_id,
                "subject": e.get("subject", ""),
                "status": e.get("status") or "open",
            }
        elif t == "ticket_queue_polled":
            for record in e.get("tickets", []):
                ticket_id = int(record["id"])
                ticket = self.tickets.setdefault(ticket_id, {"id": ticket_id})
                ticket.update(
                    subject=record.get("subject", ""),
                    status=record.get("status") or "open",
                )
        elif t == "ticket_solved":
            ticket_id = int(e["ticket_id"])
            ticket = self.tickets.setdefault(
                ticket_id,
                {"id": ticket_id, "subject": e.get("subject", ""), "status": "open"},
            )
            ticket["status"] = "solved"
        elif t == "ticket_failed":
            ticket_id = int(e["ticket_id"])
            ticket = self.tickets.setdefault(
                ticket_id, {"id": ticket_id, "subject": "", "status": "open"}
            )
            ticket["status"] = "failed"
        elif t == "convergence_check":
            self.toolbox_stable = not e.get("toolbox_changed", False)
            self.plan_stable = not e.get("plan_mutated", False)
        elif t == "llm_call":
            self.cost += e.get("cost_usd", 0.0)
            self.in_tokens += e.get("input_tokens", 0)
            self.out_tokens += e.get("output_tokens", 0)
            self.llm_calls += 1
        elif t == "halted":
            self.halted = e.get("reason")


# --- panels ------------------------------------------------------------------


def _toolbox_panel(vm: ViewModel) -> Panel:
    table = Table(expand=True, show_edge=False, pad_edge=False)
    table.add_column("tool", overflow="fold")
    table.add_column("status", justify="center")
    table.add_column("rev", justify="right")
    table.add_column("use", justify="right")
    if not vm.tools:
        table.add_row(Text("(empty — synthesis not yet triggered)", style="dim"), "", "", "")
    for name, tool in vm.tools.items():
        style = _STATUS_STYLE.get(tool["status"], "white")
        table.add_row(
            Text(name, style=style),
            Text(tool["status"].upper(), style=style),
            str(tool["revisions"]),
            str(tool["uses"]),
        )
    promoted = sum(1 for t in vm.tools.values() if t["status"] == "promoted")
    failed = sum(1 for t in vm.tools.values() if t["status"] == "failed")
    subtitle = f"promoted {promoted} · failed {failed} · v{vm.toolbox_version}"
    return Panel(table, title="[bold]Toolbox[/]", subtitle=subtitle, border_style="cyan")


def _tickets_panel(vm: ViewModel) -> Panel:
    table = Table(expand=True, show_edge=False, pad_edge=False)
    table.add_column("id", width=5)
    table.add_column("subject", overflow="ellipsis")
    table.add_column("status", justify="right")
    if not vm.tickets:
        table.add_row("", Text("(waiting for queue)", style="dim"), "")
    for ticket_id, ticket in vm.tickets.items():
        status = ticket["status"]
        style = {
            "solved": "green",
            "failed": "red",
            "open": "yellow",
            "new": "yellow",
            "pending": "blue",
        }.get(status, "white")
        table.add_row(
            f"#{ticket_id}",
            ticket["subject"],
            Text(status.upper(), style=style),
        )
    solved = sum(1 for ticket in vm.tickets.values() if ticket["status"] == "solved")
    return Panel(
        table,
        title="[bold]Tickets[/]",
        subtitle=f"solved {solved}/{len(vm.tickets)}",
        border_style="cyan",
    )


def _plan_panel(vm: ViewModel) -> Panel:
    rows: list[Any] = []
    if not vm.plan:
        rows.append(Text("(no plan yet)", style="dim"))
    for i, step in enumerate(vm.plan, 1):
        mark = {"done": "[green]✔[/]", "pending": "[yellow]○[/]", "blocked": "[red]✗[/]"}.get(
            step.get("status", "pending"), "○"
        )
        rows.append(Text.from_markup(f"{mark} {i}. {step.get('step', '')}"))

    def fmt(flag: bool | None) -> str:
        if flag is None:
            return "[dim]–[/]"
        return "[green]✓[/]" if flag else "[red]✗[/]"

    indicator = Text.from_markup(
        f"toolbox stable: {fmt(vm.toolbox_stable)}  ·  plan stable: {fmt(vm.plan_stable)}"
    )
    body = Group(*rows, Text(""), indicator)
    subtitle = f"turn {vm.turn}" + (f" · HALTED ({vm.halted})" if vm.halted else "")
    return Panel(body, title="[bold]Plan[/]", subtitle=subtitle, border_style="cyan")


_EVENT_STYLE = {
    "gap_detected": "yellow",
    "synthesis_requested": "yellow",
    "synthesis_complete": "yellow",
    "revision_requested": "magenta",
    "tool_drafted": "yellow",
    "test_drafted": "yellow",
    "verification_run": "blue",
    "verification_failed": "red",
    "tool_revised": "magenta",
    "tool_promoted": "bold green",
    "tool_failed": "bold red",
    "tool_used": "green",
    "halted": "bold cyan",
    "plan_updated": "white",
    "convergence_check": "dim",
    "agent_message": "white",
    "toolbox_snapshot": "dim",
    "ticket_queue_polled": "dim",
    "ticket_started": "yellow",
    "ticket_solved": "green",
    "ticket_failed": "red",
    "ticket_queue_converged": "bold cyan",
}


def _event_line(e: dict[str, Any]) -> Text:
    t = e.get("type", "")
    style = _EVENT_STYLE.get(t, "dim")
    detail = ""
    if t == "gap_detected":
        detail = f"{e.get('name')} — {e.get('purpose', '')[:60]}"
    elif t in ("synthesis_requested", "synthesis_complete", "revision_requested"):
        detail = f"{e.get('name')}"
    elif t in ("tool_drafted", "test_drafted"):
        detail = f"{e.get('name')}"
    elif t == "verification_run":
        detail = f"{e.get('name')} (attempt {e.get('attempt')})"
    elif t == "tool_revised":
        detail = f"{e.get('name')} → revision {e.get('revision')}"
    elif t == "tool_promoted":
        detail = f"{e.get('name')} ✓ ({e.get('revisions')} rev, {e.get('duration_s')}s)"
    elif t == "tool_failed":
        detail = f"{e.get('name')} ✗ after {e.get('revisions')} revisions"
    elif t == "tool_used":
        detail = f"{e.get('name')} (×{e.get('uses')})"
    elif t == "plan_updated":
        detail = f"{len(e.get('steps', []))} steps"
    elif t == "halted":
        detail = f"reason={e.get('reason')} turn={e.get('turn')}"
    elif t == "agent_message":
        tag = "FINAL: " if e.get("final") else ""
        detail = tag + e.get("text", "")[:80].replace("\n", " ")
    elif t == "llm_call":
        detail = f"{e.get('label')} {e.get('input_tokens')}→{e.get('output_tokens')} tok ${e.get('cost_usd')}"
    elif t in ("ticket_started", "ticket_solved", "ticket_failed"):
        detail = f"#{e.get('ticket_id')} {e.get('subject', '')[:55]}"
    elif t == "ticket_queue_converged":
        detail = f"{e.get('solved')} solved · pass {e.get('pass_number')}"
    return Text.from_markup(f"[{style}]{t:<18}[/] {detail}")


def _stream_panel(vm: ViewModel, height: int = 12) -> Panel:
    lines = [_event_line(e) for e in vm.log[-(height):]]
    body: Any = Group(*lines) if lines else Text("(waiting…)", style="dim")
    if vm.last_failure is not None:
        name, excerpt = vm.last_failure
        excerpt = "\n".join(excerpt.strip().splitlines()[-6:])
        fail = Panel(
            Text(excerpt, style="red"),
            title=f"[bold red]VERIFICATION FAILED — {name}[/] (the gate caught it)",
            border_style="red",
        )
        body = Group(body, fail)
    cost = f"${vm.cost:.4f} · {vm.llm_calls} calls · {vm.in_tokens}+{vm.out_tokens} tok"
    return Panel(body, title="[bold]Event stream[/]", subtitle=cost, border_style="cyan")


def build_layout(vm: ViewModel) -> Layout:
    root = Layout()
    root.split_column(Layout(name="top", ratio=3), Layout(name="bottom", ratio=2))
    root["top"].split_row(
        Layout(name="tickets"),
        Layout(name="toolbox"),
        Layout(name="plan"),
    )
    root["top"]["tickets"].update(_tickets_panel(vm))
    root["top"]["toolbox"].update(_toolbox_panel(vm))
    root["top"]["plan"].update(_plan_panel(vm))
    root["bottom"].update(_stream_panel(vm))
    return root


# --- drivers -----------------------------------------------------------------


def run_live(bus: EventBus, is_done: Callable[[], bool], refresh_per_second: int = 8) -> None:
    """Live mode: re-derive the view from the bus deque each tick until done."""
    interval = 1.0 / refresh_per_second
    with Live(build_layout(ViewModel()), refresh_per_second=refresh_per_second, screen=False) as live:
        while True:
            vm = ViewModel()
            for e in list(bus.events):
                vm.ingest(e)
            live.update(build_layout(vm))
            if is_done():
                # one final paint to capture the last events
                vm = ViewModel()
                for e in list(bus.events):
                    vm.ingest(e)
                live.update(build_layout(vm))
                break
            time.sleep(interval)


def run_replay(events: list[dict[str, Any]], speed: float = 1.0) -> ViewModel:
    """Replay a recorded run through the TUI. `speed` scales inter-event delay."""
    vm = ViewModel()
    base_delay = 0.12 / max(speed, 0.01)
    pause = {"verification_failed": 6.0, "tool_promoted": 4.0, "halted": 4.0, "gap_detected": 2.0}
    with Live(build_layout(vm), refresh_per_second=12, screen=False) as live:
        for e in events:
            vm.ingest(e)
            live.update(build_layout(vm))
            time.sleep(base_delay * pause.get(e.get("type", ""), 1.0))
        live.update(build_layout(vm))
    return vm
