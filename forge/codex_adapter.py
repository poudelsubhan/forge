"""File-based Codex CLI adapter for tool synthesis and revision."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from forge import events

_VALID_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_CHILD_ENV_ALLOWLIST = frozenset(
    {"PATH", "HOME", "TMPDIR", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM"}
)
_TOOL_PROMPT = """Read SPEC.md. Write tool.py containing one public function with
the exact requested name, a typed signature, and a docstring. Follow the import
and safety constraints exactly. Write nothing else."""
_TEST_PROMPT = """Read SPEC.md. Write test_tool.py using pytest. Test the contract
as a black box: use `from tool import <requested function>`, cover correctness
and an edge case, and do not assume implementation details. Imports are limited
to pytest, sys, tool, and the imports explicitly allowed by SPEC.md. Do not
start a server or mock the network; if SPEC.md supplies a localhost service,
call that service directly. Write nothing else."""
_REVISE_PROMPT = """The verification test failed. Read SPEC.md and FAILURE.md.
Fix tool.py to satisfy the contract and the test. Do not weaken or edit the
test. Diagnose the actual runtime data structure behind any parse/key failure;
handle semantically equivalent nested fields when the contract requires a
normalized result. Do not copy constants, fixtures, mock payloads, or lookup
tables from test_tool.py into the implementation. Write nothing else."""
_REVISE_TEST_PROMPT = """The test itself was rejected by the verification gate.
Read SPEC.md, FAILURE.md, and test_tool.py. Fix test_tool.py to test the same
contract while obeying every import and safety constraint. You cannot see the
implementation; do not weaken the correctness assertions. Write nothing else."""


@dataclass(frozen=True)
class ToolCandidate:
    tool_name: str
    workspace: Path
    tool_file: Path
    test_file: Path

    @property
    def tool_code(self) -> str:
        return self.tool_file.read_text(encoding="utf-8")

    @property
    def test_code(self) -> str:
        return self.test_file.read_text(encoding="utf-8")


class CodexAdapterError(RuntimeError):
    pass


def _run_codex(workspace: Path, prompt: str, timeout: float) -> str | None:
    command = [
        shutil.which("codex") or "codex",
        "exec",
        "--cd",
        str(workspace),
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--json",
        prompt,
    ]
    try:
        child_env = {
            key: value for key, value in os.environ.items() if key in _CHILD_ENV_ALLOWLIST
        }
        proc = subprocess.run(
            command,
            cwd=workspace,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexAdapterError(f"Codex invocation failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no output").strip()
        raise CodexAdapterError(f"Codex exited {proc.returncode}: {detail[-4000:]}")

    thread_id: str | None = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
    return thread_id


def _require_files(workspace: Path) -> ToolCandidate:
    spec = workspace / "SPEC.md"
    name_match = re.search(r"^Tool name: `([^`]+)`$", spec.read_text(encoding="utf-8"), re.MULTILINE)
    if not name_match:
        raise CodexAdapterError("SPEC.md is missing its tool name")
    candidate = ToolCandidate(
        tool_name=name_match.group(1),
        workspace=workspace,
        tool_file=workspace / "tool.py",
        test_file=workspace / "test_tool.py",
    )
    missing = [p.name for p in (candidate.tool_file, candidate.test_file) if not p.is_file()]
    if missing:
        raise CodexAdapterError(f"Codex did not create required file(s): {', '.join(missing)}")
    return candidate


def synthesize(
    tool_name: str,
    purpose: str,
    signature: str,
    workspace_dir: Path | str,
    *,
    allowed_imports: str,
    timeout: float = 300.0,
) -> ToolCandidate:
    """Ask separate Codex invocations to author the tool and black-box test."""
    if not _VALID_TOOL_NAME.fullmatch(tool_name):
        raise ValueError(f"invalid tool name: {tool_name!r}")

    workspace = (Path(workspace_dir) / tool_name).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    spec_text = f"""# Tool contract

Tool name: `{tool_name}`
Required signature: `{signature}`

## Capability

{purpose}

## Constraints

- The implementation must expose exactly one public function named `{tool_name}`.
- Use full type hints and a docstring.
- Allowed imports: {allowed_imports}.
- No shell, dynamic execution, environment-variable access, or credential access.
- Network access is allowed only when the capability requires it.
- Tests may use a localhost URL supplied by the contract; no other network in tests.
- The test must validate this contract, not mirror the implementation.
"""
    (workspace / "SPEC.md").write_text(spec_text, encoding="utf-8")
    for stale in ("tool.py", "test_tool.py", "FAILURE.md"):
        path = workspace / stale
        if path.exists():
            path.unlink()

    events.emit("synthesis_requested", name=tool_name, workspace=str(workspace))
    tool_thread = _run_codex(workspace, _TOOL_PROMPT, timeout)
    if not (workspace / "tool.py").is_file():
        raise CodexAdapterError("Codex did not create tool.py")

    # A separate workspace prevents the tester from being anchored on tool.py.
    tester = workspace / ".tester"
    if tester.exists():
        shutil.rmtree(tester)
    tester.mkdir()
    shutil.copy2(workspace / "SPEC.md", tester / "SPEC.md")
    test_thread = _run_codex(tester, _TEST_PROMPT, timeout)
    generated_test = tester / "test_tool.py"
    if not generated_test.is_file():
        raise CodexAdapterError("Codex did not create test_tool.py")
    shutil.copy2(generated_test, workspace / "test_tool.py")
    shutil.rmtree(tester)

    candidate = _require_files(workspace)
    events.emit(
        "synthesis_complete",
        name=tool_name,
        workspace=str(workspace),
        tool_thread_id=tool_thread,
        test_thread_id=test_thread,
    )
    return candidate


def revise(
    workspace_dir: Path | str,
    stderr_text: str,
    *,
    timeout: float = 300.0,
) -> ToolCandidate:
    """Append verbatim sandbox output and ask Codex to revise the tool."""
    workspace = Path(workspace_dir).resolve()
    failure_path = workspace / "FAILURE.md"
    with failure_path.open("a", encoding="utf-8") as fh:
        fh.write("\n\n# Verification failure\n\n```text\n")
        fh.write(stderr_text)
        fh.write("\n```\n")
    name_match = re.search(
        r"^Tool name: `([^`]+)`$",
        (workspace / "SPEC.md").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    tool_name = name_match.group(1) if name_match else workspace.name
    revise_test = "AST check failed (test)" in stderr_text
    target = "test" if revise_test else "tool"
    events.emit(
        "revision_requested",
        name=tool_name,
        workspace=str(workspace),
        target=target,
    )
    if revise_test:
        tester = workspace / ".tester"
        if tester.exists():
            shutil.rmtree(tester)
        tester.mkdir()
        for filename in ("SPEC.md", "FAILURE.md", "test_tool.py"):
            shutil.copy2(workspace / filename, tester / filename)
        _run_codex(tester, _REVISE_TEST_PROMPT, timeout)
        revised_test = tester / "test_tool.py"
        if not revised_test.is_file():
            raise CodexAdapterError("Codex did not revise test_tool.py")
        shutil.copy2(revised_test, workspace / "test_tool.py")
        shutil.rmtree(tester)
    else:
        _run_codex(workspace, _REVISE_PROMPT, timeout)
    return _require_files(workspace)
