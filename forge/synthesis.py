"""Tool authoring + verification pipeline (Phase 2).

The sequence here *is* the demo:

    gap_detected → author tool → author test → verify in sandbox
      → (fail) revise the tool → re-verify ...  (max 3 revisions)
      → promote on pass, or mark failed.

The tool and the test are written by **two distinct agents** (2A, 2B):

  * the **tool author** (FORGE_MODEL) writes — and later revises — the tool;
  * the **test author** (FORGE_TEST_MODEL — a separate, optionally different,
    model) writes the test **black-box**: it sees only the contract (name,
    signature, purpose), NOT the tool's source, and is prompted adversarially
    to assume the tool is buggy and to catch real correctness defects (including
    degenerate/constant outputs), not just shape.

This separation matters. A single call writing both — or a tester that reads the
implementation — produces tests that mirror the tool's bugs. An independent,
black-box adversary is far likelier to catch them.

The verification gate (2C) is the product: a tool is only promoted after its
own test passes in the sandbox. On failure the verbatim sandbox stderr is fed
back into a revision prompt — summarized errors produce worse fixes.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from forge import events, identity, llm, sandbox
from forge.registry import Registry

MAX_REVISIONS = 3
EVENT_STDERR_CAP = 4000  # truncate only for the event log; prompts get the full text

# Friendly labels for the few allowlisted modules whose submodule is what the
# author actually uses; everything else is shown by its import name.
_IMPORT_LABELS = {
    "bs4": "bs4 (BeautifulSoup)",
    "html": "html.parser",
    "urllib": "urllib.parse",
}


def _imports() -> str:
    """The import allowlist string shown to the author, derived from
    sandbox.ALLOWED_IMPORTS so the prompt and the AST gate can never drift.
    `sys` and `forge_id` are omitted — tools don't need sys (the test prompt adds
    it for sys.exit), and forge_id is offered only when secrets are granted (see
    _secrets_note)."""
    hidden = {"sys", "forge_id"}
    names = sorted(n for n in sandbox.ALLOWED_IMPORTS if n not in hidden)
    return ", ".join(_IMPORT_LABELS.get(n, n) for n in names)


def _secrets_note(spec: "ToolSpec") -> str:
    """Author guidance for declared secrets — empty when the tool needs none.

    The tool reads each granted credential through ``forge_id.get(NAME)``; the
    value is brokered just-in-time and injected at runtime. The author sees only
    the env-var names and references, never any value."""
    if not spec.secrets:
        return ""
    grants = "; ".join(f"{name} (from {ref})" for name, ref in spec.secrets.items())
    return (
        "\n- SECRETS — this tool was granted these credentials, injected into the "
        f"environment ONLY while it runs: {grants}. Read each via `import forge_id` "
        "then `forge_id.get(\"ENV_NAME\")`. NEVER hardcode a secret, accept one as a "
        "parameter, log it, or return it — read it where you use it and nowhere else."
    )


@dataclass
class ToolSpec:
    name: str
    purpose: str
    proposed_signature: str
    # Secrets the tool needs, declared by reference: ENV_VAR -> op:// path. The
    # value is brokered just-in-time at run; the tool reads it via forge_id.get.
    secrets: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolSpec":
        return cls(
            name=d["name"],
            purpose=d.get("purpose", ""),
            proposed_signature=d.get("proposed_signature", f"{d['name']}(...)"),
            secrets=dict(d.get("secrets") or {}),
        )


@dataclass
class SynthesisResult:
    spec: ToolSpec
    status: str  # "promoted" | "failed"
    record: dict[str, Any] | None
    revisions: int
    last_error: str | None


# --- code extraction ---------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _extract_code(text: str) -> str:
    """Pull the Python source out of a model response (fenced or bare)."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _truncate(text: str, cap: int = EVENT_STDERR_CAP) -> str:
    if text is None:
        return ""
    return text if len(text) <= cap else text[:cap] + f"\n...[truncated {len(text) - cap} chars]"


# --- prompts -----------------------------------------------------------------

_AUTHOR_TOOL_SYSTEM = """You author ONE Python tool function for an autonomous agent.
Output ONLY the Python source for a single module — no prose, no explanation, no markdown fences.

Hard contract:
- Exactly ONE public function named `{name}`. Keep this name. Aim to match the proposed signature `{signature}`, BUT you SHOULD ADD a generalizing parameter (with a default that reproduces the requested behavior) when an axis is plausibly variable for the same logic — e.g. given `fetch_hn_top_stories()` for page 1, author `fetch_hn_top_stories(page: int = 1) -> list[dict]` so the same tool serves page 2+. Adding such a default-valued parameter never breaks the caller (the default preserves the original request) and prevents near-duplicate tools later.
- Full type hints on every parameter and the return value.
- A docstring describing what it does, its parameters, and what it returns.
- Imports limited to: {imports}. NOTHING else. No os, no subprocess, no eval/exec.
- File I/O is allowed but SCOPED: use only RELATIVE paths in the current working directory (the harness provides a jailed workdir). Never use absolute paths (starting with `/` or `~`) or `..` traversal. Prefer bs4 (BeautifulSoup) for HTML parsing — it is far more robust than hand-rolled html.parser.
- No printing and no global mutable state. Network access is allowed (use httpx). On error, raise — do not print or swallow.
- Private helper functions (names starting with `_`) are allowed; there must be exactly one PUBLIC function.{secrets}

Design at the right altitude (think like a senior engineer — not too specific, not too general):
- Parameterize an axis ONLY where variation shares the SAME underlying logic. A page number, section, count, or query on the same site → a parameter with a sensible default (e.g. `page: int = 1`), because one implementation handles them. Do NOT hardcode such a value as a literal in the body.
- Do NOT widen an axis that would change the logic itself. Different websites have different HTML, so a single "scrape any site" function is WRONG — it ends up brittle or fakes structure it never parsed. Scope the tool to one site/source and say so.
- Separate what is STABLE from what VARIES: fetching (HTTP GET, redirects, decode) is the same everywhere; parsing is source-specific. Prefer a small reusable primitive plus a source-specific adapter over one over-parameterized function.
- State the DOMAIN OF VALIDITY in the docstring — exactly what input the tool is valid for (e.g. "parses Hacker News listing markup"). Never silently claim to handle inputs it was not built for.
- Litmus test: too-specific bloats the toolbox; too-general lies. Generalize where repetition is plausible for the same logic; specialize where the logic differs.
"""

_AUTHOR_TOOL_USER = """Capability needed: {purpose}
Function name: {name}
Proposed signature: {signature}

Write the module now. Output only Python source."""

_AUTHOR_TEST_SYSTEM = """You are an INDEPENDENT, ADVERSARIAL tester. A different agent wrote the tool; you did NOT see its source and you assume it may be buggy. Your job is to CATCH real defects, not to confirm the happy path. You test the tool from its CONTRACT (name, signature, purpose) alone — black box.

Output ONLY Python source — no prose, no markdown fences.

Hard contract:
- Import the tool with `from {module} import {name}`.
- NO test framework. Use bare `assert` and `sys.exit`.
- Write 2 to 5 assertions. At least one MUST be a CORRECTNESS invariant that a broken implementation would fail — not merely a shape/type check.
- REJECT DEGENERATE OUTPUTS. If the contract implies a field should VARY across records (a parsed domain, id, name, score, ...), assert the records are NOT all identical on that field — the same constant or fallback value for every record is a bug. If a field is parsed from external/real-world data, assert at least some values are non-default and plausibly correct (e.g. a real domain contains a dot and is not the catch-all fallback for every single record).
- Include at least one edge case.
- Stay resilient to benign content drift: assert on invariants and structure that hold regardless of which specific items are present right now — never on exact bytes or counts that change minute to minute.
- On success: exit 0 (you may `print("PASS")`). On failure: print a short, specific reason and exit nonzero (`sys.exit(1)`), or let an AssertionError propagate.
- Imports limited to: {module}, sys, {imports}.
- Tests may hit real network endpoints (these are web tasks).
"""

_AUTHOR_TEST_USER = """Write an adversarial contract test for this tool. You do NOT get to see its implementation — test it as a black box against its stated contract.

  name: {name}
  signature: {signature}
  purpose: {purpose}

Module to import: `{module}`. Output only Python source."""

_REVISE_SYSTEM = """You revise a tool that FAILED its own test in the sandbox.
Output ONLY the corrected Python source for the tool module — no prose, no markdown fences.

Rules:
- Fix the TOOL, not the test — unless the test is provably wrong (it asserts something the spec does not require). Default to fixing the tool.
- Keep the same public function name `{name}` and the same import contract.
- Same import allowlist ({imports}) and no-side-effects rules as before.{secrets}
- Address the specific failure shown in the sandbox output.
"""

_REVISE_USER = """Capability: {purpose}
Signature: {signature}

Current tool source:
```python
{tool_code}
```

The test it must pass:
```python
{test_code}
```

Your tool failed its own test. Sandbox output (verbatim):
--- stdout ---
{stdout}
--- stderr ---
{stderr}

Produce the corrected tool module. Output only Python source."""


# --- authoring ---------------------------------------------------------------


def author_tool(spec: ToolSpec) -> str:
    """LLM (plain completion) writes the tool module. One corrective retry if
    the output doesn't parse as Python."""
    system = _AUTHOR_TOOL_SYSTEM.format(
        name=spec.name, signature=spec.proposed_signature, imports=_imports(),
        secrets=_secrets_note(spec),
    )
    user = _AUTHOR_TOOL_USER.format(
        purpose=spec.purpose, name=spec.name, signature=spec.proposed_signature
    )
    msg = llm.complete(system, [{"role": "user", "content": user}], label="author_tool")
    code = _extract_code(llm.text_of(msg))
    if not _parses(code):
        retry_user = (
            user
            + "\n\nYour previous output did not parse as valid Python. "
            "Output ONLY valid Python source for the module, nothing else."
        )
        msg = llm.complete(system, [{"role": "user", "content": retry_user}], label="author_tool_retry")
        code = _extract_code(llm.text_of(msg))
    return code


def author_test(spec: ToolSpec, module: str) -> str:
    """Independent black-box adversarial test author (FORGE_TEST_MODEL).

    Deliberately does NOT receive the tool source — only the contract — so it
    cannot mirror the implementation's bugs.
    """
    system = _AUTHOR_TEST_SYSTEM.format(module=module, name=spec.name, imports=_imports())
    user = _AUTHOR_TEST_USER.format(
        name=spec.name,
        purpose=spec.purpose,
        signature=spec.proposed_signature,
        module=module,
    )
    msg = llm.complete(
        system, [{"role": "user", "content": user}], model=llm.TEST_MODEL, label="author_test"
    )
    return _extract_code(llm.text_of(msg))


def revise_tool(
    tool_code: str, test_code: str, spec: ToolSpec, result: sandbox.SandboxResult, module: str
) -> str:
    """LLM revises the tool given the verbatim sandbox failure."""
    system = _REVISE_SYSTEM.format(name=spec.name, imports=_imports(), secrets=_secrets_note(spec))
    user = _REVISE_USER.format(
        purpose=spec.purpose,
        signature=spec.proposed_signature,
        tool_code=tool_code,
        test_code=test_code,
        stdout=result.stdout or "(empty)",
        stderr=result.stderr or result.rejected_reason or "(empty)",
    )
    msg = llm.complete(system, [{"role": "user", "content": user}], label="revise")
    return _extract_code(llm.text_of(msg))


# --- orchestration (the demo) ------------------------------------------------


def synthesize(
    registry: Registry,
    spec: ToolSpec,
    max_revisions: int = MAX_REVISIONS,
    timeout: float = 30.0,
) -> SynthesisResult:
    """author → test → verify → (revise → re-verify)* → promote | fail.

    Every transition emits an event. The tool is promoted only after its own
    test passes in the sandbox.
    """
    module = spec.name
    tool_path = registry.tools_dir / f"{module}.py"
    test_path = registry.tools_dir / f"test_{module}.py"

    events.emit(
        "gap_detected",
        name=spec.name,
        purpose=spec.purpose,
        signature=spec.proposed_signature,
        secrets=list(spec.secrets.values()),
    )

    # Policy gate, before any code is written: if the tool declared secrets that
    # are unconfigured or outside policy, fail fast so the agent can replan —
    # don't author a tool that can never be granted what it needs.
    try:
        identity.authorize(spec.secrets, requester=spec.name)
    except identity.IdentityError as exc:
        registry.add_draft(
            spec.name, tool_path.name, spec.proposed_signature, spec.purpose,
            test_path.name, secrets=spec.secrets,
        )
        registry.mark_failed(spec.name)
        events.emit("tool_failed", name=spec.name, revisions=0, last_error=str(exc))
        return SynthesisResult(spec, "failed", registry.get(spec.name), 0, str(exc))

    tool_code = author_tool(spec)
    tool_path.write_text(tool_code, encoding="utf-8")
    events.emit("tool_drafted", name=spec.name, signature=spec.proposed_signature, chars=len(tool_code))

    test_code = author_test(spec, module)
    test_path.write_text(test_code, encoding="utf-8")
    events.emit("test_drafted", name=spec.name, chars=len(test_code))

    registry.add_draft(
        spec.name, tool_path.name, spec.proposed_signature, spec.purpose, test_path.name,
        secrets=spec.secrets,
    )

    last_error: str | None = None
    for attempt in range(max_revisions + 1):
        registry.mark_testing(spec.name)
        events.emit("verification_run", name=spec.name, attempt=attempt)
        result = sandbox.run_test(tool_path, test_path, timeout=timeout, secret_refs=spec.secrets)

        if result.passed:
            registry.promote(spec.name)
            record = registry.get(spec.name)
            events.emit(
                "tool_promoted",
                name=spec.name,
                attempt=attempt,
                revisions=record["revisions"],
                duration_s=result.duration,
            )
            return SynthesisResult(spec, "promoted", record, record["revisions"], None)

        last_error = result.stderr or result.rejected_reason or "test failed"
        events.emit(
            "verification_failed",
            name=spec.name,
            attempt=attempt,
            stderr=_truncate(result.stderr),
            stdout=_truncate(result.stdout),
            rejected_reason=result.rejected_reason,
            duration_s=result.duration,
        )

        if attempt == max_revisions:
            break

        tool_code = revise_tool(tool_code, test_code, spec, result, module)
        tool_path.write_text(tool_code, encoding="utf-8")
        revision = registry.bump_revision(spec.name)
        events.emit("tool_revised", name=spec.name, revision=revision, chars=len(tool_code))

    registry.mark_failed(spec.name)
    record = registry.get(spec.name)
    events.emit(
        "tool_failed",
        name=spec.name,
        revisions=record["revisions"],
        last_error=_truncate(last_error or ""),
    )
    return SynthesisResult(spec, "failed", record, record["revisions"], last_error)
