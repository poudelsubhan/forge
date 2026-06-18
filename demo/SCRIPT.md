# Forge — 90-second demo beat sheet

> **The one line:** an agent that, on hitting a capability gap, writes a new
> tool *and an independent test for it*, runs the test in a sandbox, and only
> promotes the tool on a pass. The verification gate is the product.

## Setup (before you present) — verified working

The full pipeline has been run end-to-end (Sonnet builder + Opus adversary,
`demo/task.txt`): tool synthesized, Opus test passed in-sandbox, agent halted via
`final_answer`, ~$0.15 / ~30s. These are the exact prerequisites used.

```bash
# 1. Toolchain (one-time; uv drops into ~/.local/bin, already on PATH)
curl -LsSf https://astral.sh/uv/install.sh | sh
cd ~/Projects/forge
uv sync                                   # install deps into .venv

# 2. Secrets + models
cp .env.example .env                      # then edit: set ANTHROPIC_API_KEY=sk-ant-...
# Append the demo model split (Sonnet builds, Opus is the independent adversary):
#   FORGE_MODEL=claude-sonnet-4-6
#   FORGE_TEST_MODEL=claude-opus-4-8

# 3. Rehearse once privately, then wipe for the live run
uv run main.py demo/task.txt --fresh
```

> **Model framing — say this out loud.** `FORGE_MODEL` drives BOTH the agent loop
> and the tool *author*. `FORGE_TEST_MODEL` is the *independent adversary* that
> writes the test black-box. Sonnet builds, Opus tries to break it — two
> different models, so the tester can't inherit the builder's bugs.

> **Always `--fresh` for the live synthesis run.** The seed `manifest.json` may
> reference tool files that have drifted or aren't present; `--fresh` wipes the
> toolbox so the audience watches synthesis happen from an empty registry.

Keep a recorded run as insurance: every run writes `runs/<timestamp>.jsonl`.

## Beat 1 — the gap (0:00–0:20)

```bash
uv run main.py demo/task.txt --fresh
```

- The **Toolbox** panel (left) starts **empty**. The seed registry has no
  fetch, no parse — synthesis is forced.
- The agent lays out a **Plan** (right), then emits a `request_tool` for a
  capability it doesn't have. Narrate: *"determine — it detected the gap."*

## Beat 2 — build + the (maybe) caught failure (0:20–0:50)

- A tool appears in the Toolbox as **draft → testing** (yellow → blue).
- Watch the **Event stream** (bottom): `tool_drafted`, `test_drafted`,
  `verification_run`. Narrate: *"claim — Sonnet wrote the tool, Opus wrote a
  separate adversarial test, blind to the source."*
- If the first draft fails its own test, a **red panel** shows the actual
  sandbox **stderr** — the money shot: *"establish — the gate caught a generated
  tool before it touched real work."* The tool goes **revising**, then re-verifies.

> **Note:** synthesis sometimes passes on the **first try** (it did in the
> verified run — 0 revisions). The red-panel catch is the best beat but is **not
> guaranteed**. To make a catch likely, hand the audience a trickier parse (see
> Demo 2) where a naïve first implementation more often trips the adversary.

## Beat 3 — promote + answer (0:50–1:15)

- On a pass, the tool turns **green (promoted)**. The agent immediately **calls**
  it (use count ticks up).
- The agent does the plain plan-work (the domain-frequency step) reusing the
  promoted tool, then `final_answer` — a ranked table of domains.
- The convergence indicator flips to stable and the run halts via
  **`final_answer`**, not a turn cap.

## Beat 4 — the reuse beat (1:15–1:30)

```bash
uv run main.py demo/task2.txt --keep     # NOTE: --keep, toolbox persists
```

- The Toolbox is already populated (green) and the page-1 tool is reused.
  Narrate: *"the gate paid off once; reuse is free forever after."*

> **Known gap (see `planv2.md`):** today the agent may synthesize a *near-
> duplicate* for page 2 instead of reusing/generalizing the page-1 tool. If you
> hit that live, own it: *"this is exactly the generality problem v2 fixes —
> tools should be parameterized, not minted per instance."*

## Beat 5 — the audience task (live, unrehearsed)

Take a suggestion and run it for real. **Steer the audience toward a fetch-and-
compute shape** — the sandbox only allows `httpx, json, re, html.parser,
urllib.parse, datetime, collections, math, csv, io` and **network is on**:

```bash
uv run main.py "Pull the latest XKCD via https://xkcd.com/info.0.json and return its title and number" --keep
uv run main.py "Get the front-page titles on https://lobste.rs and count how many mention 'Rust'" --keep
```

Avoid (or use to show the AST gate working): anything needing file writes,
`os`/`subprocess`, or `pandas`/`numpy` — those are rejected before they run.

> **This steering is a current limitation, not a design choice** — `planv2.md`
> Workstream C replaces it with a scoped workdir (file I/O allowed in a jail), a
> tiered import policy (BeautifulSoup, pypdf, …), and capability negotiation so
> the *system* tells the agent what's allowed instead of you pre-steering humans.

## The numbers (say these)

```bash
uv run scripts/stats.py        # aggregates the latest run
```

- **Verification catch rate** — "the gate caught X% of generated tools before
  they touched real work." (0% if it passed first try; higher on trickier tasks.)
- **Reuse ratio** — `tool_used` ÷ syntheses; run 2 should be all reuse.
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
