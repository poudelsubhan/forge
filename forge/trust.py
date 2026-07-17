"""Trust ratchet: verified correctness earns egress, failures revoke it (Track B).

The loop starts at tier0 — no external access at all. Every `tool_promoted`
event (a tool that survived its own adversarial test in the sandbox) raises the
ledger's score; every `tool_failed` (a tool the gate caught) costs more than a
promotion earned. The tier is a pure function of the score, and each tier
renders a Pomerium config from `deploy/pomerium/templates/gateway.yaml`:

    tier0 — nothing reachable (all routes deny; every egress attempt → 403)
    tier1 — read-only routes open (gate.localhost + zero.localhost)
    tier2 — write route opens too

Pomerium hot-reloads on config file change (~2-4s, proven in recon 0.2), so
`apply()` is just an atomic file write. Privileges are never granted — they
are earned, and they are revocable.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from forge import events

TIER_NAMES = ("tier0", "tier1", "tier2")
TIER1_SCORE = 1  # promotions (net of failures) to open read-only egress
TIER2_SCORE = 3  # to open write egress
FAILURE_COST = 2  # one caught failure erases two earned promotions

DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "deploy/pomerium/templates/gateway.yaml"
DEFAULT_PORT = 8901


def tier_of(score: int) -> int:
    if score >= TIER2_SCORE:
        return 2
    if score >= TIER1_SCORE:
        return 1
    return 0


class TrustLedger:
    """Consumes loop events, maintains the tier, applies Pomerium policy."""

    def __init__(
        self,
        config_path: Path | str,
        read_upstream: str = "http://127.0.0.1:8900",
        zero_upstream: str = "http://127.0.0.1:8477",
        write_upstream: str = "http://127.0.0.1:8900",
        port: int = DEFAULT_PORT,
        template_path: Path | str = DEFAULT_TEMPLATE,
    ) -> None:
        self.config_path = Path(config_path)
        self.template = Path(template_path).read_text(encoding="utf-8")
        self.read_upstream = read_upstream
        self.zero_upstream = zero_upstream
        self.write_upstream = write_upstream
        self.port = port
        self.score = 0
        self.tier = 0
        self.apply()  # start locked down

    # --- policy ---------------------------------------------------------------

    def render(self) -> str:
        return self.template.format(
            port=self.port,
            read_upstream=self.read_upstream,
            zero_upstream=self.zero_upstream,
            write_upstream=self.write_upstream,
            read_allow="true" if self.tier >= 1 else "false",
            write_allow="true" if self.tier >= 2 else "false",
        )

    def apply(self) -> None:
        """Atomic write; Pomerium's file watch picks it up without a restart."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.config_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self.render())
        os.replace(tmp, self.config_path)

    # --- ratchet --------------------------------------------------------------

    def process(self, event: dict[str, Any]) -> bool:
        """Feed one loop event through the ratchet. Returns True when the tier
        changed (and the new policy was applied)."""
        etype = event.get("type")
        if etype == "tool_promoted":
            self.score += 1
            reason = f"tool_promoted:{event.get('name')}"
        elif etype == "tool_failed":
            self.score = max(0, self.score - FAILURE_COST)
            reason = f"tool_failed:{event.get('name')}"
        else:
            return False

        new_tier = tier_of(self.score)
        if new_tier == self.tier:
            return False
        old = self.tier
        self.tier = new_tier
        self.apply()
        events.emit(
            "trust_tier_changed",
            from_tier=TIER_NAMES[old],
            to_tier=TIER_NAMES[new_tier],
            score=self.score,
            reason=reason,
        )
        return True

    def replay(self, event_stream: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
        """Feed a recorded run through the ratchet; returns tier transitions."""
        transitions: list[tuple[str, str]] = []
        for event in event_stream:
            before = self.tier
            if self.process(event):
                transitions.append((TIER_NAMES[before], TIER_NAMES[self.tier]))
        return transitions
