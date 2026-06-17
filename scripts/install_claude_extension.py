#!/usr/bin/env python3
"""Install a minimal Claude Desktop/Cowork extension wrapper."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


EXTENSION_ID = "ant.dir.whiletrueblackobelizk.shared-workspace-mcp"


def load_tools(server_path: Path) -> list[dict[str, str]]:
    spec = importlib.util.spec_from_file_location("shared_workspace_server", server_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return [{"name": tool.name, "description": tool.description} for tool in asyncio.run(module.list_tools())]


def build_manifest(python_path: Path, server_path: Path, tools: list[dict[str, str]]) -> dict:
    return {
        "manifest_version": "0.3",
        "name": "shared-workspace-mcp",
        "display_name": "Shared Workspace MCP",
        "version": "0.1.0",
        "description": "Local-first memory, pipelines, learning, feedback, gates, and safe code tools for agent handover.",
        "long_description": (
            "Shared Workspace MCP gives Claude Cowork, Claude Code, Codex, and other agents a shared local "
            "operating memory: current task, plan, activity, file events, goals, gates, token logs, and lessons learned."
        ),
        "author": {"name": "WhileTrueBlackObelizk"},
        "repository": {
            "type": "git",
            "url": "https://github.com/WhileTrueBlackObelizk/shared-workspace-mcp",
        },
        "homepage": "https://github.com/WhileTrueBlackObelizk/shared-workspace-mcp",
        "documentation": "https://github.com/WhileTrueBlackObelizk/shared-workspace-mcp#readme",
        "server": {
            "type": "python",
            "entry_point": "server.py",
            "mcp_config": {
                "command": str(python_path),
                "args": [str(server_path), "--stdio"],
                "env": {},
            },
        },
        "tools": tools,
        "keywords": ["mcp", "agents", "handover", "memory", "pipeline", "coding"],
        "license": "MIT",
        "compatibility": {"platforms": ["win32"], "runtimes": {"python": ">=3.12"}},
        "user_config": {},
    }


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def install_extension(claude_root: Path, python_path: Path, server_path: Path) -> dict:
    if not python_path.exists():
        raise FileNotFoundError(python_path)
    if not server_path.exists():
        raise FileNotFoundError(server_path)

    tools = load_tools(server_path)
    manifest = build_manifest(python_path, server_path, tools)
    extension_dir = claude_root / "Claude Extensions" / EXTENSION_ID
    settings_dir = claude_root / "Claude Extensions Settings"
    installations_path = claude_root / "extensions-installations.json"
    manifest_path = extension_dir / "manifest.json"

    write_json(manifest_path, manifest)
    (extension_dir / "README.md").write_text(
        "# Shared Workspace MCP\n\nLocal Claude Extension wrapper for the shared-workspace MCP server.\n",
        encoding="utf-8",
    )
    write_json(settings_dir / f"{EXTENSION_ID}.json", {"isEnabled": True})

    if installations_path.exists():
        backup = installations_path.with_suffix(".json.bak-shared-workspace")
        if not backup.exists():
            backup.write_text(installations_path.read_text(encoding="utf-8-sig"), encoding="utf-8")

    installations = read_json(installations_path, {"extensions": {}})
    installations.setdefault("extensions", {})[EXTENSION_ID] = {
        "id": EXTENSION_ID,
        "version": manifest["version"],
        "hash": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "installedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "manifest": manifest,
        "signatureInfo": {"status": "unsigned"},
        "source": "local",
    }
    write_json(installations_path, installations)

    return {
        "extension_id": EXTENSION_ID,
        "manifest": str(manifest_path),
        "settings": str(settings_dir / f"{EXTENSION_ID}.json"),
        "tools": len(tools),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--server", required=True, type=Path)
    args = parser.parse_args()

    result = install_extension(
        args.claude_root.resolve(),
        args.python.resolve(),
        args.server.resolve(),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
