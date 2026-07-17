# 3-Minute Demo Script — Loop Engineering Hackathon

**Command of record (rehearsed: 78s measured):**
```bash
uv run main.py --replay demo/hackathon/golden_run.jsonl --speed 0.15
```
This replays the committed golden run through the live TUI — deterministic,
network-independent, identical rendering to a live run. **A live run
(`uv run main.py demo/hackathon/task-market-brief.md --fresh --trust`) is the
encore if judges ask; it costs ~$0.40 + ~$0.01 of x402 payments and takes
~4 minutes, so it starts BEFORE the demo in a second terminal as insurance.**

---

## 0:00 – 0:20 — The problem (talk over title/terminal)

> "Agent loops today make a bad trade: either you pre-grant your agent every
> tool and privilege it might need — and pray — or you lock it down and it's
> useless. We built Forge, a loop where **nothing is granted. Everything is
> earned, and everything is verified** — tools, privileges, even the compute."

## 0:20 – 0:50 — Architecture (one slide / README diagram)

> "The loop plans, acts, observes, self-corrects. When it hits a capability
> gap it has two moves: **BUILD** — synthesize a tool, and a *separate
> adversarial model* writes a black-box test it must pass in a sandbox — or
> **BUY** — acquire one from Zero.xyz's marketplace of 14k paid x402 tools.
> Bought or built, same gate: no tool enters the registry without passing an
> independent test. And every verified promotion feeds a **trust ratchet**:
> a Pomerium policy that starts the agent at zero privileges and widens —
> or revokes — its access based on demonstrated correctness. The whole run
> executes in a container the loop deploys on Akash's compute marketplace."

## 0:50 – 2:30 — The golden run (start the replay at 0:50)

Narrate the beats as they render (they land in this order):

1. **make_or_buy: BUILD, 403** — *"First gap: the agent tries to BUY —
   Pomerium says no. Tier zero, no purchase rights. Watch what it does: it
   doesn't stall, it BUILDS the tool instead."* (point at red `tier0` in the
   plan panel)
2. **tool_promoted → trust tier0→tier1** — *"That build passed an adversarial
   test written by a different model that never saw the source. Promotion.
   And the ratchet notices: tier 1 — it has EARNED the right to spend money."*
3. **Second gap, probe fails, builds again** — *"It tries to buy again — the
   marketplace tool's contract doesn't fit; caught by a $0.001 probe before
   wasting a cent more. Builds instead. Self-correction includes correcting
   the make-or-buy decision."*
4. **acquire_wrapped → tool_promoted via=zero → tier2** — *"Third gap: now it
   BUYS. Real x402 micropayment to a Coinbase feed, an adapter written
   against the real payload, and — same rule as ever — an adversarial test
   judges it. Promoted. See the toolbox: two tools say **built**, one says
   **acquired**. The ratchet hits tier 2."*
5. **final_answer / converged** — *"It cross-checks BTC across the two
   sources — 16 cents apart — writes the brief, and the loop halts on
   convergence, not a turn cap. Eleven turns, 37 cents, three verified
   tools, zero human input."*

## 2:30 – 3:00 — Close (stats + Akash)

Show `uv run scripts/stats.py demo/hackathon/golden_run.jsonl`, then
`runs/akash-*.jsonl` header:

> "Same event log, parsed: 3 promoted, 0 unverified, converged. And this
> other log? The identical loop, run inside a container on **Akash's**
> decentralized marketplace — bid, leased, executed, retrieved, torn down by
> one script. Plan, act, observe, self-correct — with tools it buys on
> Zero.xyz, privileges it earns through Pomerium, and compute it rents on
> Akash. Nothing granted. Everything verified. That's Forge."

---

## Failure playbook
- Replay glitches → re-run the replay command (deterministic, <2s to restart).
- Judge asks "is it real?" → switch to the pre-started live terminal; the
  x402 wallet transactions and Akash lease are on-chain evidence.
- Projector dies → `scripts/stats.py` output + this script read aloud.

## Rehearsal log
- 2026-07-17: replay at --speed 0.15 = 78s measured; narration beats fit under 3:00
  with 30s close. (speed 2 = 6.5s, scale linearly for tighter slots.)
