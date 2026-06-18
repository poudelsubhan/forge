"""Bright Data integration — the live-web grounding + acting layer.

Forge's model is frozen at its training cutoff; Bright Data is how the agent
touches the world as it is *now*. Two capabilities, both via the single REST
endpoint ``POST https://api.brightdata.com/request``:

  * **web_unlock(url)** — Web Unlocker: fetch ANY page (proxies, anti-bot,
    CAPTCHA solved server-side), returned as clean markdown. This is what makes
    "act on the live web" real — plain ``httpx`` gets blocked on most real sites.
  * **web_search(query)** — SERP API: structured, current search results for
    grounding ("what does the web say right now").

These live in TRUSTED harness code on purpose. The sandbox strips the env to
``PATH`` (the API key must never reach synthesized code), so Bright Data is
exposed to the agent as builtin tools (see ``loop.py``); synthesized tools then
*parse* the content these return — fetch is stable and lives here, parsing is
site-specific and gets synthesized.
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus

import httpx
from dotenv import load_dotenv

load_dotenv()

API_ENDPOINT = "https://api.brightdata.com/request"

# Zone names are created in the Bright Data control panel. Defaults match the
# common auto-generated names; override per account via env.
_UNLOCKER_ZONE = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "web_unlocker1")
_SERP_ZONE = os.environ.get("BRIGHTDATA_SERP_ZONE", "serp_api1")
_TIMEOUT = float(os.environ.get("BRIGHTDATA_TIMEOUT", "60"))


def _api_key() -> str | None:
    return os.environ.get("BRIGHTDATA_API_KEY") or None


def is_configured() -> bool:
    """True if a Bright Data key is present — gates whether the builtin web
    tools are offered to the agent, so Forge still runs without a key."""
    return _api_key() is not None


def _request(zone: str, url: str, data_format: str = "markdown", timeout: float | None = None) -> str:
    """One Bright Data request. Returns the (markdown) body, or raises."""
    key = _api_key()
    if not key:
        raise RuntimeError("BRIGHTDATA_API_KEY is not set — cannot reach Bright Data.")
    payload = {
        "zone": zone,
        "url": url,
        "format": "raw",          # return the body directly, not a JSON envelope
        "method": "GET",
        "data_format": data_format,  # "markdown" → clean, model-friendly text
    }
    resp = httpx.post(
        API_ENDPOINT,
        json=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout or _TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def web_unlock(url: str, data_format: str = "markdown") -> str:
    """Fetch a live web page via Bright Data Web Unlocker (bypasses bot
    detection / CAPTCHAs). `data_format` may be "markdown" (clean text, default)
    or "html" (raw markup for a synthesized parser to consume)."""
    return _request(_UNLOCKER_ZONE, url, data_format=data_format)


def web_search(query: str, engine: str = "google") -> str:
    """Live, structured web search via Bright Data SERP API. Returns current
    results as markdown — grounding for what the web says right now."""
    engines = {
        "google": "https://www.google.com/search?q=",
        "bing": "https://www.bing.com/search?q=",
    }
    base = engines.get(engine, engines["google"])
    return _request(_SERP_ZONE, base + quote_plus(query), data_format="markdown")
