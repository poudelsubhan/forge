"""Zero.xyz acquisition path — the BUY side of make-or-buy (Track A).

When the loop hits a capability gap, synthesis BUILDS a tool; this module
ACQUIRES one from the Zero.xyz agentic-web marketplace instead. The acquired
tool is a thin httpx wrapper over the local zero bridge — and it earns
promotion exactly like a built tool: an independent adversarial test authored
black-box, run in the sandbox. Acquired tools get no special trust.

Pipeline:  discover (zero search, free) → rank → wrap → test → sandbox
           → promote | fail.  On fail we do NOT revise (the wrapper is not
           the bug — the capability is); the caller falls back to synthesis.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from forge import events, sandbox, synthesis, zero_bridge
from forge.registry import Registry
from forge.synthesis import SynthesisResult, ToolSpec

SEARCH_TIMEOUT = 45.0

_STOPWORDS = frozenset(
    "a an the of in on for from to with and or return returns returning live "
    "data dict list acquired paid current get fetch".split()
)


def distill_query(spec: ToolSpec) -> str:
    """Marketplace search wants keywords, not a contract paragraph. Take the
    tool name's words plus the first content words of the purpose."""
    words = [w for w in spec.name.replace("_", " ").split() if w != "zero"]
    first_sentence = spec.purpose.split(".")[0]
    for token in first_sentence.split():
        clean = token.strip(",()'\"").lower()
        if clean and clean not in _STOPWORDS and clean not in words:
            words.append(clean)
        if len(words) >= 8:
            break
    return " ".join(words)


def _search(query: str) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [zero_bridge.zero_bin(), "search", query, "--json"],
        capture_output=True,
        text=True,
        timeout=SEARCH_TIMEOUT,
    )
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout).get("capabilities", [])
    except json.JSONDecodeError:
        return []


def discover(gap: str, limit: int = 3) -> list[dict[str, Any]]:
    """Query the Zero.xyz index for capabilities covering `gap`.

    Free/read-only. Returns healthy candidates ranked by success rate then
    price. Emits `acquire_search` and one `acquire_candidate` per keeper.
    """
    raw = _search(gap)
    healthy = []
    for cap in raw:
        if cap.get("availabilityStatus") != "healthy":
            continue
        rating = cap.get("rating") or {}
        try:
            success = float(rating.get("successRate") or 0.0)  # unrated ranks last
        except (TypeError, ValueError):
            success = 0.0
        try:
            cost = float((cap.get("cost") or {}).get("amount") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        healthy.append((success, cost, cap))
    healthy.sort(key=lambda t: (-t[0], t[1]))
    kept = [cap for _, _, cap in healthy[:limit]]
    events.emit("acquire_search", query=gap, total=len(raw), kept=len(kept))
    for cap in kept:
        events.emit(
            "acquire_candidate",
            token=cap.get("token"),
            name=cap.get("canonicalName") or cap.get("name"),
            cost=(cap.get("cost") or {}).get("amount"),
            success_rate=(cap.get("rating") or {}).get("successRate"),
        )
    return kept


_WRAPPER_TEMPLATE = '''"""Acquired via Zero.xyz: {label} ({price}/call, x402 protocol).

{purpose}

Domain of validity: returns the live payload of the "{label}" marketplace
capability; structure follows that feed. Raises RuntimeError when the paid
call cannot be completed (e.g. wallet unfunded or capability unavailable).
"""
import json
import re

import httpx


def _extract(text: str) -> dict:
    """Best-effort structured payload from CLI output: trailing JSON object,
    else every number labelled by nearby text, else the raw text."""
    for match in reversed(list(re.finditer(r"\\{{[^{{}}]*\\}}|\\[[^\\[\\]]*\\]", text, re.DOTALL))):
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else {{"items": parsed}}
    numbers = re.findall(r"[-+]?\\d[\\d,]*\\.?\\d*", text)
    if numbers:
        return {{"values": numbers[:8], "text": text.strip()[-400:]}}
    return {{"text": text.strip()[-400:]}}


def {name}() -> dict:
    """{purpose}

    Returns a dict: {{{{'source': capability name, 'data': parsed payload}}}}.
    """
    resp = httpx.post(
        "{bridge}/call",
        json={{"token": "{token}"}},
        timeout=120.0,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError("acquired capability failed: " + (body.get("output") or "")[:400])
    return {{"source": "{label}", "data": _extract(body.get("output") or "")}}
'''


def wrap(candidate: dict[str, Any], spec: ToolSpec, bridge_url: str) -> str:
    """Generate wrapper-tool source for `candidate` satisfying `spec`.

    v1 wrappers are zero-argument (the capability call is fully specified by
    its token); the docstring states the domain of validity.
    """
    label = candidate.get("canonicalName") or candidate.get("name") or "capability"
    price = (candidate.get("pricing") or {}).get("summary") or "paid"
    return _WRAPPER_TEMPLATE.format(
        label=label,
        price=price,
        purpose=spec.purpose,
        name=spec.name,
        bridge=bridge_url.rstrip("/"),
        token=candidate.get("token"),
    )


def acquire(
    registry: Registry,
    spec: ToolSpec,
    bridge_url: str,
    timeout: float = 120.0,
    query: str | None = None,
) -> SynthesisResult | None:
    """Try to BUY the capability. Returns None when no candidate exists (caller
    falls back to synthesis); otherwise a SynthesisResult whose record carries
    provenance `via="zero"`. The acquired wrapper passes through the SAME
    black-box adversarial test + sandbox gate as a built tool.
    """
    candidates = discover(query or distill_query(spec))
    if not candidates:
        return None
    candidate = candidates[0]

    module = spec.name
    tool_path = registry.tools_dir / f"{module}.py"
    test_path = registry.tools_dir / f"test_{module}.py"

    tool_code = wrap(candidate, spec, bridge_url)
    tool_path.write_text(tool_code, encoding="utf-8")
    events.emit(
        "acquire_wrapped",
        name=spec.name,
        token=candidate.get("token"),
        capability=candidate.get("canonicalName") or candidate.get("name"),
        chars=len(tool_code),
    )

    test_code = synthesis.author_test(spec, module)
    test_path.write_text(test_code, encoding="utf-8")
    events.emit("test_drafted", name=spec.name, chars=len(test_code), via="zero")

    record = registry.add_draft(
        spec.name, tool_path.name, spec.proposed_signature, spec.purpose, test_path.name
    )
    record["via"] = "zero"
    record["capability_token"] = candidate.get("token")
    registry.save()

    registry.mark_testing(spec.name)
    events.emit("verification_run", name=spec.name, attempt=0, via="zero")
    result = sandbox.run_test(tool_path, test_path, timeout=timeout)

    if result.passed:
        registry.promote(spec.name)
        record = registry.get(spec.name)
        events.emit(
            "tool_promoted", name=spec.name, attempt=0, revisions=0,
            duration_s=result.duration, via="zero",
        )
        return SynthesisResult(spec, "promoted", record, 0, None)

    last_error = result.stderr or result.rejected_reason or result.stdout or "test failed"
    events.emit(
        "verification_failed", name=spec.name, attempt=0, via="zero",
        stderr=(result.stderr or "")[:2000], stdout=(result.stdout or "")[:2000],
        rejected_reason=result.rejected_reason, duration_s=result.duration,
    )
    registry.mark_failed(spec.name)
    events.emit("tool_failed", name=spec.name, revisions=0, via="zero",
                last_error=last_error[:2000])
    return SynthesisResult(spec, "failed", registry.get(spec.name), 0, last_error)
