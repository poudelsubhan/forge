"""Aggregate a run's JSONL into the metrics that tell the story (Observability).

    uv run scripts/stats.py [runs/X.jsonl]   # defaults to the latest run

Reports:
  * Verification catch rate — fraction of drafted tools that failed >=1
    verification before promotion. This is the pitch: "the gate caught X% of
    generated tools before they touched real work."
  * Reuse ratio — tool_used events / syntheses. Run 2 (with --keep) should be
    all reuse, zero synthesis.
  * Convergence quality — did the run halt via convergence/answer vs. the cap.
  * Cost — total, and per synthesized tool (tokens x model price).

The JSONL log is the single source; this and --replay both read from it.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run as `uv run scripts/stats.py` — put the project root (not scripts/) on path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge.events import load_run  # noqa: E402

_AUTHOR_LABELS = {"author_tool", "author_tool_retry", "author_test", "revise"}


def _latest_run(runs_dir: str = "runs") -> str | None:
    files = sorted(Path(runs_dir).glob("*.jsonl"))
    return str(files[-1]) if files else None


def aggregate(path: str) -> dict:
    events = load_run(path)
    tools: dict[str, dict] = {}
    current: str | None = None
    n_syntheses = 0
    n_tool_used = 0
    halted_reason = None
    total_cost = 0.0
    total_in = total_out = 0
    agent_cost = 0.0

    def tool(name: str) -> dict:
        return tools.setdefault(
            name,
            {"attempts": 0, "failures": 0, "revisions": 0, "status": "drafted",
             "sandbox_s": 0.0, "cost": 0.0, "in_tok": 0, "out_tok": 0},
        )

    for e in events:
        t = e.get("type")
        if t == "gap_detected":
            current = e["name"]
            tool(current)
            n_syntheses += 1
        elif t == "verification_run":
            tool(e["name"])["attempts"] += 1
        elif t == "verification_failed":
            rec = tool(e["name"])
            rec["failures"] += 1
            rec["sandbox_s"] += e.get("duration_s") or 0.0
        elif t == "tool_revised":
            tool(e["name"])["revisions"] = e.get("revision", tool(e["name"])["revisions"])
        elif t == "tool_promoted":
            rec = tool(e["name"])
            rec["status"] = "promoted"
            rec["revisions"] = e.get("revisions", rec["revisions"])
            rec["sandbox_s"] += e.get("duration_s") or 0.0
            current = None
        elif t == "tool_failed":
            rec = tool(e["name"])
            rec["status"] = "failed"
            rec["revisions"] = e.get("revisions", rec["revisions"])
            current = None
        elif t == "tool_used":
            n_tool_used += 1
        elif t == "halted":
            halted_reason = e.get("reason")
        elif t == "llm_call":
            cost = e.get("cost_usd", 0.0)
            total_cost += cost
            total_in += e.get("input_tokens", 0)
            total_out += e.get("output_tokens", 0)
            if e.get("label") in _AUTHOR_LABELS and current is not None:
                rec = tool(current)
                rec["cost"] += cost
                rec["in_tok"] += e.get("input_tokens", 0)
                rec["out_tok"] += e.get("output_tokens", 0)
            else:
                agent_cost += cost

    drafted = len(tools)
    caught = sum(1 for r in tools.values() if r["failures"] > 0)
    catch_rate = caught / drafted if drafted else 0.0
    reuse_ratio = n_tool_used / n_syntheses if n_syntheses else 0.0

    return {
        "path": path,
        "tools": tools,
        "drafted": drafted,
        "promoted": sum(1 for r in tools.values() if r["status"] == "promoted"),
        "failed": sum(1 for r in tools.values() if r["status"] == "failed"),
        "caught": caught,
        "catch_rate": catch_rate,
        "syntheses": n_syntheses,
        "tool_used": n_tool_used,
        "reuse_ratio": reuse_ratio,
        "halted_reason": halted_reason,
        "converged_quality": halted_reason in ("converged", "final_answer"),
        "total_cost": total_cost,
        "agent_cost": agent_cost,
        "synthesis_cost": total_cost - agent_cost,
        "total_in": total_in,
        "total_out": total_out,
    }


def report(path: str) -> None:
    a = aggregate(path)
    print(f"run: {a['path']}\n")
    print(f"  verification catch rate : {a['catch_rate']:.0%} "
          f"({a['caught']}/{a['drafted']} drafted tools failed >=1 verification before promotion)")
    print(f"  reuse ratio             : {a['reuse_ratio']:.2f} "
          f"({a['tool_used']} tool uses / {a['syntheses']} syntheses)")
    print(f"  promoted / failed       : {a['promoted']} / {a['failed']}")
    print(f"  convergence             : halted via {a['halted_reason']} "
          f"({'GOOD' if a['converged_quality'] else 'CAP — investigate prompt regressions'})")
    print(f"  total cost              : ${a['total_cost']:.4f} "
          f"(synthesis ${a['synthesis_cost']:.4f} · agent ${a['agent_cost']:.4f})")
    print(f"  tokens                  : {a['total_in']} in / {a['total_out']} out")
    if a["tools"]:
        print("\n  per-tool:")
        print(f"    {'tool':<22} {'status':<9} {'rev':>3} {'fails':>5} {'sbox_s':>7} {'cost$':>8}")
        for name, r in a["tools"].items():
            print(f"    {name:<22} {r['status']:<9} {r['revisions']:>3} {r['failures']:>5} "
                  f"{r['sandbox_s']:>7.2f} {r['cost']:>8.4f}")


def main(argv: list[str]) -> int:
    path = argv[0] if argv else _latest_run()
    if not path:
        print("no run files found in runs/", file=sys.stderr)
        return 1
    report(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
