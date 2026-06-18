"""Convergence-gated control loop (Phase 3).

The loop's main branch each turn is "call a promoted tool OR trigger synthesis".
The agent maintains an explicit plan, prefers promoted tools, and requests
synthesis only when no tool covers a step.

Halt criterion (kept dumb): a run halts when

  * `final_answer` fires  → halted(reason="final_answer"), or
  * a full pass over the remaining plan steps completes with zero toolbox
    changes AND zero plan mutations  → halted(reason="converged"), or
  * the hard safety cap of 25 turns is hit → halted(reason="cap").

`cap` is logged distinctly from the convergence halts so `scripts/stats.py` can
report convergence quality (converged/answered vs. cap).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from forge import brightdata, events, llm
from forge.registry import Registry
from forge.synthesis import MAX_REVISIONS, ToolSpec, synthesize

MAX_TURNS = 25
RESULT_CAP = 12_000  # bound any tool result fed back into the agent's context


# --- agent state -------------------------------------------------------------


class PlanStep(BaseModel):
    step: str
    status: str = "pending"  # pending | done | blocked


class AgentState(BaseModel):
    task: str
    plan: list[PlanStep] = Field(default_factory=list)
    findings: dict[str, Any] = Field(default_factory=dict)
    toolbox_version: int = 0  # bumped on any promote/fail


@dataclass
class RunResult:
    state: AgentState
    halt_reason: str
    turns: int
    final_answer: str | None


# --- builtin tools -----------------------------------------------------------

UPDATE_PLAN_TOOL = {
    "name": "update_plan",
    "description": (
        "Create or revise your explicit numbered plan. Call this first to lay out "
        "steps, and again whenever a step's status changes. Mark steps done as you "
        "finish them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "done", "blocked"],
                        },
                    },
                    "required": ["step", "status"],
                },
            }
        },
        "required": ["steps"],
    },
}

REQUEST_TOOL_TOOL = {
    "name": "request_tool",
    "description": (
        "Request synthesis of a NEW tool when no promoted tool covers a capability "
        "you need. The harness authors the tool AND a test, verifies it in a sandbox, "
        "and promotes it only if the test passes — then you can call it. "
        "BEFORE requesting: check your promoted toolbox. If an existing tool could do "
        "this step with DIFFERENT ARGUMENTS (e.g. another page or URL of the same "
        "source), CALL IT with those arguments instead — do not request a near-"
        "duplicate. Only request a new tool for a genuinely different capability or a "
        "different data source whose format the existing tools were not built for."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "snake_case function name, e.g. fetch_url"},
            "purpose": {"type": "string", "description": "Precisely what the tool must do."},
            "proposed_signature": {
                "type": "string",
                "description": "e.g. fetch_url(url: str, timeout: float = 10.0) -> str",
            },
        },
        "required": ["name", "purpose", "proposed_signature"],
    },
}

FINAL_ANSWER_TOOL = {
    "name": "final_answer",
    "description": (
        "Provide the final answer to the task and end the run. Call this only when "
        "every plan step is done."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The complete final answer."}},
        "required": ["text"],
    },
}

BUILTIN_TOOLS = [UPDATE_PLAN_TOOL, REQUEST_TOOL_TOOL, FINAL_ANSWER_TOOL]
BUILTIN_NAMES = {"update_plan", "request_tool", "final_answer"}

# --- Bright Data: live-web grounding + acting (offered only when configured) --

WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": (
        "Search the LIVE web via Bright Data SERP API and get current, structured "
        "results as markdown. Use this for anything time-sensitive or newer than your "
        "training data (prices, news, today's facts, who/what is current). Returns "
        "real search results, not your memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "engine": {"type": "string", "enum": ["google", "bing"], "description": "Search engine (default google)."},
        },
        "required": ["query"],
    },
}

WEB_UNLOCK_TOOL = {
    "name": "web_unlock",
    "description": (
        "Fetch a live web page via Bright Data Web Unlocker — bypasses bot detection "
        "and CAPTCHAs that block plain HTTP. Returns the page as clean markdown (or "
        "html). Use this to FETCH real-world sites; then request_tool to synthesize a "
        "PARSER over the returned content. Do NOT synthesize a tool that re-fetches a "
        "blocked site with raw httpx — fetch here, parse in a synthesized tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The page URL to fetch."},
            "data_format": {"type": "string", "enum": ["markdown", "html"], "description": "markdown (default) or html for a parser."},
        },
        "required": ["url"],
    },
}

BRIGHTDATA_TOOLS = [WEB_SEARCH_TOOL, WEB_UNLOCK_TOOL]
BRIGHTDATA_NAMES = {"web_search", "web_unlock"}


def _builtin_tools() -> list[dict[str, Any]]:
    """Builtins offered this turn: the always-on three, plus Bright Data's live-
    web tools when a key is configured (otherwise hidden, so Forge still runs)."""
    if brightdata.is_configured():
        return BUILTIN_TOOLS + BRIGHTDATA_TOOLS
    return BUILTIN_TOOLS

SYSTEM_PROMPT = """You are Forge, a self-extending agent. You accomplish a task by maintaining an explicit plan and acting ONE tool per turn.

Operating rules:
- FIRST, call update_plan to lay out a numbered plan of concrete steps.
- Prefer PROMOTED tools in your toolbox (shown in the state block). Call them directly.
- REUSE BEFORE REQUESTING: before asking for a new tool, check whether a promoted tool already does the job with DIFFERENT ARGUMENTS. A tool that fetches one page of a source also fetches its other pages — call it with the different argument; do not request a near-duplicate (e.g. a separate "page 2" tool).
- If a promoted tool is ALMOST right but too narrow (it hardcodes a value you now need to vary), prefer requesting a GENERALIZATION of that capability (same job, one new parameter) over a parallel copy.
- Request a genuinely NEW tool only for a different capability, or a different data source whose format existing tools were not built for (different websites have different markup — that is a real new tool, not a duplicate).
- When a step needs a capability that NO promoted tool provides, call request_tool with a precise name, purpose, and proposed_signature. The harness authors the tool and a test, verifies it in a sandbox, and promotes it only if it passes — then you can call it.
- NEVER inline-fake or hallucinate a capability you requested a tool for. If you requested fetch_url, do not pretend to know a page's contents — call the tool once it is promoted.
- For LIVE or up-to-date information (prices, news, anything newer than your training data), use web_search when it is available — it returns current results, not your memory. Never answer time-sensitive questions from memory if web_search is offered.
- To get a real-world web page, prefer web_unlock (it bypasses bot-blocking that plain httpx hits). The robust pattern: call web_unlock to FETCH the page as markdown/html, then request_tool to synthesize a PARSER that extracts the structured data from that returned content. Fetch with the builtin; parse with a synthesized tool.
- Design tools to RETURN COMPACT, STRUCTURED DATA (parsed records, counts, small lists) — not large raw blobs. You must pass tool outputs as inputs to later tools, so keeping outputs small keeps the work reliable.
- Each turn, call EXACTLY ONE tool: update_plan, a promoted tool, request_tool, or final_answer.
- Keep your plan current: call update_plan again to mark steps done as you finish them.
- When every step is done and you have the answer, call final_answer with the complete result.
- Be efficient: synthesize the few tools you need, then reuse them."""


# --- helpers -----------------------------------------------------------------


def _format_result(out: Any) -> str:
    if isinstance(out, str):
        text = out
    else:
        try:
            text = json.dumps(out, default=str, ensure_ascii=False)
        except Exception:
            text = repr(out)
    if len(text) > RESULT_CAP:
        text = text[:RESULT_CAP] + f"\n...[truncated {len(text) - RESULT_CAP} chars]"
    return text


def _short(out: Any, cap: int = 300) -> str:
    text = out if isinstance(out, str) else repr(out)
    return text if len(text) <= cap else text[:cap] + "..."


def _plan_changed(old: list[PlanStep], new: list[PlanStep]) -> bool:
    return [(s.step, s.status) for s in old] != [(s.step, s.status) for s in new]


def _state_summary(state: AgentState, registry: Registry) -> str:
    lines = ["=== STATE ===", f"Task: {state.task}", "Plan:"]
    if state.plan:
        for i, step in enumerate(state.plan, 1):
            lines.append(f"  {i}. [{step.status}] {step.step}")
    else:
        lines.append("  (no plan yet — call update_plan)")
    promoted = registry.list_promoted()
    lines.append(
        "Promoted tools (call these directly, varying ARGUMENTS to fit the step; "
        "do NOT request near-duplicates):"
    )
    if promoted:
        for rec in promoted:
            desc = (rec.get("description") or "").strip().replace("\n", " ")
            if len(desc) > 160:
                desc = desc[:160] + "…"
            lines.append(f"  - {rec['signature']}  (used {rec['uses']}x)")
            if desc:
                lines.append(f"      ↳ {desc}")
    else:
        lines.append("  (none yet)")
    return "\n".join(lines)


def _initial_user(task: str, state: AgentState, registry: Registry) -> str:
    return f"Task:\n{task}\n\n{_state_summary(state, registry)}"


def _result(state: AgentState, reason: str, turns: int) -> RunResult:
    return RunResult(
        state=state,
        halt_reason=reason,
        turns=turns,
        final_answer=state.findings.get("final_answer"),
    )


# --- the loop ----------------------------------------------------------------


class Session:
    """A persistent, multi-turn Forge session (Workstream B).

    Holds the conversation (`messages`) and the toolbox (`registry`) across
    successive prompts, so the agent reuses synthesized tools and prior context
    from one prompt to the next — the foundation of the interactive shell.
    `submit(task)` runs the turn-loop to its next halt and returns; the session
    then waits for the next prompt. `run()` below is the one-shot wrapper.
    """

    def __init__(
        self,
        registry: Registry,
        max_turns: int = MAX_TURNS,
        max_revisions: int = MAX_REVISIONS,
        sandbox_timeout: float = 30.0,
        stream_cb: Any = None,
    ) -> None:
        self.registry = registry
        self.max_turns = max_turns
        self.max_revisions = max_revisions
        self.sandbox_timeout = sandbox_timeout
        self.messages: list[dict[str, Any]] = []
        self._started = False
        # When set (by the interactive shell), agent-turn text streams through
        # this callback token-by-token; otherwise turns are non-streaming.
        self.stream_cb = stream_cb

    def submit(self, task: str) -> RunResult:
        registry = self.registry
        state = AgentState(task=task)
        if not self._started:
            events.emit("run_start", task=task, model=llm.DEFAULT_MODEL, max_turns=self.max_turns)
            self.messages.append({"role": "user", "content": _initial_user(task, state, registry)})
            self._started = True
        else:
            events.emit("prompt", task=task)
            self.messages.append(
                {"role": "user", "content": f"New task:\n{task}\n\n{_state_summary(state, registry)}"}
            )
        messages = self.messages
        stable_streak = 0

        for turn in range(1, self.max_turns + 1):
            events.emit("turn_start", turn=turn, toolbox_version=state.toolbox_version)
            tools = registry.to_anthropic_tools() + _builtin_tools()
            resp = llm.complete(
                SYSTEM_PROMPT,
                messages,
                tools=tools,
                tool_choice={"type": "any", "disable_parallel_tool_use": True},
                max_tokens=4096,
                label="agent_turn",
                on_text=self.stream_cb,
            )
            messages.append({"role": "assistant", "content": resp.content})

            text = llm.text_of(resp).strip()
            if text:
                events.emit("agent_message", turn=turn, text=text[:2000])

            uses = llm.tool_uses(resp)
            if not uses:
                messages.append(
                    {
                        "role": "user",
                        "content": "You must call exactly one tool (update_plan, a promoted tool, request_tool, or final_answer).",
                    }
                )
                continue

            block = uses[0]
            name = block.name
            tool_input = dict(block.input)
            tool_use_id = block.id

            toolbox_changed = False
            plan_mutated = False
            domain_used = False

            # --- terminal: final_answer ---
            if name == "final_answer":
                answer = tool_input.get("text", "")
                state.findings["final_answer"] = answer
                events.emit("agent_message", turn=turn, final=True, text=answer[:2000])
                # Close the tool_use with a tool_result so the conversation stays
                # valid for the NEXT prompt in this session.
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": "Final answer delivered. Awaiting the next instruction.",
                            }
                        ],
                    }
                )
                events.emit("halted", reason="final_answer", turn=turn, toolbox_version=state.toolbox_version)
                return _result(state, "final_answer", turn)

            # --- plan management ---
            if name == "update_plan":
                new_plan = [PlanStep(**s) for s in tool_input.get("steps", [])]
                plan_mutated = _plan_changed(state.plan, new_plan)
                state.plan = new_plan
                if plan_mutated:
                    events.emit("plan_updated", turn=turn, steps=[s.model_dump() for s in new_plan])
                result_text = "Plan recorded."

            # --- synthesis ---
            elif name == "request_tool":
                spec = ToolSpec.from_dict(tool_input)
                if registry.has_promoted(spec.name):
                    result_text = (
                        f"Tool '{spec.name}' already exists and is promoted; call it directly."
                    )
                else:
                    syn = synthesize(
                        registry, spec, max_revisions=self.max_revisions, timeout=self.sandbox_timeout
                    )
                    state.toolbox_version += 1
                    toolbox_changed = True
                    if syn.status == "promoted":
                        result_text = (
                            f"Tool '{spec.name}' PROMOTED — it passed its own test after "
                            f"{syn.revisions} revision(s). Signature: {syn.record['signature']}. "
                            "You can call it now."
                        )
                    else:
                        result_text = (
                            f"Tool '{spec.name}' FAILED verification after {syn.revisions} "
                            f"revisions. Last error:\n{(syn.last_error or '')[:800]}\n"
                            "Replan around this gap (try a different approach or signature)."
                        )

            # --- Bright Data live-web builtins ---
            elif name in BRIGHTDATA_NAMES:
                domain_used = True
                try:
                    out = brightdata.web_search(**tool_input) if name == "web_search" else brightdata.web_unlock(**tool_input)
                    result_text = _format_result(out)
                    state.findings[name] = _short(out)
                    events.emit("tool_used", name=name, uses=1)
                except Exception as exc:  # noqa: BLE001 — surface the failure to the agent
                    result_text = f"Bright Data tool '{name}' failed: {type(exc).__name__}: {exc}"
                    events.emit("error", where="brightdata", tool=name, error=str(exc))

            # --- promoted (domain) tool ---
            else:
                domain_used = True
                try:
                    out = registry.dispatch(name, tool_input)
                    result_text = _format_result(out)
                    state.findings[name] = _short(out)
                except Exception as exc:  # noqa: BLE001 — surface any tool error to the agent
                    result_text = f"Tool '{name}' raised {type(exc).__name__}: {exc}"
                    events.emit("error", where="dispatch", tool=name, error=str(exc))

            # --- convergence bookkeeping ---
            stable = not (toolbox_changed or plan_mutated or domain_used)
            stable_streak = stable_streak + 1 if stable else 0
            unfinished = sum(1 for s in state.plan if s.status != "done")
            threshold = max(1, unfinished)
            converged = turn >= 2 and stable_streak >= threshold
            events.emit(
                "convergence_check",
                turn=turn,
                stable=stable,
                stable_streak=stable_streak,
                threshold=threshold,
                unfinished=unfinished,
                toolbox_changed=toolbox_changed,
                plan_mutated=plan_mutated,
                toolbox_version=state.toolbox_version,
            )

            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tool_use_id, "content": result_text},
                        {"type": "text", "text": _state_summary(state, registry)},
                    ],
                }
            )

            if converged:
                events.emit("halted", reason="converged", turn=turn, toolbox_version=state.toolbox_version)
                return _result(state, "converged", turn)

        events.emit("halted", reason="cap", turn=self.max_turns, toolbox_version=state.toolbox_version)
        return _result(state, "cap", self.max_turns)


def run(
    task: str,
    registry: Registry,
    max_turns: int = MAX_TURNS,
    max_revisions: int = MAX_REVISIONS,
    sandbox_timeout: float = 30.0,
) -> RunResult:
    """One-shot wrapper: a single-prompt Session. Preserves the original API so
    main.py's `--no-tui`/live paths and tests are unchanged."""
    session = Session(
        registry, max_turns=max_turns, max_revisions=max_revisions, sandbox_timeout=sandbox_timeout
    )
    return session.submit(task)
