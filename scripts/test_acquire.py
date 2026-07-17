"""Track A standalone harness: discover → wrap → adversarial test → sandbox.

    uv run scripts/test_acquire.py

Uses a SCRATCH registry (never touches forge/tools). Exit codes:
  0 — an acquired tool was PROMOTED through the gate (full acceptance)
  2 — pipeline ran end-to-end but the paid call failed for lack of wallet
      funds (acceptance blocked ONLY on `zero wallet fund`)
  1 — anything else failed
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge import acquire, events, zero_bridge
from forge.registry import Registry
from forge.synthesis import ToolSpec

SPEC = ToolSpec(
    name="zero_btc_spot",
    purpose=(
        "Return live Bitcoin spot price data in USD, acquired from a paid "
        "Zero.xyz x402 market-data capability. Returns a dict with 'source' "
        "(capability name) and 'data' (the feed payload)."
    ),
    proposed_signature="zero_btc_spot() -> dict",
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="forge_acquire_") as tmp:
        bus = events.EventBus(run_dir=Path(tmp) / "runs")
        events.set_active(bus)
        registry = Registry(Path(tmp) / "tools")
        server = zero_bridge.start()
        try:
            result = acquire.acquire(
                registry, SPEC, bridge_url=f"http://127.0.0.1:{zero_bridge.DEFAULT_PORT}"
            )
        finally:
            zero_bridge.stop(server)
            events.set_active(None)

        print("\n=== events ===")
        for ev in bus.recent():
            keys = {k: v for k, v in ev.items() if k not in ("ts",)}
            print(f"  {keys.pop('type'):>20}  {keys}")
        bus.close()

    if result is None:
        print("RESULT: no Zero.xyz candidate found for the gap")
        return 1
    print(f"\nRESULT: {result.status}  (via=zero, tool={result.spec.name})")
    if result.status == "promoted":
        return 0
    err = (result.last_error or "").lower()
    if "insufficient funds" in err or "acquired capability failed" in err or "x402" in err:
        balance = subprocess.run(
            [zero_bridge.zero_bin(), "wallet", "balance"], capture_output=True, text=True
        ).stdout.strip()
        print(f"BLOCKED ON FUNDING: zero wallet balance = {balance!r} (need >= 0.05 USDC)")
        print("Run `zero wallet fund` in a browser, then re-run this script.")
        return 2
    print(f"last_error:\n{(result.last_error or '')[:1500]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
