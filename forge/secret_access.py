"""Runtime secret access for synthesized tools — imported as ``forge_id``.

A synthesized tool NEVER hardcodes a credential and never accepts one as an
argument (that would route the secret value through the agent's context). It
declares the secret it needs *by reference* at request time, then reads the
brokered value here at runtime:

    import forge_id
    token = forge_id.get("GITHUB_TOKEN")

The harness resolves the declared ``op://`` reference just-in-time and injects
the value into this single execution's environment; ``get`` only reads it, and
the value is gone the moment the run ends. This module is trusted harness code
(copied into the verification sandbox as ``forge_id.py`` and aliased in-process
for dispatch). It deliberately exposes exactly one capability — read a granted
secret by name — and imports nothing from the rest of Forge, so it is safe to
drop into the jail standalone.
"""

from __future__ import annotations

import os


class SecretUnavailable(RuntimeError):
    """A tool asked for a secret that was not granted to this run."""


def get(name: str) -> str:
    """Return the value of a secret granted to this tool, by env-var name.

    Raises ``SecretUnavailable`` if it was not injected — which means it was not
    declared in the tool's ``request_tool`` ``secrets`` mapping, or policy
    denied it. The tool must declare every secret it reads.
    """
    try:
        return os.environ[name]
    except KeyError:
        raise SecretUnavailable(
            f"secret {name!r} was not granted to this tool. Declare it in the "
            "request_tool `secrets` mapping (ENV_NAME -> op:// reference); the "
            "harness injects it at runtime, scoped to this call."
        ) from None
