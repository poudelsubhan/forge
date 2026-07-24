# Forge × Codex × Zendesk — Self-Extending Support Agent — Implementation Plan

> **Purpose**: Instruction doc for Codex. Each phase is a HARD gate — all tasks
> in Phase N must complete before ANY task in Phase N+1 begins. Tasks within a
> phase run in parallel unless noted.
>
> **Base**: existing Forge repo (Python 3.12, `uv`, `rich`, `httpx`, `pydantic`).
> Forge's core stays intact: tool registry backed by `manifest.json`, sandboxed
> test execution, verified-synthesis gate (no tool enters the registry without
> passing its own test), JSONL event log, replay mode, convergence-gated loop.
> This plan swaps the model backend to OpenAI, delegates synthesis to Codex,
> and re-points the task domain at Zendesk tickets.

---

## Context & Key Findings

- **Concept**: A Zendesk support agent that, on hitting a capability gap while
  working a ticket, delegates to Codex to author a new tool *plus a test for
  that tool*, runs the test in a sandbox, and only promotes the tool on a pass.
  Failed tools are revised by Codex using verbatim sandbox stderr.
- **Core invariant (unchanged)**: no tool enters the registry without passing
  its own test in the sandbox. The verification gate is the product.
- **Two model seams**:
  1. **Agent loop** → OpenAI Responses API (latest general GPT-5.x model) via
     the `openai` SDK. Handles ticket triage, planning, tool selection, replies.
  2. **Synthesis + revision** → `codex exec` (headless Codex CLI), file-based
     contract (Codex writes `tool.py` + `test_tool.py` into a scratch
     workspace; the harness reads files back — never parse prose stdout).
- **Demo arc (90s)**: Ticket #1 asks for a refund; policy lives in a fixture
  doc and order data lives behind a mock internal API the agent has no tool
  for → agent requests a tool → Codex synthesizes `lookup_order` → tool fails
  its own test on screen → Codex revises off verbatim stderr → passes,
  promoted → agent resolves ticket, posts reply to Zendesk. Ticket #2 (another
  refund) is resolved instantly because the tool already exists. Cut to the
  Zendesk UI showing both replies.
- **Pitch one-liner (for the presentation submission form)**: "A Zendesk
  support agent that forges its own tools with Codex — and no tool ships
  without passing its own test."

---

## Phase 0 — Manual pre-event checklist (human, not Codex)

- [ ] Zendesk trial/sandbox account created; note subdomain. Generate API
      token (Admin Center → Apps and integrations → APIs). Auth =
      `{email}/token:{api_token}` basic auth.
- [ ] `.env`: `OPENAI_API_KEY`, `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`,
      `ZENDESK_API_TOKEN`.
- [ ] Codex CLI installed and authenticated; confirm `codex exec` runs.
- [ ] Confirm event rules on pre-existing code; disclose Forge in the pitch.

---

## Phase 1 — Backend swap + domain scaffolding (all tasks parallel)

### 1A. OpenAI loop backend
- Locate Forge's LLM client module (the one wrapping the Anthropic SDK) and
  the schema converter (`to_anthropic_tools()` or equivalent).
- Replace with an `openai` SDK client using the Responses API. Add
  `to_openai_tools()` deriving JSON schemas from `inspect.signature` + type
  hints — same rule as before: never let the LLM write its own schema.
- Keep the interface identical so the loop, TUI, and event log are untouched.
- Model set via env/CLI flag, default to the latest general GPT-5.x model.

### 1B. Codex synthesis adapter (`codex_adapter.py`)
- First step: run `codex exec --help` and confirm exact flags for
  non-interactive mode, working directory, sandbox level, and session resume.
  Do not guess flags — verify, then hardcode what's confirmed.
- `synthesize(gap_spec, workspace_dir) -> ToolCandidate`:
  - Create `workspace/{tool_name}/` containing `SPEC.md` (gap description,
    required signature, available context, constraints).
  - Invoke `codex exec` with cwd = that dir, workspace-write sandbox, prompt:
    "Read SPEC.md. Write tool.py (single function, typed signature, docstring)
    and test_tool.py (pytest, self-contained, no network unless SPEC allows a
    localhost URL). Write nothing else."
  - Read `tool.py` / `test_tool.py` back from disk. Fail loudly if missing.
- `revise(workspace_dir, stderr_text) -> ToolCandidate`:
  - Append verbatim stderr to `FAILURE.md` in the same dir; resume the same
    Codex session (or re-invoke with full dir context) with prompt: "The test
    failed. Read FAILURE.md. Fix tool.py and/or test_tool.py."
  - **Tool authorship and test authorship remain separable acts**: SPEC.md
    instructs Codex to write the test against the SPEC's contract, not against
    the implementation. If synthesized tests mirror bugs, split into two
    `codex exec` invocations (tool first, then test with tool.py hidden).
- Emit JSONL events: `synthesis_requested`, `synthesis_complete`,
  `revision_requested`, with tool name + workspace path.

### 1C. Zendesk client (`zendesk_client.py`)
- Thin `httpx` wrapper, basic auth from env. Endpoints:
  - `list_open_tickets()` → GET `/api/v2/tickets.json` filtered to open
  - `get_ticket(id)` → GET `/api/v2/tickets/{id}.json`
  - `add_reply(id, body, public=True)` → PUT `/api/v2/tickets/{id}.json`
    with a comment payload
  - `solve_ticket(id)` → status update in same PUT
- Retry/backoff on 429. No pagination cleverness — demo has <10 tickets.

### 1D. Mock internal order API (`mock_order_api.py`)
- Tiny stdlib/`uvicorn`-free HTTP server (e.g. `http.server` handler or a
  20-line `asyncio` server) on `localhost:8377` serving
  `GET /orders/{order_id}` → JSON `{order_id, customer_email, amount,
  purchased_at, item}` from a hardcoded dict. Intentionally quirky response
  shape (e.g. amount in cents as a string) so the synthesized tool has real
  parsing work and a plausible first-attempt failure.
- The agent is told this API exists (base URL in the ticket context) but has
  **no tool for it** — this is the capability gap that triggers synthesis.

### 1E. Demo fixtures + seed script (`seed_demo.py`)
- `fixtures/refund_policy.md`: refund policy (30-day window, item categories,
  partial-refund rules) — enough logic that the agent must actually apply it.
- `seed_demo.py`: creates via the Zendesk API:
  - Ticket #1: refund request with order ID + purchase framing that requires
    both the policy and the order API.
  - Ticket #2: second refund request, different order ID (the reuse beat).
  - One decoy ticket answerable with no tools (shows triage, not scripted).
- Idempotent: tag seeded tickets, delete-and-recreate on rerun.

---

## Phase 2 — Wiring the loop (sequential within phase: 2A → 2B)

### 2A. Gap detection → Codex synthesis → verification gate
- Keep Forge's `request_tool` builtin as the explicit gap-detection act.
  On invocation, route to `codex_adapter.synthesize`.
- Run `test_tool.py` in Forge's existing sandbox (subprocess, pytest,
  **API keys and Zendesk credentials stripped from sandbox env**; only the
  mock API's localhost URL is reachable).
- On failure: capture verbatim stderr → `codex_adapter.revise` → retest.
  Max 3 revisions, then surface failure to the loop.
- On pass: promote into `manifest.json` registry exactly as Forge does today;
  `to_openai_tools()` picks it up on the next turn automatically.

### 2B. Zendesk task driver
- Replace Forge's generic task source with a ticket-driven one: pull open
  seeded tickets, present each as a task with context = ticket body +
  requester + pointer to `fixtures/refund_policy.md` + mock API base URL.
- Terminal action per ticket: draft reply → `add_reply` + `solve_ticket`.
- Convergence rule per session: halt when all seeded tickets are solved and
  the toolbox survived a full pass unchanged (Forge's existing stopping rule,
  re-scoped to the ticket queue).

---

## Phase 3 — End-to-end demo arc (single task)

- Run the full arc against the live Zendesk sandbox: seed → run → ticket #1
  triggers synthesis → fail → revise → pass → promote → reply posted →
  ticket #2 reuses tool → decoy handled → converge/halt.
- Persist the toolbox by default (`--keep` semantics) — the run-2 reuse beat
  dies if every run starts fresh. Add `--fresh` to wipe registry + workspaces
  for the on-stage run.
- Verify JSONL event log captures the whole arc (this feeds replay).
- Fix whatever breaks. Nothing in Phase 4 starts until this arc completes
  clean twice in a row.

---

## Phase 4 — Presentation layer (all tasks parallel)

### 4A. TUI update
- Extend Forge's Rich TUI: swap/add a **Tickets** panel (id, subject, status
  live from Zendesk) alongside the existing Toolbox and Event panels. The
  synthesis fail→revise→pass sequence must be visually unmissable (color
  states on the tool row: SYNTHESIZING / TEST FAILED / REVISING / PROMOTED).

### 4B. Replay mode
- Confirm Forge's replay mode works off the new event types — replay of a
  recorded good run is live-demo insurance if wifi or the API dies on stage.

### 4C. Demo script + submission
- `DEMO.md`: exact 90-second beat sheet, commands, what to say over each beat,
  and the fallback command (`--replay path/to/good_run.jsonl`).
- Include the presentation-form one-liner and "APIs used: OpenAI (Responses
  API + Codex), Zendesk Tickets API."

### 4D. README
- Short: what it is, the invariant, architecture diagram (ASCII), how the
  triad maps (gap detection = *determine*, Codex authorship = *claim*,
  sandbox verification + promotion = *establish*), setup, disclosure that
  Forge's harness predates the event and the Codex/Zendesk integration was
  built at the event.

---

## Dependency Graph

```
Phase 0 (human)   Phase 1 (parallel)      Phase 2            Phase 3         Phase 4 (parallel)
                  ┌─ 1A openai loop ─┐
  accounts/env ─▶ ├─ 1B codex adapter┤   2A gate wiring ─┐                   ┌─ 4A TUI
                  ├─ 1C zendesk client┼─▶                ├─▶ 3 e2e arc ────▶ ├─ 4B replay
                  ├─ 1D mock API ────┤   2B ticket driver┘                   ├─ 4C demo script
                  └─ 1E fixtures/seed┘                                       └─ 4D README
```

---

## Time allocation (event runs 5:00–7:00 PM build window)

- **Pre-event (at home)**: Phases 0–2 complete, Phase 3 passing at least once.
- **On-site**: rerun Phase 3 on venue wifi, Phase 4 polish, record the replay
  run, submit the presentation form **early** (closes 15 min before build end).
- If pre-building is disallowed by rules: Phase 1A/1C/1D/1E are each small
  enough to synthesize on-site with Codex itself; cut 4A polish first, never
  the revise-on-failure loop.

---

## Notes for Codex

- The verification gate is the product. Under time pressure, cut TUI polish,
  never the revise-on-failure loop.
- File-based contract with `codex exec`: read `tool.py`/`test_tool.py` from
  disk. Never parse tool code out of Codex's prose output.
- Revision input must be **verbatim** sandbox stderr. Summarized errors
  produce worse fixes.
- Strip all credentials from the sandbox env. Synthesized code must never see
  `OPENAI_API_KEY` or Zendesk tokens.
- `manifest.json` stays the single source of truth for tool state; TUI and
  `to_openai_tools()` both read from it. No in-memory drift.
- Derive tool schemas from `inspect.signature` + type hints, never from
  LLM-written schemas.
- The synthesis prompt must not hardcode the mock API's response quirks — the
  agent figuring out the parsing is the point.
- Zendesk trial accounts rate-limit aggressively; keep the seed count small
  and back off on 429.
