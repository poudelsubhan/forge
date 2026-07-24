"""Small synchronous Zendesk Tickets API client."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()


class ZendeskConfigError(RuntimeError):
    pass


class ZendeskClient:
    def __init__(
        self,
        subdomain: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 4,
    ) -> None:
        subdomain = subdomain or os.getenv("ZENDESK_SUBDOMAIN")
        email = email or os.getenv("ZENDESK_EMAIL")
        api_token = api_token or os.getenv("ZENDESK_API_TOKEN")
        if not all((subdomain, email, api_token)):
            raise ZendeskConfigError(
                "Set ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_TOKEN."
            )
        self.base_url = f"https://{subdomain}.zendesk.com"
        self.max_retries = max_retries
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=self.base_url,
            auth=(f"{email}/token", api_token),
            timeout=20.0,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "ZendeskClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            response = self.client.request(method, path, **kwargs)
            if response.status_code != 429 or attempt == self.max_retries:
                response.raise_for_status()
                return response
            delay = min(float(response.headers.get("Retry-After", "1")), 10.0)
            time.sleep(max(delay, 0.05))
        raise AssertionError("unreachable")

    def list_open_tickets(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/v2/tickets.json")
        return [
            ticket
            for ticket in response.json().get("tickets", [])
            if ticket.get("status") in {"new", "open", "pending"}
        ]

    def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        return self._request("GET", f"/api/v2/tickets/{ticket_id}.json").json()["ticket"]

    def add_reply(self, ticket_id: int, body: str, public: bool = True) -> dict[str, Any]:
        payload = {"ticket": {"comment": {"body": body, "public": public}}}
        return self._request("PUT", f"/api/v2/tickets/{ticket_id}.json", json=payload).json()[
            "ticket"
        ]

    def solve_ticket(self, ticket_id: int) -> dict[str, Any]:
        payload = {"ticket": {"status": "solved"}}
        return self._request("PUT", f"/api/v2/tickets/{ticket_id}.json", json=payload).json()[
            "ticket"
        ]

    def create_ticket(self, ticket: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/tickets.json", json={"ticket": ticket}).json()[
            "ticket"
        ]

    def search_tickets(self, query: str) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/v2/search.json", params={"query": query})
        return list(response.json().get("results", []))

    def delete_ticket(self, ticket_id: int) -> None:
        self._request("DELETE", f"/api/v2/tickets/{ticket_id}.json")
