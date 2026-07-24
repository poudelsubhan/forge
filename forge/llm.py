"""OpenAI Responses API client for Forge's agent loop."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from forge import events

load_dotenv()

DEFAULT_MODEL = os.environ.get("FORGE_MODEL", "gpt-5.6")

_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.6": (5.0, 30.0),
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (1.0, 6.0),
}

_client: OpenAI | None = None


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


def client() -> OpenAI:
    """Lazily construct a shared client with SDK retry/backoff enabled."""
    global _client
    if _client is None:
        _client = OpenAI(max_retries=2)
    return _client


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = _PRICING.get(model, _PRICING["gpt-5.6"])
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def _input_hash(system: str, messages: list[dict[str, Any]]) -> str:
    payload = system + json.dumps(messages, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def complete(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    model: str | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    label: str = "",
    on_text: Any = None,
) -> Any:
    """Create one Responses API turn and emit usage, cost, and latency."""
    model = model or DEFAULT_MODEL
    kwargs: dict[str, Any] = {
        "model": model,
        "instructions": system,
        "input": messages,
        "max_output_tokens": max_tokens,
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if tools:
        kwargs["parallel_tool_calls"] = False

    started = time.monotonic()
    if on_text is not None:
        with client().responses.stream(**kwargs) as stream:
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    on_text(event.delta)
            response = stream.get_final_response()
    else:
        response = client().responses.create(**kwargs)
    latency = time.monotonic() - started

    usage = response.usage
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
    events.emit(
        "llm_call",
        model=model,
        label=label,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=round(cost_usd(model, in_tok, out_tok), 6),
        latency_s=round(latency, 3),
        input_hash=_input_hash(system, messages),
        stop_reason=getattr(response, "status", None),
        response_id=getattr(response, "id", None),
    )
    return response


def text_of(response: Any) -> str:
    return getattr(response, "output_text", "") or ""


def output_items(response: Any) -> list[dict[str, Any]]:
    """Serialize response output so it can be replayed as the next input."""
    items: list[dict[str, Any]] = []
    for item in getattr(response, "output", []):
        if hasattr(item, "model_dump"):
            items.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            items.append(item)
    return items


def tool_uses(response: Any) -> list[ToolUse]:
    uses: list[ToolUse] = []
    for item in getattr(response, "output", []):
        if getattr(item, "type", None) != "function_call":
            continue
        try:
            arguments = json.loads(getattr(item, "arguments", "{}") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        uses.append(
            ToolUse(
                id=getattr(item, "call_id", None) or getattr(item, "id", ""),
                name=item.name,
                input=arguments,
            )
        )
    return uses
