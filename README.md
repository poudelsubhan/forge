# Forge: a self extending agent harness

An agent that, on hitting a capability gap, **authors a new tool plus a test for
that tool**, runs the test in a sandbox, and only promotes the tool into its
registry on a pass. The control loop is convergence gated: a run halts when the
plan and toolbox survive a full pass unchanged (or `final_answer` fires), not
when a turn cap is hit.

> **Core invariant: no tool enters the registry without passing its own test in
> the sandbox. The verification gate is the product.**

## determine · claim · establish

The harness maps cleanly onto a determine → claim → establish arc:

| stage | what happens | in the code |
|-------|--------------|-------------|
| **determine** | detect the capability gap | the agent emits a `request_tool` call when no promoted tool covers a step |
| **claim** | author the tool, and have a **separate, adversarial agent** test its contract | `synthesis.author_tool` (builder) + `synthesis.author_test` (independent tester) |
| **establish** | verify in a sandbox; promote only on a pass | `sandbox.run_test` → `registry.promote` / `mark_failed` |

Tool and test are written by **two distinct agents** on purpose. The **tool
author** (`FORGE_MODEL`) writes and revises the tool. The **test author**
(`FORGE_TEST_MODEL,` a separate, optionally different model) writes the test
**black box**: it sees only the contract (name, signature, purpose), *not* the
tool's source, and is prompted adversarially to assume the tool is buggy and to
catch real correctness defects, including degenerate/constant outputs, not
just shape. A single call writing both, or a tester that reads the
implementation, produces tests that mirror the tool's bugs; an independent
black box adversary catches them.

## Concrete Execution

1. What happens in a normal turn

Every turn is one pass through the for turn in range(...) loop in loop.py:213. Concretely:

a. Build the toolset (loop.py:215): promoted tools from the registry (their schemas derived live from function signatures) + the 3 builtins (update_plan, request_tool, final_answer).
b. Call the model (loop.py:216) with the system prompt + conversation, forcing exactly one tool call (tool_choice: "any", parallel disabled).
c. Dispatch on which tool it picked:
  - update_plan → record the new plan; note whether it actually changed.
  - a promoted tool (the else branch, loop.py:291) → registry.dispatch() runs the actual synthesized function, result captured.
  - request_tool → run the whole synthesis pipeline (author → test → sandbox → revise → promote/fail).
  - final_answer → store answer, emit halted, return immediately.
d. Feed the result back (loop.py:319): a tool_result block plus a fresh state summary (the plan + list of promoted tools) gets appended as the next user message. That state block is re-sent every turn so the agent always sees current plan status and which tools exist.
e. Convergence bookkeeping (loop.py:301-331), described next.

## Convergence: N consecutive turns where the agent changed neither its toolbox nor its plan nor fetched anything.
A "normal" productive turn: the agent either advances its plan, calls a tool to get data, or requests a new tool.

2. The convergence loop (what it actually does)

This is the halt criterion. The loop does not stop at a turn cap by design (the cap is just a safety backstop). It stops when work has genuinely settled. The whole logic is 5 lines (loop.py:302-306):

stable = not (toolbox_changed or plan_mutated or domain_used)
stable_streak = stable_streak + 1 if stable else 0
unfinished = sum(1 for s in state.plan if s.status != "done")
threshold = max(1, unfinished)
converged = turn >= 2 and stable_streak >= threshold

What "stable" means: a turn is stable if it did none of three things: no toolbox change (synthesis), no plan mutation, no promoted-tool call. In other words, a turn where the agent changed nothing about the world.

- Synthesizing a tool (even a failed one) → toolbox_changed = True → not stable.
- An update_plan that genuinely edits the plan → plan_mutated = True → not stable.

## Architecture

```
                          task
                            │
                            ▼
        ┌──────────────────────────────────────────────┐
        │  loop.py: convergence gated control loop     │
        │                                              │
        │  serialize state ─▶ agent turn (tool use) ─┐ │
        │        ▲                                   │ │
        │        │           ┌── update_plan ────────┤ │
        │        │           ├── promoted tool ──────┤ │   ┌── halt: final_answer
        │        └───────────┼── request_tool        │ ├──▶├── halt: converged
        │                    └── final_answer ───────┘ │   └── halt: cap (25 turns)
        └───────────┬───────────────────────┬──────────┘
                    │ request_tool          │ dispatch
                    ▼                       ▼
        ┌───────────────────────┐   ┌──────────────────┐
        │ synthesis.py          │   │ registry.py      │
        │  author_tool  (LLM)   │   │  manifest.json   │  ◀── single source of truth
        │  author_test  (LLM)   │◀─▶│  promoted tools  │
        │  verify + revise×3    │   │  draft/testing/  │
        └──────────┬────────────┘   │  failed/promoted │
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
disk. Anthropic tool schemas are derived from `inspect.signature` + type hints;
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

The toolbox **persists across runs by default** (`--keep`), that persistence is
what makes the run-2 reuse beat work. `--fresh` wipes it.

### Model

Defaults to `claude-sonnet-4-6` (current Sonnet, fast and cheap enough for a
loop that makes many calls). The **tool author** uses `FORGE_MODEL`; the
**test author** is a separate agent on `FORGE_TEST_MODEL` (defaults to
`FORGE_MODEL`). Pair a Sonnet builder with a stronger Opus adversary:

```bash
FORGE_MODEL=claude-opus-4-8 uv run main.py demo/task.txt    # max synthesis quality
FORGE_MODEL=claude-sonnet-4-5 uv run main.py demo/task.txt  # the plan's original pin
FORGE_TEST_MODEL=claude-opus-4-8 uv run main.py demo/task.txt  # Sonnet builds, Opus tests
```

## The TUI

A live 3-panel `rich` layout (`tui.py`), reconstructed purely from the event
stream so the same renderer drives live runs and `--replay`:

- **Toolbox** (left): tools status-colored: draft `yellow`, testing `blue`,
  failed `red`, promoted `green,` with use and revision counts. Failed tools
  stay visible: the graveyard is part of the story.
- **Plan** (right): the current plan with per step status + a convergence
  indicator (toolbox stable ✓/✗ · plan stable ✓/✗).
- **Event stream** (bottom): a scrolling log. A verification failure renders as
  a **red panel with the actual sandbox stderr**, the caught failure is *seen*,
  not summarized away.

The loop runs on a background thread and emits to the event bus; the TUI polls
the deque. The loop never blocks on rendering.

## Observability

The JSONL log (`runs/<ts>.jsonl`) is the single source. `scripts/stats.py`
aggregates it into:

- **Verification catch rate**: fraction of drafted tools that failed ≥1
  verification before promotion. *"The gate caught X% of generated tools before
  they touched real work."*
- **Reuse ratio**: `tool_used` events ÷ syntheses. Run 2 (`--keep`) is all
  reuse, zero synthesis.
- **Convergence quality**: halted via `final_answer`/`converged` vs. `cap`.
- **Cost**: per LLM call (model, input/output tokens, dollar cost, latency,
  input hash) logged at the call site, aggregated into total and **per-tool**
  cost. The cost meter ships with the feature, not after.

Every LLM call logs `llm_call`; every external call (the sandbox subprocess,
synthesized tools' HTTP) is bounded by a timeout. A run that halts via `cap` (or
any synthesis exceeding 3 revisions) is flagged loudly, since both indicate
prompt regressions.

## Capability surface & boundary

What a synthesized tool *can* do is defined entirely by the AST allowlist in
`sandbox.py` (`ALLOWED_IMPORTS`), the one source of truth, from which the
author-facing prompt is derived so the two never drift. The capacity is
deliberately small and web-shaped:

- **Fetch from the network.** `httpx` for arbitrary outbound HTTP(S),
  `urllib.parse` for URL handling. Network is intentionally ON: the demo domain
  is web tasks, and tests hit real endpoints.
- **Parse the web.** `bs4` (BeautifulSoup) + `html` for HTML; `json` and `csv`
  for structured payloads.
- **Read & write local files.** `open()` (read *and* write) + `pathlib`, but
  **scoped to the subprocess cwd jail**: relative paths only, no absolute paths,
  no `..` traversal.
- **Transform data.** `re`, `string`, `datetime`, `collections`, `itertools`,
  `functools`, `math`, `io`, `typing`.

Everything outside that list is rejected **before the code runs**, which is also
the boundary of what the harness can build:

- **No shelling out / dynamic exec.** `os.system` / `subprocess` / `eval` /
  `exec` / `__import__` / `compile` are all rejected, as is `import os` itself.
- **No off-allowlist libraries** (`requests`, `pandas`, `numpy`, …). If a tool
  needs it, the allowlist has to grow first.
- **No file access outside the cwd jail.**

In one line: Forge builds **web/API fetchers (including authenticated ones),
HTML/JSON/CSV parsers, scoped local file read/write tools, and pure
data-transform tools**, and nothing that shells out or imports off-allowlist.

This is an **AST allowlist, not a real sandbox**: a determined adversary can
defeat static analysis; it stops *accidents and obvious misuse*, not attacks.
Isolation around it is lightweight by design:

- **Subprocess isolation.** Tests run via `python -E -s -B` in a temp dir
  containing only the copied tool + test, with the environment stripped to
  `PATH` (the API key never reaches synthesized code), a 30s wall-clock timeout,
  and `.pyc` writes off. We use `-E -s` rather than `-I` deliberately: `-I`
  implies `-P`, which would remove the temp dir from `sys.path` and break the
  test's `import <tool>` of its sibling file.

## A note on tool data flow

Tool outputs flow back through the agent's context and are re-passed as the next
tool's input. Round-tripping large raw blobs (e.g. a full HTML page) through the
model is unreliable, so the system prompt steers the agent to design tools that
**return compact, structured data** (parsed records, counts), and any tool
result fed back is capped at ~12K chars. The synthesis prompts stay domain-
agnostic, the agent figures out the parsing.

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

---

*Topics: LLM agents · self-extending agents · tool synthesis · autonomous tool creation · agent harness · code generation · test-driven verification · sandboxed execution · AST allowlist · convergence-gated control loop · Anthropic Claude API · tool use / function calling · agentic loops · AI safety gates · Python*
