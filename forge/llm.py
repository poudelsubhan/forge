"""Anthropic client wrapper (Phase 1B).

A single `complete()` entry point wraps `anthropic.Anthropic().messages.create`.
Two usage modes:

  * **Agent turns** — pass `tools=...` to use the native tool-use API.
  * **Tool/test authorship** — plain text completion (no tools); code
    generation is cleaner without tool-call wrapping.

Every call logs model, token usage, computed dollar cost, latency, and a hash
of the input to the event bus as an `llm_call` event. The cost meter is a
first-class artifact: `scripts/stats.py` aggregates these into cost-per-tool.

Retry on rate-limit / overload is delegated to the SDK (`max_retries`), which
already does exponential backoff — no custom retry loop.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import anthropic
from dotenv import load_dotenv

from forge import events

load_dotenv()

# The plan pins claude-sonnet-4-5; we default to its current successor
# claude-sonnet-4-6 (same tier/pricing, current model). Override with FORGE_MODEL
# — e.g. FORGE_MODEL=claude-sonnet-4-5 to match the plan verbatim, or
# FORGE_MODEL=claude-opus-4-8 for maximum synthesis quality.
DEFAULT_MODEL = os.environ.get("FORGE_MODEL", "claude-sonnet-4-6")

# The test author is a SEPARATE, INDEPENDENT agent from the tool author. By
# default it runs on the same model, but pointing it at a different (stronger)
# model makes the adversary more likely to catch the tool author's bugs —
# e.g. FORGE_TEST_MODEL=claude-opus-4-8 pairs a Sonnet builder with an Opus tester.
TEST_MODEL = os.environ.get("FORGE_TEST_MODEL", DEFAULT_MODEL)

# USD per 1M tokens (input, output). Used to compute per-call cost for the meter.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    """Lazily construct a shared client. SDK retries 429/5xx with backoff."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=2)
    return _client


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = _PRICING.get(model, _PRICING["claude-sonnet-4-6"])
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def _input_hash(system: str, messages: list[dict[str, Any]]) -> str:
    payload = system + repr(messages)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def complete(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    model: str | None = None,
    tool_choice: dict[str, Any] | None = None,
    label: str = "",
    on_text: Any = None,
) -> anthropic.types.Message:
    """One Anthropic Messages call, fully instrumented.

    `label` tags the call (e.g. "agent_turn", "author_tool", "author_test",
    "revise") so the cost meter can attribute spend per stage.

    `on_text`, if given, is a callback invoked with each text delta as it streams
    — this drives the interactive shell's token-by-token display. When omitted,
    the call is non-streaming (headless paths are unchanged).
    """
    model = model or DEFAULT_MODEL
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice

    # time.monotonic via the standard library — measure wall-clock latency.
    import time

    started = time.monotonic()
    if on_text is not None:
        with client().messages.stream(**kwargs) as stream:
            for delta in stream.text_stream:
                on_text(delta)
            message = stream.get_final_message()
    else:
        message = client().messages.create(**kwargs)
    latency = time.monotonic() - started

    usage = message.usage
    in_tok = usage.input_tokens
    out_tok = usage.output_tokens
    events.emit(
        "llm_call",
        model=model,
        label=label,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=round(cost_usd(model, in_tok, out_tok), 6),
        latency_s=round(latency, 3),
        input_hash=_input_hash(system, messages),
        stop_reason=message.stop_reason,
    )
    return message


def text_of(message: anthropic.types.Message) -> str:
    """Concatenate the text blocks of a response (ignoring tool_use blocks)."""
    return "".join(block.text for block in message.content if block.type == "text")


def tool_uses(message: anthropic.types.Message) -> list[Any]:
    """Return the tool_use blocks of a response, in order."""
    return [block for block in message.content if block.type == "tool_use"]
