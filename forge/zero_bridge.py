"""Local HTTP bridge to the Zero.xyz CLI (Track A).

Synthesized/acquired tools run inside the sandbox, whose AST gate bans
`subprocess` — so a wrapper tool cannot shell out to the `zero` CLI itself.
Instead the harness runs this tiny HTTP bridge in the MAIN process; wrapper
tools reach it with plain `httpx` (allowlisted). In Phase 2 the bridge sits
behind a Pomerium route, so acquired-tool execution is itself tier-gated.

Endpoints:
  GET  /health          → {"ok": true}
  POST /call            → body {"token": "<capability token>"}
                          runs `zero fetch --capability <token>`, returns
                          {"ok": bool, "output": "<combined stdout/stderr>"}
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8477
FETCH_TIMEOUT = 90.0

# Markers in `zero fetch` output that mean the call did NOT deliver data.
_FAILURE_MARKERS = ("Insufficient funds", "Fetch failed", "not found", "error:")


def zero_bin() -> str:
    """Prefer the managed runner (auto-updated), fall back to PATH."""
    managed = Path.home() / ".zero" / "runtime" / "bin" / "zero"
    if managed.exists():
        return str(managed)
    found = shutil.which("zero")
    if found:
        return found
    raise FileNotFoundError("zero CLI not found (npm i -g @zeroxyz/cli && zero init)")


def call_capability(token: str, timeout: float = FETCH_TIMEOUT) -> dict:
    proc = subprocess.run(
        [zero_bin(), "fetch", "--capability", token],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 and not any(m.lower() in output.lower() for m in _FAILURE_MARKERS)
    return {"ok": ok, "output": output}


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/call":
            self._send(404, {"ok": False, "error": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length) or b"{}")
            token = req["token"]
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"ok": False, "error": f"bad request: {exc}"})
            return
        try:
            self._send(200, call_capability(token))
        except subprocess.TimeoutExpired:
            self._send(504, {"ok": False, "output": "zero fetch timed out"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "output": f"bridge error: {exc}"})

    def log_message(self, *args) -> None:  # silence per-request stderr noise
        pass


def start(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()
