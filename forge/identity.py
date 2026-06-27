"""Agent-identity / secrets broker — scoped, just-in-time credential access.

Forge already keeps the harness's OWN api key out of synthesized code (the
sandbox strips the subprocess env to ``PATH``). This module handles the other
direction: when a synthesized tool legitimately needs a real-world credential
(a GitHub token, a cloud key) to do its job, it must get one WITHOUT ever
holding it — the value must never appear in the tool's source, the agent's
context, or on disk.

The design follows the agent-identity principles (AGI House research brief):

  * **Zero standing privilege.** No secret lives in ``.env``, the prompt, or the
    tool source. The agent declares, by *reference* — an ``op://vault/item/field``
    path, a name not a value — which credentials a tool needs. Nothing is
    granted by default.
  * **Just-in-time + just-enough.** A reference is resolved to its value only at
    the moment the tool runs, scoped to exactly the references that task
    justified, and the value is gone the instant the run ends.
  * **Authority proven at runtime, not stored.** The harness authenticates to
    1Password with its OWN identity (a service-account token it holds, never the
    agent) and brokers the secret. The tool proves nothing and holds nothing.
  * **Accountability.** Every resolution — and every policy denial — is logged
    to the run's event stream by *reference*, never by value, so each secret
    access binds to the tool that requested it and the turn it happened on.

Backend: the 1Password CLI (``op read``), authenticated by EITHER a service
account (``OP_SERVICE_ACCOUNT_TOKEN`` — a dedicated machine identity, the ideal)
OR a signed-in user session (desktop-app CLI integration / ``op account add`` —
the path on a personal 1Password account, where the agent acts under the human's
unlocked session, still scoped by ``FORGE_OP_ALLOWED``). Swap ``_op_read`` for
the 1Password SDK or an OIDC / Workload-Identity-Federation broker without
touching the policy or audit boundary. When neither identity is present the
broker is dormant: Forge behaves exactly as before and the ``secrets`` capability
is not offered to the agent.
"""

from __future__ import annotations

import os
import subprocess
import functools
from contextlib import contextmanager
from typing import Iterator

from forge import events

REF_PREFIX = "op://"


class IdentityError(RuntimeError):
    """A secret could not be brokered — misconfiguration or policy denial."""


def _service_account_token() -> str | None:
    return os.environ.get("OP_SERVICE_ACCOUNT_TOKEN") or None


@functools.lru_cache(maxsize=1)
def _op_user_account() -> bool:
    """True if the ``op`` CLI is wired to a user account (desktop-app integration
    or ``op account add``), so ``op read`` can unlock and resolve references even
    without a service account — the path for a personal 1Password account.

    Cached: the binding doesn't change within a run, and this avoids shelling out
    on every turn. Detection is local and never prompts (``op account list`` only
    lists configured accounts); the actual unlock happens at ``op read`` time.
    """
    try:
        proc = subprocess.run(
            ["op", "account", "list", "--format=json"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() not in ("", "[]")


def identity_mode() -> str:
    """Which identity the harness will broker with: a dedicated service account
    (the ideal — a machine identity distinct from the human), a signed-in user
    session (the agent acts under the human's unlocked session, scoped by
    FORGE_OP_ALLOWED), or none."""
    if _service_account_token():
        return "service-account"
    if _op_user_account():
        return "user-session"
    return "none"


def is_configured() -> bool:
    """True when the harness holds a 1Password identity to broker with — either a
    service-account token or a signed-in user session. When False the secrets
    capability is dormant and Forge runs exactly as before."""
    return identity_mode() != "none"


def _allowlist() -> list[str]:
    """Reference prefixes the agent may request, from ``FORGE_OP_ALLOWED``
    (comma-separated). Default-deny: an empty allowlist grants nothing — "by
    default an identity has no access."
    """
    raw = os.environ.get("FORGE_OP_ALLOWED", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_allowed(ref: str) -> bool:
    return any(ref.startswith(prefix) for prefix in _allowlist())


def authorize(secrets: dict[str, str], *, requester: str) -> None:
    """Policy gate, evaluated up front WITHOUT fetching any value: every declared
    reference must be a well-formed ``op://`` path inside the allowlist, and the
    harness must hold an identity. Raises ``IdentityError`` (auditing the denial)
    so the caller can fail the synthesis and let the agent replan.
    """
    if not secrets:
        return
    if not is_configured():
        raise IdentityError(
            f"agent identity not configured: set OP_SERVICE_ACCOUNT_TOKEN, or sign in the "
            f"1Password CLI (desktop-app integration / `op account add`), to grant "
            f"'{requester}' the secrets it declared ({', '.join(secrets.values())})."
        )
    for ref in secrets.values():
        if not ref.startswith(REF_PREFIX):
            raise IdentityError(f"'{requester}': {ref!r} is not an op:// reference")
        if not is_allowed(ref):
            events.emit("secret_denied", requester=requester, ref=ref, reason="outside FORGE_OP_ALLOWED")
            raise IdentityError(
                f"'{requester}': reference {ref!r} is outside policy. Grant it by adding its "
                "prefix to FORGE_OP_ALLOWED — access is denied by default (zero standing privilege)."
            )


def _op_read(ref: str) -> str:
    """Resolve one ``op://`` reference to its value via the 1Password CLI. The
    service-account token in the env IS the harness identity; ``op`` returns the
    secret on stdout for this process only and writes it nowhere."""
    try:
        proc = subprocess.run(
            ["op", "read", ref],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:  # the `op` CLI is not installed
        raise IdentityError(
            "1Password CLI ('op') not found — install it (brew install 1password-cli) "
            "or point _op_read at the 1Password SDK."
        ) from exc
    if proc.returncode != 0:
        raise IdentityError(f"op read failed for {ref}: {proc.stderr.strip() or 'unknown error'}")
    return proc.stdout.rstrip("\n")


def resolve(secrets: dict[str, str], *, requester: str) -> dict[str, str]:
    """Broker declared references into ``ENV_VAR -> value``, re-checking policy
    and auditing each resolution. Returns a mapping to inject into a single tool
    execution. Raises ``IdentityError`` on misconfig/denial — nothing partial is
    returned. ``requester`` is the tool name, for the audit trail.
    """
    authorize(secrets, requester=requester)
    mode = identity_mode()
    resolved: dict[str, str] = {}
    for env_name, ref in secrets.items():
        resolved[env_name] = _op_read(ref)
        events.emit("secret_resolved", requester=requester, ref=ref, env=env_name, mode=mode)
    return resolved


@contextmanager
def injected(secrets: dict[str, str], *, requester: str) -> Iterator[dict[str, str]]:
    """Resolve ``secrets`` and expose them in ``os.environ`` for the duration of
    the block ONLY — restored on exit, so there is nothing standing to steal
    between tool calls. Used for in-process dispatch; the sandbox subprocess path
    passes the resolved mapping straight into the child env instead.
    """
    resolved = resolve(secrets, requester=requester)
    saved = {k: os.environ.get(k) for k in resolved}
    try:
        os.environ.update(resolved)
        yield resolved
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
