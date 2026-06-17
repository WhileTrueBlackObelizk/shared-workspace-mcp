# Shared Workspace MCP

A small local MCP server for Cowork/Codex handover, code workspace inspection,
simple pipelines, and token usage tracking.

It gives multiple AI clients the same local context without stuffing every
session with long prompts.

## One-click install

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/WhileTrueBlackObelizk/shared-workspace-mcp/main/install.ps1 | iex"
```

Linux with systemd user services:

```bash
curl -fsSL https://raw.githubusercontent.com/WhileTrueBlackObelizk/shared-workspace-mcp/main/install.sh | bash
```

The server runs at:

```text
http://localhost:8765/sse
```

## What it stores

State is stored as UTF-8 JSON in:

```text
~/.claude/shared-workspace/
```

Files:

```text
kv.json
activity.json
file_events.json
pipelines.json
token_usage.json
```

## Core workflow

At the start of an agent session:

```text
workspace_dump
get_recent_activity n=10
get_file_events n=10
workspace_write key=session_owner value=codex source=codex
log_activity source=codex action=session_start detail="short intent"
```

For handover:

```text
workspace_write key=active_task value="..." source=codex
workspace_write key=current_plan value="..." source=codex
workspace_write key=context value="..." source=codex
workspace_write key=next_steps value="1. ..." source=codex
workspace_write key=handover_notes value="..." source=codex
log_activity source=codex action=handover detail="to cowork: ..."
```

## Code tools

Use these before free-form shell access:

```text
repo_status
git_diff
search_code
read_file
run_check
```

`run_check` supports only safe presets:

```text
git_status
python_compile
python_self_check
pytest
npm_test
npm_build
```

## Pipelines

Use a pipeline when a task has multiple phases or might be handed to another
client:

```text
pipeline_create name="Implement auth endpoint" source=codex
pipeline_next pipeline_id=implement-auth-endpoint
pipeline_update_step pipeline_id=implement-auth-endpoint step=plan status=done
pipeline_finish pipeline_id=implement-auth-endpoint note="Done and checked."
```

Default flow:

```text
intake -> plan -> implement -> test -> review -> handover
```

## Token efficiency

Log exact usage when available:

```text
token_log task="fix auth" agent=codex input_tokens=12000 output_tokens=2400
```

Or estimate locally:

```text
estimate_tokens text="..."
token_log task="handover" agent=codex text="summary text"
```

Summarize recent usage:

```text
token_summary n=20
```

## Manual start

Windows:

```cmd
start.bat
```

Linux/macOS:

```bash
./start.sh
```

## Validation

```bash
python server.py --self-check
```

## Architecture

See `ARCHITECTURE.md` for storage, tool groups, safety boundaries, and the
change policy.
