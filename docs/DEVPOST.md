# Forge — the zero-trust, self-extending agent loop

**Tagline:** An agent loop where nothing is granted: it *builds or buys* its
own tools, every tool must survive an adversarial test before use, and its
privileges are *earned* through verified work — enforced live by Pomerium,
purchased live on Zero.xyz, executed on Akash.

## What it does

Forge is a convergence-gated agent loop. Give it a task; it plans, acts one
tool-call per turn, observes, and self-corrects — and when it hits a
capability gap it faces a **make-or-buy decision**:

- **BUILD**: one model authors a Python tool; a *different* model — which
  never sees the source — writes an adversarial black-box test; the tool only
  enters the registry if the test passes in a sandbox.
- **BUY**: it searches Zero.xyz's ~14k-tool agentic-web marketplace, makes a
  single paid x402 probe call, has an adapter written against the *real*
  payload — and then that acquired tool faces the exact same adversarial
  gate. Purchases get no special trust.

Every verified promotion feeds a **trust ratchet**: a Pomerium policy that
starts each run at tier0 (no external access — the agent literally cannot
spend money), widens on demonstrated correctness, and *revokes* on caught
failures. The loop halts when work settles (plan + toolbox survive a full
pass unchanged), not when a turn cap fires. The whole thing containerizes and
runs on Akash's decentralized compute marketplace with one script.

## How each sponsor tool maps onto the loop (plan → act → observe → self-correct)

| Loop stage | Sponsor | What actually happens |
|---|---|---|
| **act** (capability gap) | **Zero.xyz** | `zero search` finds paid x402 capabilities; a $0.001 probe call captures the real payload; the wrapper adapter is promoted only through the adversarial gate. Real USDC micropayments on Base, live in the golden run. |
| **act** (boundary) | **Pomerium** | The acquisition channel is a Pomerium route that starts DENIED. The golden run's first buy attempt is a real 403. Config hot-reload (~2s file-watch) means tier changes enforce mid-run with no restart. |
| **observe / self-correct** | **Pomerium + the gate** | Sandbox verdicts stream into the trust ledger: promotions ratchet tiers up (tier1 = may spend, tier2 = write access); a caught failure costs two promotions and can demote mid-run — we recorded a tier1→tier0→tier1 round trip in one autonomous run. |
| **substrate** | **Akash** | `scripts/akash_run.sh`: buildx amd64 image → ttl.sh → SDL deployment on sandbox-2 → provider bid → lease → headless run → event log retrieved via lease-logs → teardown. Committed evidence: `runs/akash-20260717-135556.jsonl`. |

## Judging criteria, directly

- **Idea** — "Zero-trust for agents" reframes the #1 blocker to deploying
  autonomous loops (over-privileged agents) as a *loop-engineering* problem:
  privilege becomes another thing the loop earns by verified iteration.
- **Technical implementation** — independent adversarial test authorship,
  AST-gated sandboxing, convergence-based halting, an event-sourced TUI, and
  three sponsor integrations that interlock (acquired tools execute *through*
  the Pomerium-gated bridge) rather than sit side by side.
- **Tool use** — Zero.xyz (real paid x402 calls), Pomerium (real 403s and
  live policy reloads), Akash (real on-chain deployment, bid, lease) — none
  of them mocked, all of them load-bearing.
- **Presentation** — a deterministic TUI replay of the committed golden run
  (`demo/hackathon/DEMO.md`, rehearsed at 87s inside a 3:00 script).
- **Autonomy** — the golden run consumes live market data and goes
  denial → build → earn tier1 → failed purchase caught → buy succeeds →
  tier2 → converge, with zero human input. `stats.py` proves it from the log.

## The golden run (committed: `demo/hackathon/golden_run.jsonl`)

11 turns · $0.37 · 3 tools promoted (2 built, 1 **acquired via real x402
payment**) · 0 unverified tools · BTC cross-source deviation $0.16 ·
halted by convergence, not cap · zero manual steps.

## What's next

- Tier-gated *write* actions (tier2 currently unlocks an unused write route).
- Nexla as the cross-run observe leg: stream every run's JSONL into a data
  flow that alerts on gate-catch-rate regressions.
- Wallet budget as a first-class loop constraint: cost-per-verified-output
  as the fitness function for make-or-buy.

## Team
Subhan Poudel — built on Forge, an existing personal harness; all sponsor
integrations, the trust ratchet, and the acquisition pipeline were built at
the hackathon.
