"""Drive Forge from the queue of seeded Zendesk tickets."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from forge import events
from forge.loop import Session
from forge.registry import Registry
from forge.zendesk_client import ZendeskClient

DEMO_TAG = "forge_demo_seed"
DEFAULT_ORDER_API = "http://127.0.0.1:8377"
SUPPORT_FACTS = "Support is staffed Monday–Friday, 9:00 AM–5:00 PM Pacific."


@dataclass(frozen=True)
class TicketRun:
    ticket_id: int
    subject: str
    reply: str
    turns: int
    halt_reason: str


@dataclass(frozen=True)
class QueueResult:
    tickets: list[TicketRun]
    passes: int
    converged: bool


def _toolbox_fingerprint(registry: Registry) -> str:
    return hashlib.sha256(registry.manifest_path.read_bytes()).hexdigest()


def _is_seeded(ticket: dict[str, Any], tag: str) -> bool:
    return tag in (ticket.get("tags") or [])


def _ticket_task(ticket: dict[str, Any], policy: str, order_api_url: str) -> str:
    requester = ticket.get("requester") or {}
    requester_text = requester.get("email") or ticket.get("requester_id") or "unknown"
    body = ticket.get("description") or ticket.get("comment", {}).get("body") or ""
    return f"""Resolve this Zendesk support ticket.

Ticket ID: {ticket["id"]}
Subject: {ticket.get("subject", "")}
Requester: {requester_text}
Body:
{body}

Refund policy:
{policy}

Internal order API base URL: {order_api_url}
{SUPPORT_FACTS}

If order data is required, use a promoted order-lookup tool or request one.
Apply the policy to the returned order data. Your final_answer must contain only
the polished customer-facing reply: no internal notes, plan, or markdown label.
Do not promise an ineligible refund."""


def process_seeded_tickets(
    client: ZendeskClient,
    registry: Registry,
    *,
    policy_path: Path | str = "fixtures/refund_policy.md",
    order_api_url: str = DEFAULT_ORDER_API,
    tag: str = DEMO_TAG,
    session_factory: Callable[[Registry], Session] = Session,
    max_passes: int = 5,
) -> QueueResult:
    """Process all open seeded tickets and require one stable empty pass."""
    policy = Path(policy_path).read_text(encoding="utf-8")
    session = session_factory(registry)
    completed: list[TicketRun] = []
    previous_empty_fingerprint: str | None = None

    for pass_number in range(1, max_passes + 1):
        open_tickets = [t for t in client.list_open_tickets() if _is_seeded(t, tag)]
        events.emit(
            "ticket_queue_polled",
            pass_number=pass_number,
            open_count=len(open_tickets),
        )
        if not open_tickets:
            fingerprint = _toolbox_fingerprint(registry)
            if previous_empty_fingerprint == fingerprint or completed:
                events.emit(
                    "ticket_queue_converged",
                    pass_number=pass_number,
                    solved=len(completed),
                )
                return QueueResult(completed, pass_number, True)
            previous_empty_fingerprint = fingerprint
            continue

        previous_empty_fingerprint = None
        for summary in sorted(open_tickets, key=lambda item: int(item["id"])):
            ticket = client.get_ticket(int(summary["id"]))
            events.emit(
                "ticket_started",
                ticket_id=ticket["id"],
                subject=ticket.get("subject", ""),
                status=ticket.get("status"),
            )
            result = session.submit(_ticket_task(ticket, policy, order_api_url))
            reply = result.final_answer
            if not reply:
                events.emit(
                    "ticket_failed",
                    ticket_id=ticket["id"],
                    reason=result.halt_reason,
                )
                raise RuntimeError(
                    f"ticket #{ticket['id']} halted via {result.halt_reason} without a reply"
                )
            client.add_reply(int(ticket["id"]), reply, public=True)
            client.solve_ticket(int(ticket["id"]))
            completed.append(
                TicketRun(
                    ticket_id=int(ticket["id"]),
                    subject=ticket.get("subject", ""),
                    reply=reply,
                    turns=result.turns,
                    halt_reason=result.halt_reason,
                )
            )
            events.emit(
                "ticket_solved",
                ticket_id=ticket["id"],
                subject=ticket.get("subject", ""),
                turns=result.turns,
            )

    events.emit("ticket_queue_failed", reason="pass_cap", max_passes=max_passes)
    return QueueResult(completed, max_passes, False)
