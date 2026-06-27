"""Verified execution sandbox (Phase 1D).

`run_test(tool_file, test_file)` runs the synthesized test against the
synthesized tool in an isolated subprocess. The core invariant of the whole
harness: no tool is promoted without passing its own test here.

Isolation (hackathon-grade — stated honestly in the README):

  * **Pre-exec AST check** rejects dangerous code before it ever runs: imports
    outside an allowlist, `os.system` / `subprocess` / `eval` / `exec`, and
    `open(...)` on an absolute path or `..` traversal. File I/O with relative
    paths is allowed (Workstream C) — confined to the subprocess cwd jail.
  * **Subprocess** runs in a fresh temp dir containing only the copied tool +
    test files, with the environment stripped to ``PATH`` (no API key leaks
    into synthesized code), a wall-clock timeout, and `.pyc` writes disabled.
  * **Network stays ON** — the demo domain is web tasks, and tests hit real
    endpoints.

Note on flags: we run with ``-E -s -B`` (not ``-I``). ``-I`` implies ``-P``,
which removes the script's directory from ``sys.path`` — that would break the
test's ``import <tool>`` of its sibling file. ``-E -s`` still ignores
``PYTHON*`` env vars and user site-packages, which is the isolation we need,
while keeping the script dir importable. We invoke ``sys.executable`` so the
sandbox uses this project's venv (where ``httpx`` is installed).
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# The single allowlist of imports a synthesized tool/test may use. This is the
# one source of truth — synthesis.py derives the author-facing prompt string
# from it (see synthesis._imports) so the two can never drift.
ALLOWED_IMPORTS = frozenset(
    {
        # stdlib
        "json",
        "re",
        "html",  # covers html.parser
        "urllib",  # covers urllib.parse
        "datetime",
        "collections",
        "math",
        "csv",
        "io",
        "typing",
        "string",
        "itertools",
        "functools",
        "pathlib",  # scoped file I/O
        "sys",  # tests legitimately use sys.exit
        # web
        "httpx",
        "bs4",  # BeautifulSoup, robust HTML parsing
        # identity: the brokered-secret read helper (forge_id.get), copied into
        # the jail. Tools read granted credentials through this, never os.environ.
        "forge_id",
    }
)

# Attribute-call names that signal shelling out / dynamic exec.
_BANNED_ATTR_CALLS = frozenset(
    {"system", "popen", "Popen", "run", "call", "check_output", "check_call", "spawn"}
)
_BANNED_NAME_CALLS = frozenset({"eval", "exec", "__import__", "compile"})


@dataclass
class SandboxResult:
    passed: bool
    stdout: str
    stderr: str
    duration: float
    rejected_reason: str | None = None


def ast_check(code: str, extra_allowed: frozenset[str] | set[str] = frozenset()) -> str | None:
    """Return a rejection reason string, or None if the code passes the gate."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc}"

    allowed = ALLOWED_IMPORTS | set(extra_allowed)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in allowed:
                    return f"disallowed import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root not in allowed:
                    return f"disallowed import: {node.module}"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BANNED_NAME_CALLS:
                return f"disallowed call: {func.id}()"
            if isinstance(func, ast.Attribute) and func.attr in _BANNED_ATTR_CALLS:
                return f"disallowed call: .{func.attr}()"
            if isinstance(func, ast.Name) and func.id == "open":
                reason = _check_open(node)
                if reason:
                    return reason
    return None


def _check_open(node: ast.Call) -> str | None:
    """Scoped file I/O (Workstream C): open() is allowed for reads and writes,
    but the path must stay inside the working dir. Reject statically-detectable
    escapes — absolute paths and `..` traversal in a string-literal path. Paths
    computed at runtime can't be checked here; the subprocess cwd is the jail.
    """
    if node.args and isinstance(node.args[0], ast.Constant):
        path = node.args[0].value
        if isinstance(path, str):
            if path.startswith("/") or path.startswith("~"):
                return f"disallowed absolute file path: {path!r} (use a relative path in the working dir)"
            if ".." in path.replace("\\", "/").split("/"):
                return f"disallowed path traversal: {path!r}"
    return None


def run_test(
    tool_file: Path | str,
    test_file: Path | str,
    timeout: float = 30.0,
    secret_refs: dict[str, str] | None = None,
) -> SandboxResult:
    """Verify a tool against its test in an isolated subprocess.

    ``secret_refs`` (ENV_VAR -> op:// reference) are brokered just-in-time by the
    identity layer right before the subprocess starts and injected into its env
    for that one run, so a tool that needs a real credential can be verified for
    real without the secret ever touching its source or the agent's context.
    """
    tool_file = Path(tool_file)
    test_file = Path(test_file)
    tool_code = tool_file.read_text(encoding="utf-8")
    test_code = test_file.read_text(encoding="utf-8")
    tool_module = tool_file.stem

    # Gate the tool against the base allowlist.
    reason = ast_check(tool_code)
    if reason:
        return SandboxResult(False, "", f"AST check failed (tool): {reason}", 0.0, reason)
    # The test may additionally import the tool-under-test by module name.
    reason = ast_check(test_code, extra_allowed={tool_module})
    if reason:
        return SandboxResult(False, "", f"AST check failed (test): {reason}", 0.0, reason)

    with tempfile.TemporaryDirectory(prefix="forge_sbx_") as tmp:
        tmp_dir = Path(tmp)
        shutil.copy(tool_file, tmp_dir / tool_file.name)
        shutil.copy(test_file, tmp_dir / test_file.name)

        # Copy the sanctioned secret-read helper into the jail so a tool can
        # `import forge_id` and read a brokered credential. (Trusted harness code,
        # not AST-gated; exposes only forge_id.get(name).)
        helper_src = Path(__file__).resolve().parent / "secret_access.py"
        if helper_src.exists():
            shutil.copy(helper_src, tmp_dir / "forge_id.py")

        # Strip the environment to PATH — the ANTHROPIC_API_KEY (the secret the
        # invariant protects) never reaches synthesized code. Network stays on
        # (the demo domain is web tasks), so tests can hit real endpoints.
        env = {"PATH": os.environ.get("PATH", "")}

        # Broker any declared secrets just-in-time and inject ONLY their resolved
        # values into this child's env — scoped to this run, gone when it exits.
        # The OP service-account token stays in the parent; the child never sees it.
        if secret_refs:
            from forge import identity

            try:
                env.update(identity.resolve(secret_refs, requester=tool_module))
            except identity.IdentityError as exc:
                return SandboxResult(False, "", f"identity error: {exc}", 0.0, str(exc))

        started = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-E", "-s", "-B", test_file.name],
                cwd=tmp_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
            return SandboxResult(
                False, stdout, f"TIMEOUT after {timeout:.0f}s", timeout
            )
        duration = time.monotonic() - started

    return SandboxResult(
        passed=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration=round(duration, 3),
    )
