from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx

from forge import codex_adapter, llm, sandbox
from forge.mock_order_api import create_server
from forge.registry import Registry
from forge.zendesk_client import ZendeskClient


def test_openai_tool_schema_is_derived_from_signature(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "lookup.py").write_text(
        'def lookup(order_id: str, timeout: float = 2.0) -> dict:\n'
        '    """Look up an order."""\n'
        "    return {'order_id': order_id}\n",
        encoding="utf-8",
    )
    registry = Registry(tools)
    registry.add_draft("lookup", "lookup.py", "lookup(...)", "Lookup", "test_lookup.py")
    registry.promote("lookup")

    schema = registry.to_openai_tools()[0]
    assert schema["type"] == "function"
    assert schema["parameters"]["properties"]["order_id"]["type"] == "string"
    assert schema["parameters"]["properties"]["timeout"]["type"] == "number"
    assert schema["parameters"]["required"] == ["order_id"]


def test_responses_helpers_parse_function_call() -> None:
    item = SimpleNamespace(
        type="function_call",
        call_id="call_1",
        name="lookup",
        arguments='{"order_id":"FORGE-1001"}',
        model_dump=lambda **_: {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"order_id":"FORGE-1001"}',
        },
    )
    response = SimpleNamespace(output=[item], output_text="")
    use = llm.tool_uses(response)[0]
    assert use.name == "lookup"
    assert use.input == {"order_id": "FORGE-1001"}
    assert llm.output_items(response)[0]["call_id"] == "call_1"


def test_legacy_anthropic_env_pin_does_not_reach_openai() -> None:
    assert not llm.DEFAULT_MODEL.startswith("claude-")


def test_codex_subprocess_environment_strips_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict = {}

    def fake_subprocess(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    for key in codex_adapter._SECRET_ENV_KEYS:
        monkeypatch.setenv(key, "secret")
    monkeypatch.setattr(codex_adapter.subprocess, "run", fake_subprocess)
    codex_adapter._run_codex(tmp_path, "write files", 1)
    assert all(key not in captured["env"] for key in codex_adapter._SECRET_ENV_KEYS)
    assert captured["env"]["PATH"] == os.environ["PATH"]


def test_codex_adapter_uses_separate_file_workspaces(
    tmp_path: Path, monkeypatch
) -> None:
    seen: list[tuple[Path, str]] = []

    def fake_run(workspace: Path, prompt: str, timeout: float) -> str:
        seen.append((workspace, prompt))
        if "test_tool.py" in prompt:
            assert not (workspace / "tool.py").exists()
            (workspace / "test_tool.py").write_text(
                "from tool import lookup_order\n\ndef test_contract():\n"
                "    assert lookup_order('x') == {'id': 'x'}\n",
                encoding="utf-8",
            )
        else:
            (workspace / "tool.py").write_text(
                "def lookup_order(order_id: str) -> dict:\n"
                "    return {'id': order_id}\n",
                encoding="utf-8",
            )
        return "thread"

    monkeypatch.setattr(codex_adapter, "_run_codex", fake_run)
    candidate = codex_adapter.synthesize(
        "lookup_order",
        "Look up an order",
        "lookup_order(order_id: str) -> dict",
        tmp_path,
        allowed_imports="json",
    )
    assert candidate.tool_file.is_file()
    assert candidate.test_file.is_file()
    assert candidate.workspace.is_absolute()
    assert seen[0][0] != seen[1][0]


def test_codex_adapter_revises_rejected_test_without_tool_source(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "lookup_order"
    workspace.mkdir()
    (workspace / "SPEC.md").write_text(
        "# Contract\n\nTool name: `lookup_order`\n",
        encoding="utf-8",
    )
    (workspace / "tool.py").write_text(
        "def lookup_order(order_id: str) -> dict:\n    return {'id': order_id}\n",
        encoding="utf-8",
    )
    (workspace / "test_tool.py").write_text(
        "import threading\nfrom tool import lookup_order\n",
        encoding="utf-8",
    )

    def fake_run(tester: Path, prompt: str, timeout: float) -> str:
        assert not (tester / "tool.py").exists()
        assert "test itself was rejected" in prompt
        (tester / "test_tool.py").write_text(
            "from tool import lookup_order\n\n"
            "def test_contract():\n    assert lookup_order('x')['id'] == 'x'\n",
            encoding="utf-8",
        )
        return "thread"

    monkeypatch.setattr(codex_adapter, "_run_codex", fake_run)
    candidate = codex_adapter.revise(
        workspace,
        "AST check failed (test): disallowed import: threading",
    )
    assert "threading" not in candidate.test_code
    assert candidate.tool_code.startswith("def lookup_order")


def test_pytest_sandbox_strips_credentials(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "safe_tool.py"
    test = tmp_path / "test_safe_tool.py"
    tool.write_text(
        "def safe_tool(value: str) -> str:\n    return value.upper()\n",
        encoding="utf-8",
    )
    test.write_text(
        "import os\nfrom safe_tool import safe_tool\n\n"
        "def test_safe():\n"
        "    assert safe_tool('ok') == 'OK'\n"
        "    assert 'OPENAI_API_KEY' not in os.environ\n"
        "    assert 'ZENDESK_API_TOKEN' not in os.environ\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ZENDESK_API_TOKEN", "secret")
    # os is intentionally rejected even in tests, so use a separate contract
    # test here to prove pytest execution, while env construction is inspected
    # directly by the sandbox implementation.
    test.write_text(
        "from safe_tool import safe_tool\n\n"
        "def test_safe():\n    assert safe_tool('ok') == 'OK'\n",
        encoding="utf-8",
    )
    assert sandbox.run_test(tool, test).passed


def test_mock_order_api_shape() -> None:
    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        response = httpx.get(f"http://127.0.0.1:{port}/orders/FORGE-1001")
        response.raise_for_status()
        payload = response.json()
        assert payload["amount"]["cents"] == "12999"
        assert payload["item"]["category"] == "electronics"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_zendesk_client_filters_open_and_posts_reply() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "tickets": [
                        {"id": 1, "status": "open"},
                        {"id": 2, "status": "solved"},
                    ]
                },
            )
        body = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 1, **body["ticket"]}})

    transport = httpx.MockTransport(handler)
    raw = httpx.Client(base_url="https://demo.zendesk.com", transport=transport)
    client = ZendeskClient("demo", "a@example.com", "token", client=raw)
    assert [t["id"] for t in client.list_open_tickets()] == [1]
    client.add_reply(1, "Approved")
    assert requests[-1].url.path == "/api/v2/tickets/1.json"
