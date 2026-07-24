"""Codex authors tools; Forge verifies and promotes them."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge import codex_adapter, events, sandbox
from forge.registry import Registry

MAX_REVISIONS = 3
EVENT_STDERR_CAP = 4000
WORKSPACE_ROOT = Path(".forge/workspaces")


def _imports() -> str:
    return ", ".join(sorted(n for n in sandbox.ALLOWED_IMPORTS if n not in {"sys", "pytest"}))


def _truncate(text: str, cap: int = EVENT_STDERR_CAP) -> str:
    return text if len(text) <= cap else text[:cap] + f"\n...[truncated {len(text) - cap} chars]"


@dataclass
class ToolSpec:
    name: str
    purpose: str
    proposed_signature: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolSpec":
        return cls(
            name=value["name"],
            purpose=value.get("purpose", ""),
            proposed_signature=value.get("proposed_signature", f"{value['name']}(...)"),
        )


@dataclass
class SynthesisResult:
    spec: ToolSpec
    status: str
    record: dict[str, Any] | None
    revisions: int
    last_error: str | None


def _install_candidate(candidate: codex_adapter.ToolCandidate, registry: Registry) -> tuple[Path, Path]:
    tool_path = registry.tools_dir / f"{candidate.tool_name}.py"
    test_path = registry.tools_dir / f"test_{candidate.tool_name}.py"
    shutil.copy2(candidate.tool_file, tool_path)
    shutil.copy2(candidate.test_file, test_path)
    return tool_path, test_path


def synthesize(
    registry: Registry,
    spec: ToolSpec,
    max_revisions: int = MAX_REVISIONS,
    timeout: float = 30.0,
) -> SynthesisResult:
    events.emit(
        "gap_detected",
        name=spec.name,
        purpose=spec.purpose,
        signature=spec.proposed_signature,
    )
    candidate = codex_adapter.synthesize(
        spec.name,
        spec.purpose,
        spec.proposed_signature,
        WORKSPACE_ROOT,
        allowed_imports=_imports(),
    )
    tool_path, test_path = _install_candidate(candidate, registry)
    events.emit("tool_drafted", name=spec.name, signature=spec.proposed_signature)
    events.emit("test_drafted", name=spec.name)
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

        verbatim = result.stderr or result.stdout or result.rejected_reason or "test failed"
        last_error = verbatim
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
        candidate = codex_adapter.revise(candidate.workspace, verbatim)
        tool_path, test_path = _install_candidate(candidate, registry)
        revision = registry.bump_revision(spec.name)
        events.emit("tool_revised", name=spec.name, revision=revision)

    registry.mark_failed(spec.name)
    record = registry.get(spec.name)
    events.emit(
        "tool_failed",
        name=spec.name,
        revisions=record["revisions"],
        last_error=_truncate(last_error or ""),
    )
    return SynthesisResult(spec, "failed", record, record["revisions"], last_error)
