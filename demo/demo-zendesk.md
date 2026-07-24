# Forge × Codex × Zendesk — 90-second demo beat sheet

> **The one line:** a Zendesk support agent that forges its own tools with
> Codex—and no tool ships without passing its own test. The verification gate
> is the product.

## Setup (before you present) — verified working

The full pipeline has passed live twice against Zendesk. In the blessed run,
Forge solved all three tickets, caught the first generated test at the gate,
revised it, promoted `lookup_order`, and reused that one verified tool for both
refunds.

```bash
cd ~/Projects/forge
uv sync

# .env must contain:
# OPENAI_API_KEY=...
# ZENDESK_SUBDOMAIN=...
# ZENDESK_EMAIL=...
# ZENDESK_API_TOKEN=...
```

Open three windows before you walk on stage:

1. **Terminal 1 — mock internal API**

   ```bash
   uv run python -m forge.mock_order_api
   ```

2. **Terminal 2 — Forge**

   ```bash
   # Run this shortly before presenting so Zendesk has three fresh tickets.
   uv run seed_demo.py

   # Keep this terminal ready, but do not start Forge yet.
   ```

3. **Browser — Zendesk**

   Open the Views/Tickets page and keep the three seeded tickets visible:

   - Refund request for order `FORGE-1001`
   - Please refund order `FORGE-1002`
   - What are your support hours?

> **Always use `--fresh` for the live synthesis beat.** It wipes the tool
> registry so the audience sees `lookup_order` forged from an empty toolbox.

## Your verified replay insurance

This is the blessed full run—not the newer empty-queue smoke test:

```bash
export GOOD_RUN=runs/20260723-191027.jsonl
uv run scripts/stats.py "$GOOD_RUN"
```

Expected numbers:

- verification catch rate: **100%** (`1/1`)
- reuse ratio: **2.00** (`2 uses / 1 synthesis`)
- promoted / failed: **1 / 0**
- tickets solved: **3**
- queue converged on pass **2**

## Beat 1 — show the real queue (0:00–0:12)

Start in the Zendesk browser window with the three tickets visible.

Say:

> “This is a real Zendesk queue: two refund requests and one simple support
> question. Forge can resolve them, but right now it has no way to look up an
> internal order.”

Open the first refund ticket just long enough to show `FORGE-1001`, then switch
to Terminal 2.

## Beat 2 — determine the gap (0:12–0:25)

```bash
uv run main.py --zendesk --fresh
```

- The **Tickets** panel shows the full queue.
- The **Toolbox** starts empty.
- Forge creates a plan, sees that refund eligibility needs order data, and
  calls `request_tool`.
- `lookup_order` appears as **SYNTHESIZING**.

Say:

> “Determine: the agent found a capability gap. It knows the order API exists,
> but it cannot fake the answer and it has no promoted tool that can call it.”

## Beat 3 — Codex makes the claim (0:25–0:45)

Watch `synthesis_requested` and `synthesis_complete` in the event stream.

Say:

> “Claim: Codex writes the typed tool in a scratch workspace. A separate Codex
> invocation writes the black-box pytest from the contract without seeing the
> implementation. We read files back from disk—we never scrape code out of
> prose.”

Point at `lookup_order` in the Toolbox. Do not switch windows while Codex is
working; the visible wait makes it clear that this is real synthesis.

## Beat 4 — establish at the gate (0:45–1:02)

The best live sequence is:

`TEST FAILED → REVISING → PROMOTED`

When the red failure panel appears, pause and let the audience read it.

Say:

> “Establish: the first candidate failed before it touched a customer. Forge
> feeds the verbatim sandbox failure back to the responsible Codex author,
> retests, and only then promotes the tool.”

> **If it passes first try:** do not apologize and do not fake a failure. Say:
> “The candidate passed its independent contract test on the first attempt, so
> the gate allows promotion. The recorded run shows the caught-failure path.”

## Beat 5 — resolve, then reuse (1:02–1:18)

- Ticket 1 becomes **SOLVED**.
- Forge calls the promoted `lookup_order` again for `FORGE-1002`.
- The Toolbox use count reaches **2** with no second synthesis.
- The simple support-hours ticket resolves without a new tool.

Say:

> “The gate paid off once. The second refund reuses the verified tool
> immediately, and the decoy ticket proves this is a queue-driven agent—not a
> one-ticket script.”

## Beat 6 — Zendesk proof (1:18–1:30)

When the TUI shows `solved 3/3` and queue convergence, switch to Zendesk.

Open the two refund tickets and show the public replies.

Say:

> “Both replies are in Zendesk, with policy-derived refund amounts. One tool,
> two uses, three solved tickets—and no generated code entered the toolbox
> without passing its own test.”

End on:

> “The verification gate is the product.”

## Live-demo insurance

If venue Wi-Fi, OpenAI, Codex, or Zendesk fails, stop troubleshooting in front
of the judges and replay the blessed run:

```bash
uv run main.py --replay "$GOOD_RUN" --speed 1.2
```

The replay drives the same Tickets, Toolbox, Plan, failure panel, and event
stream from the recorded JSONL.

If the live run completes but the failure is too fast to narrate, replay just
afterward and pause verbally on the red `TEST FAILED` panel.

## The numbers (if a judge asks)

```bash
uv run scripts/stats.py "$GOOD_RUN"
```

- **Verification catch rate** — “The gate caught 100% of generated candidates
  that needed correction before promotion in this run.”
- **Reuse ratio** — “One synthesis produced two real tool uses.”
- **Convergence** — “All three tickets were solved and the queue survived a
  full unchanged pass.”
- **Privacy boundary** — OpenAI Responses calls use `store=False`; Codex and
  promoted-tool subprocesses receive scrubbed environments; `.env` and run
  logs are local and gitignored.

## Do not do these live

- Do not run `seed_demo.py` after starting Forge; it deletes and recreates only
  the tagged demo tickets.
- Do not omit `--fresh` for the main synthesis performance.
- Do not use `runs/20260723-192734.jsonl` as replay insurance; it is the
  empty-queue TUI smoke test.
- Do not promise that the red failure is deterministic. Own a first-pass
  success and use the blessed replay for the caught-failure story.
- Do not show `.env`, tokens, or terminal commands that print environment
  variables.
