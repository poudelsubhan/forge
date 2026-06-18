"""Interactive Forge shell (Workstream B) — a Claude-Code-style REPL.

A vertical split:

  * **Left** — the live TUI panels (toolbox · plan · event stream), reused
    verbatim from `tui.py` (same `ViewModel`, same renderers, so live runs,
    replay, and the shell all share one source of truth).
  * **Right** — a chat log that shows the agent's thinking and answers, plus an
    input box. You keep prompting; the toolbox and conversation persist across
    prompts via a single `loop.Session`.

The loop runs on a background worker thread and emits to the event bus; a poll
timer re-derives the panels and appends new chat lines. The UI never blocks on
the agent, and the agent never blocks on the UI — they're decoupled by the bus,
exactly as the live TUI already is.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static
from textual.worker import WorkerState

from forge import events, loop
from forge.registry import Registry
from forge.tui import ViewModel, _plan_panel, _stream_panel, _toolbox_panel

# Events surfaced in the right-hand chat pane (the rest stay in the left stream).
_CHAT_TYPES = {
    "run_start",
    "prompt",
    "agent_message",
    "gap_detected",
    "verification_failed",
    "tool_promoted",
    "tool_failed",
    "tool_used",
    "halted",
}


def _chat_line(e: dict[str, Any]) -> str | None:
    """Format one event as a chat line (rich markup), or None to skip."""
    t = e.get("type")
    if t in ("run_start", "prompt"):
        return f"\n[bold cyan]you ›[/] {e.get('task', '')}"
    if t == "agent_message":
        text = (e.get("text") or "").strip()
        if not text:
            return None
        if e.get("final"):
            return f"[bold green]forge ⊙[/] {text}"
        return f"[dim]forge · thinking[/] {text}"
    if t == "gap_detected":
        return f"[yellow]  ✦ capability gap → authoring [b]{e.get('name')}[/b][/]"
    if t == "verification_failed":
        return f"[red]  ✗ {e.get('name')} failed its test — revising[/]"
    if t == "tool_promoted":
        return f"[green]  ✓ promoted [b]{e.get('name')}[/b] ({e.get('revisions')} rev)[/]"
    if t == "tool_failed":
        return f"[bold red]  ✗ {e.get('name')} could not be verified — gate refused it[/]"
    if t == "tool_used":
        return f"[green]  → used {e.get('name')}[/]"
    if t == "halted":
        return f"[cyan]  — halted ({e.get('reason')})[/]"
    return None


class ForgeShell(App):
    CSS = """
    #left  { width: 58%; }
    #right { width: 42%; border-left: solid $accent; }
    #toolbox { height: auto; }
    #plan    { height: auto; }
    #events  { height: 1fr; }
    #chat    { height: 1fr; padding: 0 1; }
    #streaming { height: auto; max-height: 10; padding: 0 1; color: $text-muted; }
    #prompt  { dock: bottom; }
    """

    TITLE = "Forge — interactive shell"

    def __init__(self, bus: events.EventBus, session: loop.Session) -> None:
        super().__init__()
        self.bus = bus
        self.session = session
        self._cursor = 0  # how many bus events already rendered to chat
        self._busy = False
        self._stream_buf = ""  # live token stream for the current prompt

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left"):
                yield Static(id="toolbox")
                yield Static(id="plan")
                yield Static(id="events")
            with Vertical(id="right"):
                yield RichLog(id="chat", wrap=True, markup=True, highlight=False)
                yield Static(id="streaming")
                yield Input(placeholder="Ask Forge to do something on the live web…", id="prompt")

    def on_mount(self) -> None:
        # Stream agent-turn tokens live (from the worker thread) into the
        # streaming pane — this is the "thinking displayed" view.
        self.session.stream_cb = lambda delta: self.call_from_thread(self._on_token, delta)
        self.set_interval(0.2, self._refresh)
        chat = self.query_one("#chat", RichLog)
        chat.write("[bold]Forge[/] ready. Type a task and press enter. The toolbox persists across prompts.")
        self.query_one("#prompt", Input).focus()

    def _on_token(self, delta: str) -> None:
        """Append a streamed token and repaint the live pane (tail only)."""
        self._stream_buf += delta
        tail = self._stream_buf[-1200:]
        self.query_one("#streaming", Static).update(Text(f"forge · streaming\n{tail}", style="dim italic"))

    def _refresh(self) -> None:
        snapshot = list(self.bus.events)
        vm = ViewModel()
        for e in snapshot:
            vm.ingest(e)
        self.query_one("#toolbox", Static).update(_toolbox_panel(vm))
        self.query_one("#plan", Static).update(_plan_panel(vm))
        self.query_one("#events", Static).update(_stream_panel(vm))

        chat = self.query_one("#chat", RichLog)
        for e in snapshot[self._cursor :]:
            if e.get("type") in _CHAT_TYPES:
                line = _chat_line(e)
                if line is not None:
                    chat.write(line)
        self._cursor = len(snapshot)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        if not task or self._busy:
            return
        prompt = self.query_one("#prompt", Input)
        prompt.value = ""
        prompt.disabled = True
        self._busy = True
        self._stream_buf = ""
        self.query_one("#streaming", Static).update("")
        # The user line is rendered by the poll from the run_start/prompt event
        # (Session.submit emits one), so we don't echo here — avoids duplicates.
        self.run_worker(lambda: self.session.submit(task), thread=True, exclusive=True)

    def on_worker_state_changed(self, event) -> None:
        if event.state in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            self._busy = False
            self.query_one("#streaming", Static).update("")  # final answer lives in the chat log
            prompt = self.query_one("#prompt", Input)
            prompt.disabled = False
            prompt.focus()


def run_shell(bus: events.EventBus, registry: Registry) -> None:
    """Launch the interactive shell over a persistent Session."""
    session = loop.Session(registry)
    ForgeShell(bus, session).run()
