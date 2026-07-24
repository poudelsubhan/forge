from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from forge import codex_adapter
from forge.registry import Registry
from forge.synthesis import ToolSpec, synthesize
from forge.zendesk_driver import process_seeded_tickets


def _candidate(workspace: Path, tool_code: str) -> codex_adapter.ToolCandidate:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "tool.py").write_text(tool_code, encoding="utf-8")
    (workspace / "test_tool.py").write_text(
        "from tool import double\n\n"
        "def test_double():\n"
        "    assert double(4) == 8\n"
        "    assert double(-2) == -4\n",
        encoding="utf-8",
    )
    return codex_adapter.ToolCandidate(
        tool_name="double",
        workspace=workspace,
        tool_file=workspace / "tool.py",
        test_file=workspace / "test_tool.py",
    )


def test_failed_candidate_is_revised_then_promoted(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"

    def fake_synthesize(*args, **kwargs):
        return _candidate(
            workspace,
            "def double(value: int) -> int:\n    return value + 2\n",
        )

    seen_failure: list[str] = []

    def fake_revise(workspace_dir, stderr_text, **kwargs):
        seen_failure.append(stderr_text)
        return _candidate(
            Path(workspace_dir),
            "def double(value: int) -> int:\n    return value * 2\n",
        )

    monkeypatch.setattr(codex_adapter, "synthesize", fake_synthesize)
    monkeypatch.setattr(codex_adapter, "revise", fake_revise)
    registry = Registry(tmp_path / "tools")
    result = synthesize(
        registry,
        ToolSpec("double", "Double an integer", "double(value: int) -> int"),
        timeout=10,
    )

    assert seen_failure
    assert result.status == "promoted"
    assert result.revisions == 1
    assert registry.dispatch("double", {"value": 6}) == 12


class FakeZendesk:
    def __init__(self) -> None:
        self.tickets = {
            1: {
                "id": 1,
                "subject": "Refund",
                "description": "Refund FORGE-1001",
                "status": "open",
                "tags": ["forge_demo_seed"],
            },
            2: {
                "id": 2,
                "subject": "Ignore",
                "description": "Unseeded",
                "status": "open",
                "tags": [],
            },
        }
        self.replies: list[tuple[int, str, bool]] = []

    def list_open_tickets(self):
        return [ticket for ticket in self.tickets.values() if ticket["status"] == "open"]

    def get_ticket(self, ticket_id: int):
        return self.tickets[ticket_id]

    def add_reply(self, ticket_id: int, body: str, public: bool = True):
        self.replies.append((ticket_id, body, public))

    def solve_ticket(self, ticket_id: int):
        self.tickets[ticket_id]["status"] = "solved"


class FakeSession:
    instances = 0

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        FakeSession.instances += 1

    def submit(self, task: str):
        assert "Refund policy:" in task
        assert "http://127.0.0.1:8377" in task
        return SimpleNamespace(
            final_answer="Your refund is approved.",
            turns=3,
            halt_reason="final_answer",
        )


def test_ticket_driver_posts_reply_solves_and_reuses_session(tmp_path: Path) -> None:
    client = FakeZendesk()
    registry = Registry(tmp_path / "tools")
    policy = tmp_path / "policy.md"
    policy.write_text("Refund within 30 days.", encoding="utf-8")
    FakeSession.instances = 0

    result = process_seeded_tickets(
        client,  # type: ignore[arg-type]
        registry,
        policy_path=policy,
        session_factory=FakeSession,  # type: ignore[arg-type]
    )

    assert result.converged
    assert result.passes == 2
    assert [ticket.ticket_id for ticket in result.tickets] == [1]
    assert client.replies == [(1, "Your refund is approved.", True)]
    assert client.tickets[1]["status"] == "solved"
    assert client.tickets[2]["status"] == "open"
    assert FakeSession.instances == 1
