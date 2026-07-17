"""Track B standalone harness: event-driven denied→allowed→revoked, live.

    uv run scripts/test_trust.py [runs/X.jsonl]

Starts a real upstream + real Pomerium, replays a recorded Forge run through
the TrustLedger, and proves with live HTTP requests that:
  1. tier0 denies the gated route (403),
  2. replayed tool_promoted events ratchet to tier1 and the SAME request
     flips to 200 with no restart and no manual step,
  3. an injected tool_failed event demotes back to tier0 → 403 again.
Exit 0 only if all three phases hold.
"""

from __future__ import annotations

import http.server
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge import events
from forge.trust import TIER_NAMES, TrustLedger

import httpx

UPSTREAM_PORT = 8900
GATE_PORT = 8901
RUN_FILE = sys.argv[1] if len(sys.argv) > 1 else "runs/20260627-194514.jsonl"


def wait_status(expected: int, timeout: float = 20.0) -> float:
    """Poll the gated route until it returns `expected`; returns seconds waited."""
    started = time.monotonic()
    last = None
    while time.monotonic() - started < timeout:
        try:
            last = httpx.get(
                f"http://127.0.0.1:{GATE_PORT}/",
                headers={"Host": f"gate.localhost:{GATE_PORT}"},
                timeout=3.0,
            ).status_code
        except httpx.HTTPError:
            last = None
        if last == expected:
            return time.monotonic() - started
        time.sleep(0.5)
    raise AssertionError(f"gated route never returned {expected} (last={last})")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="forge_trust_"))
    config = tmp / "gateway.yaml"

    upstream = http.server.ThreadingHTTPServer(
        ("127.0.0.1", UPSTREAM_PORT), http.server.SimpleHTTPRequestHandler
    )
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    bus = events.EventBus(run_dir=tmp / "runs")
    events.set_active(bus)
    ledger = TrustLedger(config)  # renders tier0 config at construction

    pomerium = subprocess.Popen(
        ["pomerium", "-config", str(config)],
        stdout=(tmp / "pomerium.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    try:
        waited = wait_status(403)
        print(f"[1] tier0: gated route DENIED (403) after {waited:.1f}s — locked down ✓")

        transitions = ledger.replay(events.iter_run(RUN_FILE))
        print(f"[2] replayed {RUN_FILE}: transitions {transitions}, "
              f"score={ledger.score}, tier={TIER_NAMES[ledger.tier]}")
        assert ledger.tier >= 1, "replay should have earned tier1"
        waited = wait_status(200)
        print(f"    same request now ALLOWED (200) after {waited:.1f}s — earned, not granted ✓")

        ledger.process({"type": "tool_failed", "name": "synthetic_regression"})
        ledger.process({"type": "tool_failed", "name": "synthetic_regression_2"})
        assert ledger.tier == 0, "failures should demote to tier0"
        waited = wait_status(403)
        print(f"[3] injected 2x tool_failed: demoted to tier0, DENIED again (403) "
              f"after {waited:.1f}s — revocable ✓")

        print("\n=== trust events emitted ===")
        for ev in bus.recent():
            if ev["type"] == "trust_tier_changed":
                print(f"  {ev['from_tier']} → {ev['to_tier']}  (score={ev['score']}, {ev['reason']})")
        print("\nPASS: denied → earned → revoked, all via events, zero manual steps")
        return 0
    finally:
        pomerium.terminate()
        upstream.shutdown()
        events.set_active(None)
        bus.close()


if __name__ == "__main__":
    sys.exit(main())
