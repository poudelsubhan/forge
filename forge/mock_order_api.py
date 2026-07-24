"""Local demo order service with an intentionally quirky money shape."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 8377

ORDERS: dict[str, dict[str, Any]] = {
    "FORGE-1001": {
        "order_id": "FORGE-1001",
        "customer_email": "maya@example.com",
        "amount": {"currency": "USD", "cents": "12999"},
        "purchased_at": "2026-07-08T16:30:00Z",
        "item": {"name": "Nimbus Headphones", "category": "electronics"},
    },
    "FORGE-1002": {
        "order_id": "FORGE-1002",
        "customer_email": "leo@example.com",
        "amount": {"currency": "USD", "cents": "4800"},
        "purchased_at": "2026-07-17T09:15:00Z",
        "item": {"name": "Linen Travel Shirt", "category": "apparel"},
    },
}


class OrderHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "orders":
            self._send(404, {"error": "not_found"})
            return
        order = ORDERS.get(parts[1])
        self._send(200, order) if order else self._send(404, {"error": "unknown_order"})

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


def create_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), OrderHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Forge's local mock order API.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"mock order API listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
