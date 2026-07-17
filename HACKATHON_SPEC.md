# Forge: Zero-Trust Self-Extending Loop — Hackathon Spec

**Event:** Loop Engineering Hackathon (build self-directing agent loops: plan → act → observe → self-correct)
**Base:** Forge (this repo) — a convergence-gated agent loop that synthesizes its own tools, adversarially tests them, and only promotes them on a sandbox pass.
**Sponsor tools targeted:** Zero.xyz, Pomerium, Akash (Nexla as stretch).
**Pitch in one line:** *An agent loop that self-corrects its own toolbox (Zero.xyz + synthesis), earns its own privileges (Pomerium trust ratchet), and provisions its own compute (Akash) — nothing is granted, everything is verified.*

---

## How to execute this spec

- Phases are **strictly sequential**: a phase's **Gate** must pass before the next phase starts.
- Tasks **within** a phase are **independent by design** — no shared files between tasks in the same phase unless noted. Run them in parallel (subagents / separate worktrees), one task per agent.
- Every task has an **Acceptance** check. A task is not done until its check passes.
- Do not refactor Forge internals beyond what a task requires. The existing loop is the product; integrations must feel native, not bolted on.

### Existing architecture (read before starting)

| Module | Role |
|---|---|
| `forge/loop.py` | Convergence-gated control loop; dispatches `update_plan` / promoted tools / `request_tool` / `final_answer` |
| `forge/synthesis.py` | `author_tool` (builder LLM) + `author_test` (adversarial black-box tester LLM) + revise cycle |
| `forge/sandbox.py` | AST gate → isolated subprocess; pass/fail is the promotion gate |
| `forge/registry.py` | `manifest.json`, tool states: draft/testing/failed/promoted |
| `forge/events.py` | Every transition → `runs/<ts>.jsonl` — single observability source |
| `forge/tui.py` | Live TUI over the event stream |

**Core invariant (do not break):** no tool enters the registry without passing its own independently-authored test in the sandbox.

---

## Phase 0 — Recon & access (all tasks parallel)

Goal: confirm every sponsor tool is actually usable before writing a line of integration code. Any tool that fails recon gets cut from scope *now*, not at hour 6.

### Task 0.1 — Zero.xyz recon
- Install the Zero.xyz CLI. Authenticate.
- From the terminal, discover and execute one real external tool/API call through Zero (any read-only service; note whether x402 micropayment fires).
- Write findings to `docs/recon/zero.md`: exact CLI invocation shape, output format (JSON? text?), latency, auth model, one working end-to-end command.
- **Acceptance:** `docs/recon/zero.md` contains a copy-pasteable command that was actually run and its real output.

### Task 0.2 — Pomerium recon
- Run Pomerium locally (Docker is fine, `pomerium/pomerium` image; all-in-one mode).
- Stand up a trivial upstream (e.g., `python -m http.server`) and gate it behind a Pomerium route with a policy.
- Verify: request through Pomerium is allowed by policy A, denied after switching to policy B. Confirm **how policy reload works** (file watch? SIGHUP? restart?) — this is load-bearing for the trust ratchet.
- Write findings + the working `pomerium-config.yaml` to `docs/recon/pomerium.md`.
- **Acceptance:** documented allow→deny flip with the reload mechanism named.

### Task 0.3 — Akash recon
- Install Akash CLI (or use Akash Console), fund wallet with testnet/faucet or sponsor-provided credits.
- Deploy the smallest possible container (e.g., `hello-world` or an nginx) via an SDL file. Tear it down.
- Write findings to `docs/recon/akash.md`: SDL that worked, deploy + logs + teardown commands, wall-clock time to live deployment.
- **Acceptance:** a deployment reached state `active` at least once; commands documented.

### Task 0.4 — Demo task selection (no external deps)
- Author 2 candidate demo tasks in `demo/hackathon/` as `.md` task files (same format as `demo/task.md`). Requirements for a good demo task: (a) forces ≥2 tool syntheses, (b) forces ≥1 capability gap that is *better bought than built* (an external API — that's the Zero.xyz beat), (c) touches real-time data (that's the autonomy criterion), (d) completes in < 90 seconds of runtime.
- Candidate direction: "monitor X live feed, detect condition, act on it" — live data + self-correction on camera.
- **Acceptance:** both task files exist and each names which sponsor beat it exercises at which step.

### GATE 0
All four tasks accepted. **Decision recorded at top of `docs/recon/DECISIONS.md`:** which of Zero/Pomerium/Akash are confirmed in-scope. If any tool failed recon, note the cut and its fallback (see Risk table at bottom) before proceeding.

---

## Phase 1 — Core integrations (three parallel tracks, zero file overlap)

Each track owns its files exclusively. No track edits `loop.py` in this phase — loop wiring is Phase 2, precisely so these can run in parallel.

### Track A — Zero.xyz: the "buy vs build" path (`forge/acquire.py`, new)
The story: when the loop hits a capability gap, synthesis is *building* a tool. Zero.xyz is *acquiring* one. Self-correction should include correcting the make-or-buy decision.
- New module `forge/acquire.py`:
  - `discover(gap_description: str) -> list[Candidate]` — query Zero.xyz for tools matching the capability gap (shell out to the CLI per the recon doc; parse output).
  - `wrap(candidate) -> tool_source: str` — generate a thin Python wrapper function whose body invokes the Zero.xyz tool, with a normal Forge tool signature. The wrapper is *ordinary tool source*: it goes through `synthesis`'s existing test-author + sandbox gate like any synthesized tool. **Acquired tools earn promotion the same way built tools do — that's the narrative.**
- Emit new event types via `events.py`: `acquire_search`, `acquire_candidate`, `acquire_wrapped` (follow existing event conventions in `events.py`).
- Standalone harness `scripts/test_acquire.py`: given a hardcoded gap string, runs discover → wrap → sandbox-test → prints promoted/failed.
- **Acceptance:** `uv run python scripts/test_acquire.py` promotes at least one Zero-acquired tool into a scratch registry, with events emitted.

### Track B — Pomerium: the trust ratchet (`forge/trust.py` + `deploy/pomerium/`, new)
The story: the loop starts with minimal privileges and *earns* access by demonstrated correctness. Verified promotions ratchet trust up; sandbox failures ratchet it down. Privilege escalation is earned, never granted.
- New module `forge/trust.py`:
  - `TrustLedger` — consumes the run's event stream (tail `runs/<ts>.jsonl` or subscribe in-process) and maintains a tier: `tier0` (LLM API only) → `tier1` (read-only external access) → `tier2` (write access). Ratchet rules (tune later): +1 tier after N tool promotions with zero sandbox failures in between; drop a tier on a sandbox failure or AST-gate rejection.
  - `render_policy(tier) -> str` — emits a Pomerium policy/config for that tier from templates in `deploy/pomerium/templates/`.
  - `apply(tier)` — writes the config and triggers reload via the mechanism found in recon (0.2).
- All outbound non-LLM HTTP from tools must be routable through Pomerium (env-based proxy or per-route upstreams — pick whichever the recon showed works; document choice in `docs/recon/DECISIONS.md`).
- Standalone harness `scripts/test_trust.py`: replays an existing JSONL run file from `runs/`, prints tier transitions, applies each policy, and proves with a live request that a tier0→tier1 transition flips a real request from denied to allowed.
- **Acceptance:** the replay demo shows at least one denied→allowed flip driven purely by events, no manual step.

### Track C — Akash: the substrate (`deploy/akash/`, new)
The story: the loop doesn't just run somewhere — its compute is provisioned on an open marketplace, and headless runs stream their event log back.
- `deploy/akash/Dockerfile`: containerize Forge headless (`main.py` run with a task file arg, no TUI). Keep the image small; `uv` inside the container.
- `deploy/akash/deploy.sdl.yaml`: SDL per recon findings. CPU-only is fine.
- `scripts/akash_run.sh`: build → push image → create deployment → wait for active → run task → fetch `runs/*.jsonl` back (via provider logs or a trivial HTTP endpoint in the container — simplest thing that works).
- Local fallback path: the same image must run via `docker run` so the demo never blocks on marketplace bidding.
- **Acceptance:** one full Forge run executed inside the container **on Akash**, JSONL retrieved locally, and `scripts/stats.py` parses it.

### GATE 1
All three tracks' acceptance checks pass **independently** (each proven by its standalone harness, no cross-track wiring). Commit each track on its own branch or as its own commit series; merge to `main` cleanly. Anything failing its acceptance gets 45 more minutes max, then invoke its fallback from the Risk table and record the decision.

---

## Phase 2 — Loop wiring (parallel where marked; this phase touches shared files)

### Task 2.1 — `request_tool` becomes buy-or-build *(owns `forge/loop.py` + `forge/synthesis.py`)*
- On `request_tool`, before synthesis: call `acquire.discover()`. If a viable Zero.xyz candidate exists, wrap it and send it through the existing test/sandbox/promote pipeline; on wrap failure or no candidate, fall through to synthesis exactly as today. Emit a `make_or_buy` event recording the decision and why.
- Convergence semantics unchanged: an acquisition is a toolbox change → not a stable turn.
- **Acceptance:** one demo task run where the transcript shows *both* paths taken — at least one tool acquired via Zero, at least one synthesized — all promoted through the same gate.

### Task 2.2 — Trust ratchet live in the loop *(owns `forge/trust.py` integration point + `main.py`)*
- Instantiate `TrustLedger` at run start (tier0); feed it events in-process as they're emitted; policy applied on every tier change. Emit `trust_tier_changed` events.
- The demo task must be *designed* to need tier1: an early step's tool call fails with a Pomerium denial, the loop observes the failure, keeps working on verifiable steps, earns promotion(s), tier flips, retries, succeeds. **That denied→earned→retried arc is the single best 30 seconds of the demo — protect it.**
- **Acceptance:** headless run of the demo task shows in JSONL: denial event → ≥1 promotion → `trust_tier_changed` → successful retry, zero manual intervention.

### Task 2.3 — TUI: make the invisible visible *(owns `forge/tui.py` — no overlap with 2.1/2.2, parallel-safe)*
- Add to the TUI: current trust tier (with tier-change flash), per-tool badge `[built]` vs `[acquired]`, and a make-or-buy decision line when it happens. Judges score what they can see.
- **Acceptance:** TUI renders all three new elements during a live run of the demo task.

*(2.3 can run parallel with 2.1 and 2.2. Run 2.1 and 2.2 sequentially OR in separate worktrees merged carefully — both touch the loop's event flow.)*

### GATE 2
One **end-to-end run on Akash**: demo task in, headless, produces the full arc (make-or-buy decision, synthesis, denial, ratchet, retry, convergence halt) in a single JSONL with no human input after launch. This JSONL is the proof artifact — save it as `demo/hackathon/golden_run.jsonl` and commit it.

---

## Phase 3 — Demo & submission (all tasks parallel)

### Task 3.1 — Demo script
- `demo/hackathon/DEMO.md`: a 3-minute script, timed to the golden run. Structure: 20s problem framing ("agents are either over-privileged or useless"), 30s architecture slide/diagram, 100s live run (TUI on screen — the denial→ratchet→retry arc is the centerpiece), 30s close on the invariant ("nothing is granted; everything is verified — tools, privileges, even the compute is bid for").
- Include a fallback: if the live run misbehaves, replay `golden_run.jsonl` through the TUI. Build that replay flag if `tui.py` doesn't have one (small, allowed).
- **Acceptance:** one full timed rehearsal ≤ 3:00.

### Task 3.2 — Devpost writeup
- `docs/DEVPOST.md`: name, tagline, what it does, how each sponsor tool is used **at which step of the loop** (map to plan/act/observe/self-correct explicitly — the judging rubric words), what's next. Screenshots of TUI states.
- **Acceptance:** every rubric criterion (Idea / Technical / Tool Use / Presentation / Autonomy) has a sentence that speaks to it directly.

### Task 3.3 — README + repo hygiene
- Update `README.md` with a "Hackathon build" section linking the spec, the recon docs, and one-command reproduction (`scripts/akash_run.sh` and the local `docker run` fallback).
- `uv run python scripts/stats.py` on the golden run; put the numbers (turns, tools built vs acquired, tier transitions, time to converge) in the README — judges love a stats table.
- **Acceptance:** fresh-clone reader can understand and reproduce the demo locally without asking questions.

### GATE 3 (final)
Timed rehearsal passed, Devpost submitted, `main` green.

---

## Stretch (only after Gate 3): Nexla observe-step
If ≥2 hours remain: register the JSONL event stream as a Nexla data flow (runs → Nexla → a live dashboard or alerting flow), making Nexla the *observe* leg of the loop across runs. New files only (`forge/nexla_sink.py`, `docs/recon/nexla.md`); must not touch the demo path. This adds a fourth prize category at zero risk to the core demo.

## Risk table / fallbacks

| Risk | Signal | Fallback |
|---|---|---|
| Zero.xyz CLI unusable / no relevant tools | Recon 0.1 fails | Drop Track A; pivot Phase 2.1 to pure synthesis and put the extra time into the Nexla stretch (keeps 3 sponsors: Pomerium, Akash, Nexla) |
| Pomerium reload too slow/awkward for live ratchet | Recon 0.2 | Restart Pomerium container on tier change — a 2s restart is fine at demo scale |
| Akash bidding slow or wallet issues | 0.3 or Track C stalls | Demo runs in the same container locally via `docker run`; show the Akash deployment reaching `active` as a pre-recorded 15s clip; keep the SDL in the repo |
| Demo task flaky (LLM nondeterminism) | Gate 2 reruns disagree | Golden-run JSONL replay through the TUI is the demo of record; live run is the encore |
| Time collapse | Behind at Gate 1 | Cut Track C to local Docker only; Pomerium ratchet + Zero acquisition alone still cover 2 prize categories + the general prize narrative |
