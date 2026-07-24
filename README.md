# Forge × Codex × Zendesk

**A Zendesk support agent that forges its own tools with Codex—and no tool
ships without passing its own test.**

When Forge encounters a capability gap while resolving a ticket, it asks Codex
to author a typed Python tool and a black-box pytest contract. Forge runs that
test in an isolated subprocess, feeds failures back to Codex verbatim for
revision, and promotes the tool only after it passes. Promoted tools persist, so
the next ticket can reuse them immediately.

> **Invariant: no tool enters the registry without passing its own test in the
> sandbox. The verification gate is the product.**

## How it works

```text
 Zendesk tickets
       |
       v
 OpenAI Responses agent loop -----> reply + solve ticket
       |
       | capability gap
       v
 request_tool
       |
       v
 Codex authors tool.py + test_tool.py
       |
       v
 AST gate -> isolated pytest
       |             |
       | fail        | pass
       v             v
 Codex revises    registry promotion
       |             |
       +--- retest   +--> reusable on later tickets

 Every transition -> JSONL event log -> live TUI / replay
```

The demo follows a **determine → claim → establish** arc:

| Stage | Forge behavior |
| --- | --- |
| **Determine** | The agent detects a missing capability and calls `request_tool`. |
| **Claim** | Codex authors the tool and its contract test in a scratch workspace. |
| **Establish** | Forge verifies it in the sandbox and promotes it only on a pass. |

`forge/tools/manifest.json` is the source of truth for tool state. OpenAI tool
schemas are derived from the promoted Python functions' signatures and type
hints, never from model-written schemas.

## Setup

Requirements: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and an installed,
authenticated Codex CLI (`codex exec`).

```bash
uv sync
cp .env.example .env
```

Fill in exactly these four credentials:

```dotenv
OPENAI_API_KEY=sk-...
ZENDESK_SUBDOMAIN=your-subdomain
ZENDESK_EMAIL=you@example.com
ZENDESK_API_TOKEN=...
```

`ZENDESK_SUBDOMAIN` is the first part of
`https://your-subdomain.zendesk.com`; `ZENDESK_EMAIL` is the agent/admin email
used to create the Zendesk API token. The optional `FORGE_MODEL` setting
overrides the default OpenAI model.

## Run the demo

Start the local order API in one terminal:

```bash
uv run python -m forge.mock_order_api
```

Seed three tagged demo tickets in Zendesk:

```bash
uv run seed_demo.py
```

Process the live Zendesk queue. `--fresh` clears the promoted-tool registry for
the full synthesis beat; omit it to demonstrate reuse across runs.

```bash
uv run main.py --zendesk --fresh
uv run main.py --zendesk
```

Run a standalone task or replay a recorded run:

```bash
uv run main.py "Summarize the titles on https://example.com"
uv run main.py --replay runs/<timestamp>.jsonl --speed 1.2
```

Runs are recorded as JSONL under `runs/`. The event stream captures gap
detection, synthesis, failed verification, revision, promotion, tool reuse,
ticket state, model usage, and halt state.

## Privacy and credential handling

- `.env` is gitignored and credentials are loaded only by the local process.
- Requests to the OpenAI Responses API explicitly set `store=False`.
- OpenAI API data is not used to train models by default.
- The synthesized-tool test subprocess receives a stripped environment, so
  `OPENAI_API_KEY` and Zendesk credentials are not exposed to generated tools.
- Credentials are intentionally excluded from prompts and logs. Run logs do
  contain ticket/application content, so treat them as sensitive and review
  them before sharing.

## Project history disclosure

The core Forge self-extending harness predates the event. The OpenAI Responses
backend, Codex synthesis workflow, Zendesk ticket integration, mock order API,
and event demo were built at the event.
