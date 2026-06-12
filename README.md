# Forge — a self-extending agent harness

An agent that, on hitting a capability gap, **authors a new tool plus a test for
that tool**, runs the test in a sandbox, and only promotes the tool into its
registry on a pass. The control loop is convergence-gated: a run halts when the
plan and toolbox survive a full pass unchanged (or `final_answer` fires), not
when a turn cap is hit.

> **Core invariant: no tool enters the registry without passing its own test in
> the sandbox. The verification gate is the product.**

## determine · claim · establish

The harness maps cleanly onto a determine → claim → establish arc:

| stage | what happens | in the code |
|-------|--------------|-------------|
| **determine** | detect the capability gap | the agent emits a `request_tool` call when no promoted tool covers a step |
| **claim** | author the tool **and** a separate test of its contract | `synthesis.author_tool` + `synthesis.author_test` (two distinct LLM calls) |
| **establish** | verify in a sandbox; promote only on a pass | `sandbox.run_test` → `registry.promote` / `mark_failed` |

Tool and test authorship are **separate LLM calls** on purpose: a single call
writing both produces a test that mirrors the implementation's bugs. The test
author sees the spec and signature, and is told to test the contract — not echo
the code.

## Architecture

```
                          task
                            │
                            ▼
        ┌──────────────────────────────────────────────┐
        │  loop.py  — convergence-gated control loop    │
        │                                               │
        │  serialize state ─▶ agent turn (tool-use) ─┐  │
        │        ▲                                   │  │
        │        │           ┌── update_plan ────────┤  │
        │        │           ├── promoted tool ──────┤  │   ┌── halt: final_answer
        │        └───────────┼── request_tool        │  ├──▶├── halt: converged
        │                    └── final_answer ────────┘  │   └── halt: cap (25 turns)
        └───────────┬───────────────────────┬───────────┘
                    │ request_tool           │ dispatch
                    ▼                         ▼
        ┌───────────────────────┐   ┌──────────────────┐
        │ synthesis.py          │   │ registry.py       │
        │  author_tool  (LLM)   │   │  manifest.json    │  ◀── single source of truth
        │  author_test  (LLM)   │◀─▶│  promoted tools   │
        │  verify + revise×3    │   │  draft/testing/   │
        └──────────┬────────────┘   │  failed/promoted  │
                   │ run_test       └──────────────────┘
                   ▼
        ┌───────────────────────┐
        │ sandbox.py            │   AST gate → isolated subprocess
        │  pass / fail + stderr │   (env stripped to PATH, network on)
        └───────────────────────┘

   every transition ─▶ events.py ─▶ runs/<ts>.jsonl   (the single observability source)
                                       │
                          ┌────────────┴────────────┐
                          ▼                          ▼
                     tui.py (live)            scripts/stats.py
                     tui.py --replay          (metrics)
```

`manifest.json` is the single source of truth for tool state; the TUI and
`to_anthropic_tools()` both read from it, and in-memory state never drifts from
disk. Anthropic tool schemas are derived from `inspect.signature` + type hints —
the LLM never writes its own schema (it would drift from the actual function).

## Quickstart

```bash
cp .env.example .env      # add your ANTHROPIC_API_KEY
uv run main.py demo/task.txt
```

```bash
uv run main.py "Summarize the titles on https://example.com"   # task as a string
uv run main.py demo/task.txt --fresh    # wipe the toolbox first
uv run main.py demo/task2.txt           # reuse the persisted toolbox (no --fresh)
uv run main.py --replay runs/<ts>.jsonl --speed 1.2   # replay a recorded run
uv run main.py "..." --no-tui           # headless plain-text trace
uv run scripts/stats.py                 # metrics for the latest run
```

The toolbox **persists across runs by default** (`--keep`) — that persistence is
what makes the run-2 reuse beat work. `--fresh` wipes it.

### Model

Defaults to `claude-sonnet-4-6` (current Sonnet — fast and cheap enough for a
loop that makes many calls). Override with `FORGE_MODEL`:

```bash
FORGE_MODEL=claude-opus-4-8 uv run main.py demo/task.txt   # max synthesis quality
FORGE_MODEL=claude-sonnet-4-5 uv run main.py demo/task.txt # the plan's original pin
```

## The TUI

A live 3-panel `rich` layout (`tui.py`), reconstructed purely from the event
stream so the same renderer drives live runs and `--replay`:

- **Toolbox** (left): tools status-colored — draft `yellow`, testing `blue`,
  failed `red`, promoted `green` — with use and revision counts. Failed tools
  stay visible: the graveyard is part of the story.
- **Plan** (right): the current plan with per-step status + a convergence
  indicator (toolbox stable ✓/✗ · plan stable ✓/✗).
- **Event stream** (bottom): a scrolling log. A verification failure renders as
  a **red panel with the actual sandbox stderr** — the caught failure is *seen*,
  not summarized away.

The loop runs on a background thread and emits to the event bus; the TUI polls
the deque. The loop never blocks on rendering.

## Observability

The JSONL log (`runs/<ts>.jsonl`) is the single source. `scripts/stats.py`
aggregates it into:

- **Verification catch rate** — fraction of drafted tools that failed ≥1
  verification before promotion. *"The gate caught X% of generated tools before
  they touched real work."*
- **Reuse ratio** — `tool_used` events ÷ syntheses. Run 2 (`--keep`) is all
  reuse, zero synthesis.
- **Convergence quality** — halted via `final_answer`/`converged` vs. `cap`.
- **Cost** — per LLM call (model, input/output tokens, dollar cost, latency,
  input hash) logged at the call site, aggregated into total and **per-tool**
  cost. The cost meter ships with the feature, not after.

Every LLM call logs `llm_call`; every external call (the sandbox subprocess,
synthesized tools' HTTP) is bounded by a timeout. A run that halts via `cap` (or
any synthesis exceeding 3 revisions) is flagged loudly — both indicate prompt
regressions.

## Sandbox limitations (stated honestly)

This is **hackathon-grade isolation**, not a security boundary:

- **AST allowlist, not a real sandbox.** `sandbox.ast_check` rejects imports
  outside an allowlist (`httpx`, `json`, `re`, `html.parser`, `urllib.parse`,
  `datetime`, `collections`, `math`, `csv`, `io`, …), `os.system` / `subprocess`
  / `eval` / `exec`, and `open(...)` in a write mode. A determined adversary can
  defeat static analysis; this stops *accidents and obvious misuse*, not attacks.
- **Subprocess isolation is lightweight.** Tests run via `python -E -s -B` in a
  temp dir containing only the copied tool + test, with the environment stripped
  to `PATH` (the API key never reaches synthesized code), a 30s wall-clock
  timeout, and `.pyc` writes off. We use `-E -s` rather than `-I` deliberately:
  `-I` implies `-P`, which would remove the temp dir from `sys.path` and break
  the test's `import <tool>` of its sibling file.
- **Network is intentionally ON.** The demo domain is web tasks; tests hit real
  endpoints. That's a feature here, but it means synthesized code can make
  arbitrary outbound requests.

## A note on tool data flow

Tool outputs flow back through the agent's context and are re-passed as the next
tool's input. Round-tripping large raw blobs (e.g. a full HTML page) through the
model is unreliable, so the system prompt steers the agent to design tools that
**return compact, structured data** (parsed records, counts), and any tool
result fed back is capped at ~12K chars. The synthesis prompts stay domain-
agnostic — the agent figures out the parsing; that's the point.

## Layout

```
forge/
  llm.py          # Anthropic client wrapper + cost meter
  registry.py     # tool registry (manifest.json is the source of truth)
  sandbox.py      # AST gate + isolated verified execution
  synthesis.py    # author tool + test, verify, revise-on-failure loop
  loop.py         # convergence-gated control loop + agent state
  events.py       # event bus + JSONL log
  tui.py          # Rich 3-panel live/replay UI
  tools/          # promoted tools live here (manifest.json seed is tracked)
main.py           # CLI entry
scripts/stats.py  # run-file metrics aggregator
demo/             # task.txt, task2.txt, SCRIPT.md (90s beat sheet)
```

Built phase-by-phase via `/parallel-change`.
