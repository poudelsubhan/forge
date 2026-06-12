# Forge — 90-second demo beat sheet

> **The one line:** an agent that, on hitting a capability gap, writes a new
> tool *and a test for it*, runs the test in a sandbox, and only promotes the
> tool on a pass. The verification gate is the product.

## Setup (before you present)

```bash
cp .env.example .env          # add ANTHROPIC_API_KEY
uv run main.py --fresh demo/task.txt   # do ONE warm run privately to confirm it works,
                                       # then wipe again for the live run:
uv run main.py --fresh demo/task.txt   # (or just rely on the live run being fresh)
```

Keep a recorded run as insurance: every run writes `runs/<timestamp>.jsonl`.
If the live run is too smooth or too slow, replay a good one (below).

## Beat 1 — the gap (0:00–0:20)

```bash
uv run main.py --fresh demo/task.txt
```

- The **Toolbox** panel (left) starts **empty**. The seed registry has no
  fetch, no parse — synthesis is forced.
- The agent lays out a **Plan** (right), then emits a `request_tool` for a
  capability it doesn't have. Narrate: *"determine — it detected the gap."*

## Beat 2 — build + the caught failure (0:20–0:50)

- A tool appears in the Toolbox as **draft → testing** (yellow → blue).
- Watch the **Event stream** (bottom): `tool_drafted`, `test_drafted`,
  `verification_run`. Narrate: *"claim — it wrote the tool AND a separate test."*
- When the first draft fails its own test, a **red panel** shows the actual
  sandbox **stderr**. This is the moment: *"establish — the gate caught a
  generated tool before it touched real work."* The tool goes **revising**
  (magenta), then re-verifies.

## Beat 3 — promote + reuse (0:50–1:15)

- On a pass, the tool turns **green (promoted)**. The agent immediately **calls**
  it (use count ticks up). A second synthesized tool follows the same arc.
- The agent does the plain plan-work (the domain frequency step) reusing the
  promoted tools, then `final_answer` — a ranked table.
- The convergence indicator flips to stable and the run halts via
  **`final_answer`**, not a turn cap.

## Beat 4 — the reuse beat (1:15–1:30)

```bash
uv run main.py demo/task2.txt        # NOTE: no --fresh — toolbox persists
```

- The Toolbox is already populated (green). **Zero synthesis events** — the
  agent reuses the tools instantly on page 2. Narrate: *"the gate paid off
  once; reuse is free forever after."*

## The numbers (say these)

```bash
uv run scripts/stats.py        # aggregates the latest run
```

- **Verification catch rate** — "the gate caught X% of generated tools before
  they touched real work." (Expect 30–60% on a fresh run.)
- **Reuse ratio** — run 2 is all reuse, zero synthesis.
- **Convergence** — halted via `final_answer`/`converged`, not `cap`.
- **Cost per tool** — tokens × model price, straight off the JSONL log.

## Live-demo insurance

If the live run misbehaves (HN markup hiccup, network, a too-smooth run with no
visible failure), replay a recorded one — **do not fake a bug in front of
judges**:

```bash
uv run main.py --replay runs/<good-run>.jsonl --speed 1.2
```

Replay drives the exact same TUI from the recorded event stream, including the
red failure panel, at an adjustable speed.
