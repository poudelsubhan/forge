# Forge v2 — generality + an interactive shell

Three workstreams, independent and shippable separately:

- **A. Generality** — stop the agent minting near-duplicate tools; make it
  reuse and *generalize* what it already has, **at the right altitude**. (Core ask.)
- **B. Interactive shell** — a Claude-Code-style REPL beside the TUI: vertical
  split, live panels on the left, a chat/prompt loop with visible thinking on
  the right.
- **C. Capability surface** — widen and *negotiate* what synthesized tools may
  do (scoped file I/O, a tiered import policy) so an audience can ask for almost
  anything live, instead of being steered toward fetch-and-compute.

The doc is organized by Bloom's taxonomy — *remember → understand → apply →
analyze → evaluate → create* — applied to each workstream, so the reasoning and
the build steps are traceable, not just asserted.

---

## Workstream A — Generality (reuse over duplication)

### Remember — the observed facts

Run 1 (`demo/task.txt`, HN page 1) synthesized:

```
fetch_hn_top_stories() -> list[dict]          # URL "https://news.ycombinator.com/" hardcoded
```

Run 2 (`demo/task2.txt`, HN page 2, run with `--keep`) synthesized a **second**
tool instead of reusing the first:

```
fetch_hn_page2_top_stories() -> list[dict]    # URL ".../news?p=2" hardcoded
```

`diff` of the two files: they are the *same tool*. Same return contract
(`title/points/domain`, top 15), same parsing strategy. The **only** semantic
difference is one string literal — the URL. The manifest now carries two
promoted tools that overlap ~95%.

### Understand — why this is the wrong outcome

A self-extending agent whose toolbox grows one entry per *instance* (page 1, page
2, page 3…) instead of one entry per *capability* (fetch an HN listing) defeats
its own premise. The reuse-ratio metric collapses, the toolbox bloats, and every
new instance re-pays the full synthesis + verification cost the gate was supposed
to amortize. Generality is what makes "the gate paid off once; reuse is free
after" actually true.

### Analyze — root cause (three distinct failures)

1. **Builder bakes specifics into constants.** `author_tool` is told to *"match
   the proposed signature"* and given a purpose full of literals ("the front
   page", a specific URL). It dutifully hardcodes them. Nothing in
   `_AUTHOR_TOOL_SYSTEM` rewards lifting task-specific values into parameters.
   (`forge/synthesis.py:98`)

2. **Agent requests by instance, not capability.** `request_tool`'s description
   and `SYSTEM_PROMPT` say *"prefer promoted tools"* and *"don't request a tool
   that already exists"* — but "exists" is interpreted as *exact name match*. The
   agent has no instruction to ask: *"could an existing tool do this if I called
   it with different arguments, or generalized it slightly?"* So a new instance
   reads as a new gap. (`forge/loop.py:88`, `:126`)

3. **No near-duplicate guard at the seam.** `synthesize()` only short-circuits on
   `registry.has_promoted(spec.name)` — an exact name hit. A request for
   `fetch_hn_page2_top_stories` sails straight past the page-1 tool.
   (`forge/loop.py:269`)

### Apply — the fixes (in priority order)

**A1 — Builder prompt: author at the right altitude (senior-engineer judgment).**
Lifting constants into parameters is necessary but *not sufficient* — done
blindly it produces a `scrape_anything(url, selector_config)` god-tool that is
general in signature and useless in practice. The builder needs the *judgment*
about where to draw the abstraction boundary. Add to `_AUTHOR_TOOL_SYSTEM`:

> - **Parameterize an axis only where variation is plausible for the SAME
>   underlying logic.** A page number or section of the *same site* → a parameter
>   (`page: int = 1`), because one parser handles them. Do NOT widen an axis that
>   would change the logic itself.
> - **Separate what is stable from what varies.** Fetching (HTTP GET, redirects,
>   decode, retry) is the same everywhere → factor it into a small reusable
>   primitive. Parsing is *site-specific* → keep it in its own tool, scoped to one
>   site's markup. Prefer `general primitive + site adapter` over one
>   over-parameterized function.
> - **State the domain of validity.** The docstring must say exactly what input
>   the tool is valid for (e.g. "parses Hacker News *listing* markup"). A tool
>   must never silently claim to handle inputs it was not built for.
> - **Name for the capability, not the instance** (`fetch_hn_listing`, not
>   `fetch_hn_page2`) — but capability is scoped by domain of validity, so
>   `parse_hn_listing` and `parse_reddit_listing` are correctly *two* tools.

> **The litmus test (rule of three / YAGNI vs DRY):** generalize when you have
> evidence of repetition along an axis (page 1 → page 2 is the same parser,
> different page). Do *not* pre-generalize across axes that change the
> implementation (HN → Reddit is a different parser). Too-specific bloats the
> toolbox; too-general produces brittle, lying tools. The win is the *middle*.

**Web scraping is the sharp case.** Different sites = different HTML = different
parse logic, so a single "scrape any site" tool is a trap: it will either be
brittle or fake structure it didn't really extract (the exact degenerate-output
bug the adversarial tester exists to catch). The correct shape is a shared
`http_get(url) -> str` substrate plus one parser per *site family*, each
parameterized only within that family. (BeautifulSoup, added via Workstream C,
makes the site-specific parsers far more robust than hand-rolled `html.parser`.)

**A2 — Agent prompt: reuse-or-generalize before requesting.** Add to
`SYSTEM_PROMPT` and to `REQUEST_TOOL_TOOL`'s description:

> - Before calling `request_tool`, check the promoted toolbox: if an existing
>   tool could satisfy this step **with different arguments**, CALL IT with those
>   arguments — do not request a new one.
> - If an existing tool is *almost* right but too narrow (it hardcodes something
>   you now need to vary), request a **generalization** of it (same capability,
>   add the parameter) rather than a parallel near-duplicate.

Also enrich the state block (`_state_summary`, `forge/loop.py:166`) to show each
tool's **full parameter list**, so the agent can see it *takes a `url`* and
realizes page 2 is just a different argument.

**A3 — Near-duplicate guard at synthesis (structural).** In the `request_tool`
branch (`forge/loop.py:267`), before synthesizing, compare the requested
capability against promoted tools. Cheapest robust version: one quick LLM call
(or even a token-overlap heuristic on purpose+signature) returning
`{duplicate_of: <name|null>, can_call_with_args: bool}`. If a near-duplicate is
found, return a tool_result that *redirects* the agent — *"`fetch_hn_listing`
already covers this; call it with `url=…`"* — instead of synthesizing. This makes
generality robust even when the prompt nudges (A1/A2) don't land.
>
> **Key the check on (operation + domain of validity), not surface text.** HN
> page 1 vs page 2 = same operation, same domain → duplicate, redirect. HN parser
> vs Reddit parser = same operation, *different* domain → NOT a duplicate, allow
> synthesis. The guard must distinguish "same job, different argument" from "same
> verb, different site," or it will wrongly block legitimate new tools.

**A4 — `generalize_tool` path + regression gate (the strong version, great demo
beat).** Add a fourth synthesis mode: re-author an *existing* promoted tool to
accept a new parameter, then re-verify against **both** the original test **and**
a new test for the added case. Promote the generalized tool only if **both**
pass — i.e. adding page-2 support must not break page-1. This is the cleanest fix
*and* a compelling beat: *"it refactored its own tool to be more general and
re-proved the old contract still holds."* Requires: keep historical tests per
tool; run the test suite (not a single test) in `sandbox.run_test`.

### Evaluate — how we know it worked

- **Duplication rate** → 0 on the page-1/page-2 sequence: running `task2.txt`
  after `task.txt` with `--keep` must emit **zero `gap_detected`/synthesis**
  events (pure reuse), or exactly one `generalize_tool` event (A4), never a new
  parallel tool.
- Add a stat to `scripts/stats.py`: **distinct capabilities ÷ promoted tools**
  (1.0 = no redundancy), where a capability is `(operation, domain-of-validity)`
  — so an HN parser and a Reddit parser both count as legitimate distinct tools,
  while HN-page-1 and HN-page-2 collapse to one. The metric must reward the right
  altitude, not raw tool count.
- **Over-generalization guard:** flag any promoted tool whose signature has a
  free-form "config"/"selector"/"rules" parameter doing the parser's real work —
  that's the god-tool failure, the opposite error from duplication.
- Regression: the A4 path must show the original test still passing after
  generalization (visible in the event log).

---

## Workstream B — Interactive Claude-Code-style shell

### Verdict on "is that too hard?"

**No — it's very doable, and the architecture is already most of the way there.**
The loop runs on a background thread and the TUI is reconstructed purely from the
event bus (`tui.run_live` polls a deque). Decoupling input from render is already
done; what's missing is (1) a persistent multi-turn session and (2) an input
pane. Recommendation: **build it in [Textual](https://textual.textualize.io)**
(same authors as Rich, so the existing `rich` renderables port over) rather than
hand-rolling input handling under `rich.Live`.

### Understand — the gap between now and the goal

| Need | Today | Change |
|------|-------|--------|
| Keep prompting (multi-turn) | `loop.run()` is one task, then exits | a **session** that preserves `messages` + `registry` across prompts |
| Input pane | none (TUI is read-only) | Textual `Input` widget feeding new tasks |
| Vertical split | 3 stacked panels | Textual layout: left = panels, right = chat + input |
| Visible *thinking* | only `agent_message` text events | stream model text/thinking blocks as they arrive |

### Apply — target architecture

```
┌─────────────────────────────┬──────────────────────────────┐
│  LEFT  (existing TUI)        │  RIGHT  (new chat pane)       │
│  ┌───────────────────────┐  │  ┌────────────────────────┐  │
│  │ Toolbox (status colors)│  │  │ chat log:              │  │
│  ├───────────────────────┤  │  │  user prompts          │  │
│  │ Plan + convergence     │  │  │  agent thinking (dim)  │  │
│  ├───────────────────────┤  │  │  agent messages        │  │
│  │ Event stream           │  │  │  tool calls/results    │  │
│  └───────────────────────┘  │  ├────────────────────────┤  │
│   (driven by event bus)     │  │ > input box ▏          │  │
└─────────────────────────────┴──┴────────────────────────┴──┘
```

1. **Session loop.** Refactor `loop.run(task, registry)` into a `Session` that
   holds `messages`, `registry`, `state` and exposes `submit(task)` running the
   turn-loop to its next halt (`final_answer`/`converged`), then *waits* for the
   next prompt instead of exiting. The toolbox persists across prompts in-process
   (and to disk, as today) — so reuse across prompts is immediate.

2. **Textual app, two regions.** A `Horizontal` container: left mounts the
   current three panels (port the `ViewModel` → Rich renderables straight in);
   right mounts a scrollable chat log + an `Input`. The event bus already feeds
   the left; add a second consumer that renders chat-relevant events
   (`agent_message`, `tool_used`, `gap_detected`, …) into the right.

3. **Visible thinking.** Switch agent turns to the **streaming** Messages API and
   (optionally) enable extended **thinking blocks**; emit `agent_thinking` /
   `agent_token` events as deltas arrive and render them dimmed in the chat pane.
   This is the "interaction where thinking is displayed, not just events" the
   demo wants. `llm.complete` already centralizes the call — add a `stream=` path
   with an on-delta callback that emits to the bus.

4. **Input drives tasks.** `Input.submitted` → `session.submit(value)` on the
   worker thread (or Textual `@work`), keeping the UI responsive exactly as the
   current `threading.Thread` + `is_done` pattern does.

### Phasing (so it's incremental, not a rewrite)

- **B1** — `Session` wrapper around the existing loop; prove multi-prompt reuse
  headless (no UI change). *This alone is valuable and low-risk.*
- **B2** — Port the current TUI panels into a Textual app (left region only),
  reach parity with `tui.run_live`/`--replay`.
- **B3** — Add the right chat pane + `Input`; wire `submit`.
- **B4** — Streaming thinking deltas.

### Evaluate

- Submit two prompts in one session (HN page 1, then page 2); the second reuses
  the toolbox with zero synthesis — same generality win as Workstream A, now
  visible interactively.
- `--replay` still works (same event stream → same renderables).
- Thinking is legible in real time without drowning the event log.

---

## Workstream C — Capability surface (expand + negotiate)

### Remember — the facts

`forge/sandbox.py` enforces a fixed AST allowlist (`httpx, json, re, html,
urllib, datetime, collections, math, csv, io, …`), **bans `open()` in any write
mode**, strips the env to `PATH`, and leaves network on. In the demo I had to
*steer the audience* toward fetch-and-compute tasks, because anything that wrote
a file, read a local file, or used a library outside the allowlist is rejected by
the gate before it runs.

### Understand — why the gap matters

"Ask the audience for anything" is the whole live-proof beat. If half of natural
asks — *"save the results as a CSV", "read this PDF and summarize it", "dedupe
this file"* — are pre-rejected, the demo feels rigged and the agent's reach looks
narrower than it is. The deeper issue is that capability is **hardcoded in one
frozenset, not negotiated per task** — so a human (me) has to do the steering the
system should do itself.

### Analyze — root cause

1. **Blanket write-ban instead of scoped I/O.** `_check_open_write` rejects all
   writes globally; there's no notion of "writes are fine *inside a jail*."
2. **Fixed import allowlist.** `ALLOWED_IMPORTS` is one frozenset; no way to opt
   a run into a heavier tier (BeautifulSoup, pypdf) without editing source.
3. **No capability negotiation.** `request_tool` can't *declare* "I need
   fs-write"; the gate just fails late with a cryptic AST reason. The agent (and
   the audience) never get a clear "that's off, here's the allowed path."

### Apply — the fixes

**C1 — Scoped working directory (the jail).** Replace the blanket write-ban with
a per-run scratch dir. Synthesized tools receive a `workdir` and may read/write
*within it*; reject absolute paths and `..` traversal (AST check + a runtime path
guard). The sandbox already runs the subprocess in a temp dir with `cwd` set —
extend that temp dir into a durable per-session workdir and relax the write ban
to within-cwd only. Unlocks "write a CSV", "cache to a file", "read an input."

**C2 — Tiered import policy.** Turn `ALLOWED_IMPORTS` into a policy with tiers:
`stdlib-safe` (always on), `web` (`httpx`, **`beautifulsoup4`**, `lxml`,
`markdownify`), `files` (`pypdf`, `openpyxl`), etc. A run (or a capability
request) opts into a tier; the deps must actually be installed in `.venv`.
**BeautifulSoup is the highest-value add** — robust site-specific parsing, which
directly strengthens Workstream A's per-site parsers.

**C3 — Capability negotiation (the actual patch for the steering gap).** Extend
`request_tool` to carry a declared `capabilities` list (`network`, `fs_read`,
`fs_write`, `libs: [...]`). The harness validates it against an explicit policy
*before* authoring: grant within policy; otherwise return a legible refusal
("file writes are confined to the workdir; absolute paths are off") plus the
closest allowed approach. Now the **system** tells the agent what's possible — no
human pre-steering. This is the piece that closes the gap you flagged.

**C4 — Audience inputs.** Let an audience member drop a file (or paste data) into
the workdir; synthesized tools can read it. Turns "ask for a prompt" into "ask
for a task over *your own* data" — a much stronger live proof.

> **Honesty / scope.** Every capability added widens the accident + attack
> surface. The cwd-jail + AST gate stays **hackathon-grade, not a security
> boundary** — a determined adversary defeats static analysis. The real version
> is OS-level confinement (container or `seccomp`/Landlock, read-only rootfs,
> network off by default per tier). State this plainly; do not let "we added file
> I/O" read as "we sandboxed it safely."

### Evaluate

- An audience can ask "fetch X, save a CSV, then summarize the CSV" and it runs
  end-to-end live — **no manual steering**.
- A forbidden capability produces a **legible refusal in the chat pane** (not a
  cryptic AST error); the negotiation is visible.
- The verification gate still bites: a file-writing tool is tested *in the jail*,
  and a tool that tries to escape the workdir fails the gate.

---

## Suggested order

1. **A1 + A2** (prompt-only, hours, immediate demo improvement — now includes the
   altitude/site-format judgment, not just "use params").
2. **C1 + C2** (scoped workdir + BeautifulSoup tier) — unblocks audience tasks
   *and* makes A's per-site parsers robust. High leverage, do early.
3. **A3** (domain-aware near-duplicate guard — makes generality robust).
4. **C3** (capability negotiation — removes the need to steer the audience).
5. **B1 → B3** (interactive shell; B1 is independently shippable).
6. **A4**, **B4**, **C4** (showpiece versions — self-generalizing tools, live
   streamed thinking, bring-your-own-data) if time allows.
