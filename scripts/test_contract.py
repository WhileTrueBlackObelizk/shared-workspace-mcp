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

    print("contract tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
