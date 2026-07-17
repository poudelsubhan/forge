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

from forge import events, llm, sandbox, synthesis, zero_bridge
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


def probe(candidate: dict[str, Any], bridge_url: str) -> str:
    """Make ONE real (paid) call through the bridge and return the raw CLI
    output. Raises on any failure — including a Pomerium 403 at tier0, which
    makes a denied BUY fail fast before any LLM spend."""
    import httpx

    resp = httpx.post(
        f"{bridge_url.rstrip('/')}/call",
        json={"token": candidate.get("token")},
        timeout=120.0,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError((body.get("output") or "bridge call failed")[:400])
    return body.get("output") or ""


_WRAPPER_SYSTEM = """You author ONE Python adapter function wrapping a PAID marketplace capability for an autonomous agent.
Output ONLY the Python source for a single module — no prose, no markdown fences.

Hard contract:
- Exactly ONE public function named `{name}` matching the signature `{signature}`. Full type hints and a docstring stating what it returns and that each call costs money ({price}).
- Imports limited to: json, re, httpx. Nothing else.
- Obtain the data ONLY via: httpx.post("{bridge}/call", json={{"token": "{token}"}}, timeout=120.0). The response is JSON: {{"ok": bool, "output": str}}. If not ok (or the HTTP call fails), raise RuntimeError including an excerpt of the output — never return fabricated data.
- `output` is CLI text whose payload is typically a trailing JSON object. A REAL probe of this exact capability produced the output shown by the user. Parse robustly (do not assume exact whitespace), extract the payload, and ADAPT it to the return contract implied by the signature and purpose — use the exact key names and types the contract asks for.
- No prints, no global state. Raise on any parse failure.
"""

_WRAPPER_USER = """Capability: {label}
Purpose (the contract): {purpose}
Signature: {signature}

Verbatim output of a real probe call to this capability:
---
{probe_output}
---

Write the adapter module now. Output only Python source."""


def wrap(candidate: dict[str, Any], spec: ToolSpec, bridge_url: str, probe_output: str) -> str:
    """LLM-authored adapter: honors the spec's return contract by adapting the
    REAL probed payload, not a guessed shape. The adversarial black-box test
    still judges the result — the adapter earns promotion like any built tool."""
    label = candidate.get("canonicalName") or candidate.get("name") or "capability"
    price = (candidate.get("pricing") or {}).get("summary") or "paid"
    system = _WRAPPER_SYSTEM.format(
        name=spec.name,
        signature=spec.proposed_signature,
        price=price,
        bridge=bridge_url.rstrip("/"),
        token=candidate.get("token"),
    )
    user = _WRAPPER_USER.format(
        label=label,
        purpose=spec.purpose,
        signature=spec.proposed_signature,
        probe_output=probe_output[:3000],
    )
    msg = llm.complete(system, [{"role": "user", "content": user}], label="author_wrapper")
    return synthesis._extract_code(llm.text_of(msg))


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

    # One real paid call up front. A Pomerium denial (tier0) or unfunded wallet
    # fails HERE — fast, before any LLM spend, and without touching the
    # registry (a policy denial is not a defect, so it costs no trust).
    try:
        probe_output = probe(candidate, bridge_url)
        events.emit("acquire_probe", token=candidate.get("token"), ok=True, chars=len(probe_output))
    except Exception as exc:  # noqa: BLE001
        events.emit("acquire_probe", token=candidate.get("token"), ok=False, error=str(exc)[:300])
        return SynthesisResult(spec, "failed", None, 0, f"probe failed: {exc}")

    module = spec.name
    tool_path = registry.tools_dir / f"{module}.py"
    test_path = registry.tools_dir / f"test_{module}.py"

    tool_code = wrap(candidate, spec, bridge_url, probe_output)
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
