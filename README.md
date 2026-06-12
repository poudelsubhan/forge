# Forge — a self-extending agent harness

An agent that, on hitting a capability gap, **authors a new tool plus a test for
that tool**, runs the test in a sandbox, and only promotes the tool into its
registry on a pass. The control loop is convergence-gated: a run halts when the
plan + toolbox survive a full pass unchanged, not when a turn cap is hit.

> **Core invariant:** no tool enters the registry without passing its own test
> in the sandbox. The verification gate is the product.

This README is filled out in Phase 4. See
`forge-self-extending-harness.md` (the implementation plan) for the full design.

## Status

- [x] Phase 1 — Foundation (scaffold, llm wrapper, registry, sandbox, events)
- [ ] Phase 2 — Synthesis pipeline
- [ ] Phase 3 — Convergence-gated control loop
- [ ] Phase 4 — TUI + demo hardening

## Quickstart (Phase 1)

```bash
cp .env.example .env   # add your ANTHROPIC_API_KEY
uv run main.py
```
