"""Idempotently replace Forge's three tagged Zendesk demo tickets."""

from __future__ import annotations

from forge.zendesk_client import ZendeskClient

TAG = "forge_demo_seed"
TICKETS = [
    {
        "subject": "Refund request for order FORGE-1001",
        "comment": {
            "body": (
                "I bought the Nimbus Headphones on July 8 and opened them, but the fit "
                "does not work for me. Please refund order FORGE-1001. — Maya"
            )
        },
        "requester": {"name": "Maya Chen", "email": "maya@example.com"},
        "tags": [TAG, "forge_refund"],
        "priority": "normal",
    },
    {
        "subject": "Please refund order FORGE-1002",
        "comment": {
            "body": (
                "The Linen Travel Shirt is the wrong size. It is unworn. Can you refund "
                "order FORGE-1002? — Leo"
            )
        },
        "requester": {"name": "Leo Martin", "email": "leo@example.com"},
        "tags": [TAG, "forge_refund"],
        "priority": "normal",
    },
    {
        "subject": "What are your support hours?",
        "comment": {
            "body": "What hours is support available? A simple email response is fine."
        },
        "requester": {"name": "Avery Stone", "email": "avery@example.com"},
        "tags": [TAG, "forge_decoy"],
        "priority": "low",
    },
]


def main() -> None:
    with ZendeskClient() as client:
        old = client.search_tickets(f"type:ticket tags:{TAG}")
        for ticket in old:
            client.delete_ticket(int(ticket["id"]))
        for payload in TICKETS:
            ticket = client.create_ticket(payload)
            print(f"created #{ticket['id']}: {ticket['subject']}")


if __name__ == "__main__":
    main()
