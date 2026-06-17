# Architecture

Shared Workspace MCP is a small local MCP server for agent handover and code
workspace coordination.

## Runtime

- Transport: MCP over SSE at `http://localhost:8765/sse`
- Host: `127.0.0.1`
- Entry point: `server.py`
- Storage: UTF-8 JSON under `~/.claude/shared-workspace/`
- Watch path: `~/Claude/Projects/Skills`

## Storage files

| File | Purpose |
| --- | --- |
| `kv.json` | Shared handover keys such as `active_task`, `context`, `next_steps` |
| `activity.json` | Append-only activity ring buffer |
| `file_events.json` | Persisted file watcher ring buffer |
| `pipelines.json` | Simple pipeline state |
| `token_usage.json` | Exact or estimated token usage log |

Writes are atomic: data is written to a temporary JSON file, then replaced.

## Tool groups

Workspace memory:

```text
workspace_write
workspace_read
workspace_list
workspace_dump
workspace_delete
```

Activity and file events:

```text
log_activity
get_recent_activity
get_file_events
```

Code workspace:

```text
repo_status
git_diff
search_code
read_file
run_check
```

Pipeline state:

```text
pipeline_create
pipeline_status
pipeline_next
pipeline_update_step
pipeline_finish
```

Token efficiency:

```text
estimate_tokens
token_log
token_summary
context_snapshot
```

## Security boundaries

- Server binds to `127.0.0.1`, not a public interface.
- File paths must stay under the user's home directory.
- `run_check` does not run arbitrary shell commands.
- Supported checks are fixed presets:

```text
git_status
python_compile
python_self_check
pytest
npm_test
npm_build
```

## Pipeline model

Pipelines are intentionally simple JSON state machines. The default flow is:

```text
intake -> plan -> implement -> test -> review -> handover
```

This is enough for Cowork/Codex coordination without owning a full job queue.

## Token measurement

When provider usage is available, callers should log exact values with
`token_log`.

When exact usage is unavailable, `estimate_tokens` uses a rough local estimate.
It is good enough for trends, not billing.

## Change policy

Architecture changes must be reflected in this file and pushed to GitHub with
the code change. This prevents the live MCP behavior, README, and handover
rules from drifting apart.
