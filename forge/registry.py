"""Tool registry (Phase 1C).

A tool is one Python file in ``forge/tools/`` exposing exactly one public
function (type hints + docstring), plus an entry in ``manifest.json``.
``manifest.json`` is the single source of truth for tool state — both the TUI
and `to_openai_tools()` read from it; in-memory state never drifts from disk.

Status lifecycle:  draft → testing → failed | promoted
Failed tools stay in the manifest (the TUI shows the graveyard — it's part of
the story).

The seed registry is nearly empty: synthesis must be forced. The three builtins
(`update_plan`, `request_tool`, `final_answer`) live in the loop, not here —
this registry holds only synthesized tools.
"""

from __future__ import annotations

import ast
import inspect
import json
import time
import types
import typing
from pathlib import Path
from typing import Any, Callable

from forge import events, sandbox

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
    """Map a Python annotation to a JSON-schema type.

    Resolves typing generics (``list[str]`` → ``list`` → ``"array"``,
    ``dict[str, int]`` → ``"object"``) via ``get_origin``, and unwraps
    ``Optional[X]`` / ``X | None`` to the non-None member. Without this an
    annotated collection parameter silently fell through to ``"string"`` and the
    agent was told to pass a string where the tool wanted a list.
    """
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:  # Optional[X] / X | None
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if args:
            return _json_type(args[0])
    if origin is not None:
        annotation = origin
    return _PY_TO_JSON.get(annotation, "string")


def fn_to_schema(name: str, description: str, fn: Callable[..., Any]) -> dict[str, Any]:
    """Derive an OpenAI function-tool schema from a function's signature.

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
        "type": "function",
        "name": name,
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": False,
    }


def file_to_schema(
    name: str, description: str, path: Path
) -> tuple[dict[str, Any], str]:
    """Derive a schema and display signature from source without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name),
        None,
    )
    if function is None:
        raise ValueError(f"{path.name} has no function {name!r}")
    args = function.args.posonlyargs + function.args.args
    default_start = len(args) - len(function.args.defaults)
    properties: dict[str, Any] = {}
    required: list[str] = []
    display: list[str] = []
    for index, arg in enumerate(args):
        annotation_text = ast.unparse(arg.annotation) if arg.annotation else "str"
        root = annotation_text.split("[", 1)[0]
        json_type = {
            "str": "string",
            "int": "integer",
            "float": "number",
            "bool": "boolean",
            "list": "array",
            "dict": "object",
        }.get(root, "string")
        properties[arg.arg] = {"type": json_type, "description": f"{arg.arg} argument"}
        text = f"{arg.arg}: {annotation_text}"
        if index < default_start:
            required.append(arg.arg)
        else:
            text += f" = {ast.unparse(function.args.defaults[index - default_start])}"
        display.append(text)
    return_annotation = ast.unparse(function.returns) if function.returns else "Any"
    schema = {
        "type": "function",
        "name": name,
        "description": description or ast.get_docstring(function) or name,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": False,
    }
    return schema, f"{name}({', '.join(display)}) -> {return_annotation}"


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
        """Wipe the toolbox (the `--fresh` path): drop every synthesized tool +
        test file and empty the manifest.

        We sweep the whole tools dir, not just the files named in the manifest.
        An ORPHAN file — a tool whose synthesis was interrupted before
        `add_draft` recorded it, or a manually-renamed variant — is not in
        `self.data["tools"]`, so the old manifest-only loop left it on disk and
        it polluted the next run (this is why `--fresh` failed to remove a
        stale `_v3` and it had to be deleted by hand). Every `*.py` here is
        synthesized runtime state (the synthesized tools are gitignored; only
        the seed manifest is tracked), so deleting them all is safe."""
        for path in self.tools_dir.glob("*.py"):
            if path.name == "__init__.py":
                continue
            path.unlink()
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
        record = self._find(name)
        if record is not None:
            try:
                _, signature = file_to_schema(
                    name, record.get("description", ""), self.tools_dir / record["file"]
                )
                record["signature"] = signature
                self.save()
            except Exception:
                pass  # keep the proposed signature if the function won't load

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

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Convert promoted tools to Responses API function-tool schemas."""
        tools: list[dict[str, Any]] = []
        for record in self.list_promoted():
            try:
                schema, _ = file_to_schema(
                    record["name"],
                    record["description"],
                    self.tools_dir / record["file"],
                )
            except Exception as exc:
                # A promoted tool that won't import is a bug, but don't crash the
                # whole turn — just skip it. Include the reason so the failure is
                # actually diagnosable in the event log (it was logged blind).
                events.emit(
                    "error",
                    where="to_openai_tools",
                    tool=record["name"],
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            tools.append(schema)
        return tools

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        """Import the tool module, call the function, increment `uses`."""
        record = self._find(name)
        if record is None or record["status"] != "promoted":
            raise KeyError(f"no promoted tool named {name!r}")
        result = sandbox.run_tool(self.tools_dir / record["file"], name, args)
        record["uses"] = record.get("uses", 0) + 1
        self.save()
        events.emit("tool_used", name=name, uses=record["uses"])
        return result
