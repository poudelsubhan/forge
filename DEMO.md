# Forge × Codex × Zendesk — 90-second demo

Pitch: **A Zendesk support agent that forges its own tools with Codex—and no
tool ships without passing its own test.**

APIs used: OpenAI Responses API, Codex CLI, and Zendesk Tickets API.

## Before going on stage

Open two terminals at the repository root. Confirm `.env` contains the four
required values from `.env.example`.

Terminal 1:

```bash
uv run python -m forge.mock_order_api
```

Terminal 2:

```bash
uv run seed_demo.py
```

After a successful rehearsal, explicitly bless that run for replay:

```bash
export GOOD_RUN=runs/<verified-good-run>.jsonl
uv run scripts/stats.py "$GOOD_RUN"
```

## The 90-second beat sheet

**0:00–0:10 — Zendesk queue**

Show the three seeded tickets in Zendesk. Say: “Forge is a support agent that
can extend its own capabilities, but generated code never enters the toolbox
until its own test passes.”

**0:10–0:20 — Start fresh**

```bash
uv run main.py --zendesk --fresh
```

Point to the empty Toolbox and live Tickets panel. Say: “The first refund needs
order data. Forge knows the internal API exists, but it has no tool for it.”

**0:20–0:45 — Determine → claim**

Point to `lookup_order` changing to `SYNTHESIZING`. Say: “The agent determines
the capability gap. Codex claims a solution by authoring `tool.py`; a separate
black-box Codex invocation authors the contract test without seeing the tool.”

**0:45–1:00 — Establish**

Hold on the red `TEST FAILED` state, then `REVISING` and green `PROMOTED`.
Say: “The gate caught generated code before it touched support work. Forge
passed the verbatim sandbox failure back to the correct author and promoted
only after the revised candidate passed.”

**1:00–1:15 — Reuse**

Point to ticket 1 becoming solved, then `lookup_order` use count reaching two.
Say: “The second refund reuses the verified tool immediately—zero new
synthesis.”

**1:15–1:30 — Zendesk proof**

Show all three tickets solved in the TUI, then switch to Zendesk and open both
refund replies. Say: “The replies include the policy-derived refund amount.
The toolbox persists, the event log is replayable, and the verification gate
is the product.”

## Replay fallback

If venue Wi-Fi, OpenAI, Codex, or Zendesk is unavailable:

```bash
uv run main.py --replay "$GOOD_RUN" --speed 1.2
```

The replay uses the same event-driven renderer as the live run.
