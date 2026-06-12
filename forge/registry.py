"""Tool registry (Phase 1C).

A tool is one Python file in ``forge/tools/`` exposing exactly one public
function (type hints + docstring), plus an entry in ``manifest.json``.
``manifest.json`` is the single source of truth for tool state — both the TUI
and `to_anthropic_tools()` read from it; in-memory state never drifts from disk.

Status lifecycle:  draft → testing → failed | promoted
Failed tools stay in the manifest (the TUI shows the graveyard — it's part of
the story).

The seed registry is nearly empty: synthesis must be forced. The two builtins
(`final_answer`, `request_tool`) live in the loop, not here — this registry
holds only synthesized tools.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import time
import typing
from pathlib import Path
from typing import Any, Callable

from forge import events

VALID_STATUSES = frozenset({"draft", "testing", "failed", "promoted"})

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _json_type(annotation: Any) -> str:
    return _PY_TO_JSON.get(annotation, "string")


def fn_to_schema(name: str, description: str, fn: Callable[..., Any]) -> dict[str, Any]:
    """Derive an Anthropic tool-use JSON schema from a function's signature.

    We inspect the live signature + type hints rather than ask the LLM to write
    its own schema — a model-written schema drifts from the actual function.
    """
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        annotation = hints.get(pname, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = str
        properties[pname] = {
            "type": _json_type(annotation),
            "description": f"{pname} argument",
        }
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    desc = (description or (fn.__doc__ or "")).strip() or name
    return {
        "name": name,
        "description": desc,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


class Registry:
    def __init__(self, tools_dir: Path | str) -> None:
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.tools_dir / "manifest.json"
        if not self.manifest_path.exists():
            self._write({"tools": []})
        self.data: dict[str, Any] = self._read()

    # --- persistence ---------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        self.manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save(self) -> None:
        self._write(self.data)

    def reset(self) -> None:
        """Wipe the toolbox (the `--fresh` path): drop manifest + synthesized files."""
        for record in self.data["tools"]:
            for key in ("file", "test_file"):
                target = self.tools_dir / record.get(key, "")
                if record.get(key) and target.exists():
                    target.unlink()
        self.data = {"tools": []}
        self.save()

    # --- queries -------------------------------------------------------------

    def _find(self, name: str) -> dict[str, Any] | None:
        for record in self.data["tools"]:
            if record["name"] == name:
                return record
        return None

    def get(self, name: str) -> dict[str, Any] | None:
        return self._find(name)

    def list_all(self) -> list[dict[str, Any]]:
        return list(self.data["tools"])

    def list_promoted(self) -> list[dict[str, Any]]:
        return [r for r in self.data["tools"] if r["status"] == "promoted"]

    def has_promoted(self, name: str) -> bool:
        record = self._find(name)
        return record is not None and record["status"] == "promoted"

    # --- mutations -----------------------------------------------------------

    def add_draft(
        self,
        name: str,
        file: str,
        signature: str,
        description: str,
        test_file: str,
    ) -> dict[str, Any]:
        record = self._find(name)
        if record is None:
            record = {
                "name": name,
                "file": file,
                "signature": signature,
                "description": description,
                "status": "draft",
                "created_at": _now_iso(),
                "test_file": test_file,
                "revisions": 0,
                "uses": 0,
            }
            self.data["tools"].append(record)
        else:
            # Re-drafting an existing (e.g. previously failed) tool.
            record.update(
                file=file,
                signature=signature,
                description=description,
                test_file=test_file,
                status="draft",
            )
        self.save()
        return record

    def _set_status(self, name: str, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        record = self._find(name)
        if record is None:
            raise KeyError(f"unknown tool: {name!r}")
        record["status"] = status
        self.save()

    def mark_testing(self, name: str) -> None:
        self._set_status(name, "testing")

    def promote(self, name: str) -> None:
        self._set_status(name, "promoted")

    def mark_failed(self, name: str) -> None:
        self._set_status(name, "failed")

    def bump_revision(self, name: str) -> int:
        record = self._find(name)
        if record is None:
            raise KeyError(f"unknown tool: {name!r}")
        record["revisions"] = record.get("revisions", 0) + 1
        self.save()
        return record["revisions"]

    # --- tool-use bridge -----------------------------------------------------

    def _load_fn(self, record: dict[str, Any]) -> Callable[..., Any]:
        path = self.tools_dir / record["file"]
        spec = importlib.util.spec_from_file_location(
            f"forge_tool_{record['name']}", path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load tool module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, record["name"], None)
        if fn is None:
            raise AttributeError(
                f"tool file {record['file']} has no function {record['name']!r}"
            )
        return fn

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        """Convert promoted tools to Anthropic tool-use JSON schemas."""
        tools: list[dict[str, Any]] = []
        for record in self.list_promoted():
            try:
                fn = self._load_fn(record)
            except Exception:
                # A promoted tool that won't import is a bug, but don't crash the
                # whole turn — just skip it (it will surface in the event log).
                events.emit("error", where="to_anthropic_tools", tool=record["name"])
                continue
            tools.append(fn_to_schema(record["name"], record["description"], fn))
        return tools

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        """Import the tool module, call the function, increment `uses`."""
        record = self._find(name)
        if record is None or record["status"] != "promoted":
            raise KeyError(f"no promoted tool named {name!r}")
        fn = self._load_fn(record)
        result = fn(**args)
        record["uses"] = record.get("uses", 0) + 1
        self.save()
        events.emit("tool_used", name=name, uses=record["uses"])
        return result
