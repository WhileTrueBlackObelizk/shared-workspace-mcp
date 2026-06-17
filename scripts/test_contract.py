#!/usr/bin/env python3
"""Cheap contract tests for the MCP server."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("shared_workspace_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


async def tool_names(module) -> set[str]:
    return {tool.name for tool in await module.list_tools()}


def main() -> int:
    module = load_server()
    module.run_self_check()

    names = asyncio.run(tool_names(module))
    expected = {
        "workspace_write",
        "handover_prepare",
        "handover_takeover",
        "repo_status",
        "pipeline_create",
        "token_log",
        "learning_log_error",
        "goal_start",
        "feedback_maybe",
        "verify_file_refs",
        "gate_check",
        "gate_advance",
        "drift_report",
    }
    missing = expected - names
    assert not missing, f"missing tools: {sorted(missing)}"
    assert module.should_skip(ROOT / ".venv" / "x.py")
    assert not module.should_skip(ROOT / "server.py")
    refs = module.verify_refs("server.py:1-3", ROOT)
    assert refs["passed"], refs
    bad_refs = module.verify_refs("server.py:999999", ROOT)
    assert not bad_refs["passed"], bad_refs

    # run_check ruff preset dispatches and degrades gracefully (no crash) when absent
    ruff_out = module._call_tool("run_check", {"check": "ruff", "root": str(ROOT)})[0].text
    assert isinstance(ruff_out, str) and ruff_out, ruff_out

    store = {
        "kv": {},
        "log": [],
        "events": [{"ts": "2026-01-01T00:00:00", "type": "modified", "path": "server.py"}],
    }
    module.load_kv = lambda: dict(store["kv"])
    module.save_kv = lambda data: store.__setitem__("kv", data)
    module.load_log = lambda: list(store["log"])
    module.save_log = lambda entries: store.__setitem__("log", entries)
    module.load_file_events = lambda: list(store["events"])

    module._call_tool("handover_prepare", {
        "target": "cowork",
        "reason": "review",
        "last_output": "implemented handover tools",
        "next_steps": "1. review\n2. continue",
        "source": "codex",
    })
    assert store["kv"]["session_owner"]["value"] == "cowork"
    assert store["kv"]["last_output"]["value"] == "implemented handover tools"
    takeover = module._call_tool("handover_takeover", {"agent": "cowork"})[0].text
    assert "Owner OK." in takeover
    assert "## workspace_dump" in takeover
    assert "get_recent_activity" in takeover
    assert "get_file_events" in takeover

    check_gate_policy(module)
    check_self_score(module)

    print("contract tests OK")
    return 0


def check_self_score(module) -> None:
    """Evidence-based self-score: feedback caps the top, bad feedback subtracts,
    missing evidence scores 0 (anti-flattery)."""
    root = ROOT
    ts_event, ts_after = "2026-06-17T09:00:00", "2026-06-17T10:00:00"
    st = {
        "kv": {"acceptance_criteria": {"value": "Rejects bad input with a clear error; test added."}},
        "gates": [{"passed": True, "pipeline_id": ""}],
        "events": [{"ts": ts_event, "type": "modified", "path": str(root / "x.py")}],
        "checks": [{"ts": ts_after, "check": "pytest", "root": str(root), "passed": True, "source": "codex"}],
        "evidence": [{"ts": ts_after, "type": "file_refs", "root": str(root), "passed": True, "source": "codex"}],
        "learning": [], "feedback": [],
    }
    module.load_kv = lambda: dict(st["kv"])
    module.load_gate_results = lambda: list(st["gates"])
    module.load_file_events = lambda: list(st["events"])
    module.load_check_runs = lambda: list(st["checks"])
    module.load_evidence = lambda: list(st["evidence"])
    module.load_learning = lambda: list(st["learning"])
    module.load_feedback = lambda: list(st["feedback"])
    goal = {"created_at": "2026-06-17T08:00:00", "pipeline_id": ""}

    # everything green but no feedback yet -> capped below max
    capped = module.score_goal(goal, root)
    assert capped["score"] == 8, capped
    # user confirms -> full marks
    st["feedback"] = [{"rating": "good"}]
    assert module.score_goal(goal, root)["score"] == 10
    # bad feedback subtracts
    st["feedback"] = [{"rating": "bad"}]
    assert module.score_goal(goal, root)["score"] == 6
    # missing pre-registration + a gate blocked since start -> those score 0 (anti-flattery)
    st["feedback"] = [{"rating": "good"}]
    st["kv"] = {}
    st["learning"] = [{"ts": "2026-06-17T09:30:00", "tags": ["andon"], "error": "gate blocked"}]
    low = module.score_goal(goal, root)
    assert low["score"] == 6, low  # 0 + gates2 + evidence2 + 0 + good2
    by_name = {i["name"]: i["points"] for i in low["items"]}
    assert by_name["pre_registered"] == 0 and by_name["first_pass"] == 0, low


def check_gate_policy(module) -> None:
    """Gate policy: severity tiers, evidence freshness, pre-registration,
    four-eyes advisories, structured sign-out."""
    root = ROOT
    gstore = {
        "kv": {}, "goals": {}, "events": [], "checks": [],
        "evidence": [], "tokens": [], "learning": [], "log": [], "gate_results": [],
    }
    module.load_kv = lambda: dict(gstore["kv"])
    module.load_goals = lambda: dict(gstore["goals"])
    module.load_file_events = lambda: list(gstore["events"])
    module.load_check_runs = lambda: list(gstore["checks"])
    module.load_evidence = lambda: list(gstore["evidence"])
    module.load_token_log = lambda: list(gstore["tokens"])
    module.load_learning = lambda: list(gstore["learning"])
    module.save_learning = lambda e: gstore.__setitem__("learning", e)
    module.load_log = lambda: list(gstore["log"])
    module.save_log = lambda e: gstore.__setitem__("log", e)
    module.load_gate_results = lambda: list(gstore["gate_results"])
    module.save_gate_results = lambda e: gstore.__setitem__("gate_results", e)

    # --- plan gate requires pre-registered acceptance_criteria ---
    gstore["kv"] = {"current_plan": {"value": "Enforce real gates with fresh evidence everywhere."}}
    gstore["goals"] = {"g1": {}}
    res = module.evaluate_gate("plan", root)
    assert not res["passed"], "plan must fail without acceptance_criteria"
    gstore["kv"]["acceptance_criteria"] = {"value": "Every gate step blocks on stale or missing evidence."}
    assert module.evaluate_gate("plan", root)["passed"]

    # --- test gate: chain-of-custody freshness ---
    gstore["events"] = [{"ts": "2026-06-17T12:00:00", "type": "modified", "path": str(root / "server.py")}]
    gstore["checks"] = [{"ts": "2026-06-17T11:00:00", "check": "pytest", "root": str(root), "passed": True, "source": "codex"}]
    assert not module.evaluate_gate("test", root)["passed"], "stale check must not pass test gate"
    gstore["checks"].append({"ts": "2026-06-17T12:30:00", "check": "pytest", "root": str(root), "passed": True, "source": "codex"})
    assert module.evaluate_gate("test", root, actor="cowork")["passed"]

    # --- four-eyes advisory fires when actor == evidence source, but does not block ---
    res = module.evaluate_gate("test", root, actor="codex")
    assert res["passed"], "advisory must not block"
    assert any(a["name"] == "independent_check" for a in res["advisories"]), res

    # --- review gate: fresh file:line evidence ---
    gstore["evidence"] = [{"ts": "2026-06-17T11:00:00", "type": "file_refs", "root": str(root), "passed": True, "source": "codex"}]
    assert not module.evaluate_gate("review", root)["passed"], "stale evidence must not pass review gate"
    gstore["evidence"].append({"ts": "2026-06-17T12:45:00", "type": "file_refs", "root": str(root), "passed": True, "source": "codex"})
    assert module.evaluate_gate("review", root)["passed"]

    # --- handover gate: structured sign-out (substance + risks) ---
    gstore["kv"] = {"last_output": {"value": "x"}, "next_steps": {"value": "y"}}
    assert not module.evaluate_gate("handover", root)["passed"], "thin sign-out must fail"
    gstore["kv"] = {
        "last_output": {"value": "Implemented full gate policy with freshness and severity tiers."},
        "next_steps": {"value": "1. Review diff. 2. Run pytest. 3. Push to main after green CI."},
        "handover_notes": {"value": "No known risks; check_runs/evidence gained a source field."},
    }
    res = module.evaluate_gate("handover", root)
    assert res["passed"], res
    assert any(a["name"] == "token_log" for a in res["advisories"]), "empty token_log should warn, not block"

    # --- andon: a blocked gate records a high-severity lesson ---
    gstore["learning"] = []
    blocked = module.evaluate_gate("plan", root)  # kv has no acceptance_criteria now
    assert not blocked["passed"]
    module.andon_log(blocked, "cowork")
    assert gstore["learning"] and gstore["learning"][-1]["severity"] == "high"
    assert "andon" in gstore["learning"][-1]["tags"]


if __name__ == "__main__":
    raise SystemExit(main())
