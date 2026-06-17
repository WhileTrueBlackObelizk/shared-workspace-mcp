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
    print("contract tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
