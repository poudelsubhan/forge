"""Tool authoring + verification pipeline (Phase 2).

The sequence here *is* the demo:

    gap_detected → author tool → author test → verify in sandbox
      → (fail) revise the tool → re-verify ...  (max 3 revisions)
      → promote on pass, or mark failed.

Two LLM calls author the tool and the test **separately** (2A, 2B). This
separation matters: a single call writing both produces tests that mirror the
implementation's bugs. The test author sees the spec, signature, and tool
source, but is instructed to test the *contract*, not echo the code.

The verification gate (2C) is the product: a tool is only promoted after its
own test passes in the sandbox. On failure the verbatim sandbox stderr is fed
back into a revision prompt — summarized errors produce worse fixes.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

from forge import events, llm, sandbox
from forge.registry import Registry

MAX_REVISIONS = 3
EVENT_STDERR_CAP = 4000  # truncate only for the event log; prompts get the full text

# Imports a synthesized tool may use — mirrors sandbox.ALLOWED_IMPORTS. Stated
# in the prompt so the model doesn't reach for something the gate will reject.
_TOOL_IMPORTS = "httpx, json, re, html.parser, urllib.parse, datetime, collections, math, csv, io, typing"


@dataclass
class ToolSpec:
    name: str
    purpose: str
    proposed_signature: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolSpec":
        return cls(
            name=d["name"],
            purpose=d.get("purpose", ""),
            proposed_signature=d.get("proposed_signature", f"{d['name']}(...)"),
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
- Exactly ONE public function named `{name}`. Keep this name and its core parameters; you may refine defaults and the return type. Aim to match the proposed signature `{signature}`.
- Full type hints on every parameter and the return value.
- A docstring describing what it does, its parameters, and what it returns.
- Imports limited to: {imports}. NOTHING else. No os, no subprocess, no eval/exec, no file writes.
- No side effects beyond the return value: no printing, no global mutable state, no writing files.
- Read-only network access is allowed (use httpx). On error, raise — do not print or swallow.
- Private helper functions (names starting with `_`) are allowed; there must be exactly one PUBLIC function.
"""

_AUTHOR_TOOL_USER = """Capability needed: {purpose}
Function name: {name}
Proposed signature: {signature}

Write the module now. Output only Python source."""

_AUTHOR_TEST_SYSTEM = """You author a standalone test for a single tool function.
You see the tool's spec, signature, and source, but you test the CONTRACT — what the function must do — NOT the implementation. Do not just mirror the code's logic.

Output ONLY Python source — no prose, no markdown fences.

Hard contract:
- Import the tool with `from {module} import {name}`.
- NO test framework. Use bare `assert` and `sys.exit`.
- 2 to 4 assertions, including at least one edge case.
- On success: exit 0 (you may `print("PASS")`).
- On failure: print a short, specific reason and exit nonzero (`sys.exit(1)`), or let an AssertionError propagate.
- Imports limited to: {module}, sys, {imports}.
- Tests may hit real network endpoints (these are web tasks). Keep them fast and resilient to minor content drift — assert on structure, types, and invariants, not exact bytes that may change.
"""

_AUTHOR_TEST_USER = """Tool spec:
  name: {name}
  purpose: {purpose}
  signature: {signature}

Tool source:
```python
{tool_code}
```

Write the test now. The module to import is `{module}`. Output only Python source."""

_REVISE_SYSTEM = """You revise a tool that FAILED its own test in the sandbox.
Output ONLY the corrected Python source for the tool module — no prose, no markdown fences.

Rules:
- Fix the TOOL, not the test — unless the test is provably wrong (it asserts something the spec does not require). Default to fixing the tool.
- Keep the same public function name `{name}` and the same import contract.
- Same import allowlist ({imports}) and no-side-effects rules as before.
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
        name=spec.name, signature=spec.proposed_signature, imports=_TOOL_IMPORTS
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


def author_test(tool_code: str, spec: ToolSpec, module: str) -> str:
    """Separate LLM call writes the contract test (sees spec + source)."""
    system = _AUTHOR_TEST_SYSTEM.format(module=module, name=spec.name, imports=_TOOL_IMPORTS)
    user = _AUTHOR_TEST_USER.format(
        name=spec.name,
        purpose=spec.purpose,
        signature=spec.proposed_signature,
        tool_code=tool_code,
        module=module,
    )
    msg = llm.complete(system, [{"role": "user", "content": user}], label="author_test")
    return _extract_code(llm.text_of(msg))


def revise_tool(
    tool_code: str, test_code: str, spec: ToolSpec, result: sandbox.SandboxResult, module: str
) -> str:
    """LLM revises the tool given the verbatim sandbox failure."""
    system = _REVISE_SYSTEM.format(name=spec.name, imports=_TOOL_IMPORTS)
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
    )

    tool_code = author_tool(spec)
    tool_path.write_text(tool_code, encoding="utf-8")
    events.emit("tool_drafted", name=spec.name, signature=spec.proposed_signature, chars=len(tool_code))

    test_code = author_test(tool_code, spec, module)
    test_path.write_text(test_code, encoding="utf-8")
    events.emit("test_drafted", name=spec.name, chars=len(test_code))

    registry.add_draft(
        spec.name, tool_path.name, spec.proposed_signature, spec.purpose, test_path.name
    )

    last_error: str | None = None
    for attempt in range(max_revisions + 1):
        registry.mark_testing(spec.name)
        events.emit("verification_run", name=spec.name, attempt=attempt)
        result = sandbox.run_test(tool_path, test_path, timeout=timeout)

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
