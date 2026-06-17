# Architecture

Shared Workspace MCP is a small local MCP server for agent handover and code
workspace coordination.

## Runtime

- Transport: MCP over SSE at `http://localhost:8765/sse`
- Claude Code transport: stdio via `python server.py --stdio`
- Installers register the stdio server with `claude mcp add` when Claude Code
  is installed.
- Windows installer also writes the same stdio server into detected Claude
  Desktop/Cowork `claude_desktop_config.json` files.
- Windows installer also installs a minimal enabled Claude Extension wrapper in
  detected Claude Desktop/Cowork roots because local-agent Cowork loads dynamic
  extension MCP servers.
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
| `learning.json` | Errors, causes, fixes, and reusable lessons |
| `goals.json` | Goal state and progress history |
| `feedback.json` | Feedback prompts and user ratings |
| `check_runs.json` | Safe `run_check` history used by gates |
| `gate_results.json` | Gate evaluations and pass/fail state |
| `evidence.json` | Verified evidence such as file:line checks |

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

Handover:

```text
handover_prepare
handover_takeover
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

Learning:

```text
learning_log_error
learning_log_lesson
learning_search
learning_recent
```

Goals:

```text
goal_start
goal_update
goal_status
goal_complete
```

Feedback:

```text
feedback_maybe
feedback_log
feedback_summary
```

Drift gates:

```text
verify_file_refs
check_run_history
gate_policy
gate_check
gate_status
gate_advance
drift_report
```

HTTP routes:

```text
/sse
/messages/
/feedback
/health
```

## Security boundaries

- Server binds to `127.0.0.1`, not a public interface.
- File paths must stay under the user's home directory.
- `run_check` does not run arbitrary shell commands.
- Feedback links are local-only and write to `feedback.json`.
- Supported checks are fixed presets:

```text
git_status
python_compile
python_self_check
pytest
npm_test
npm_build
ruff
mypy
```

`ruff` and `mypy` run only if installed; otherwise the preset returns a
"not installed" message instead of failing.

## Repository standards

The repository has free guardrails that should stay in place:

- `scripts/check_secrets.py` scans tracked files for common credentials and
  high-entropy tokens.
- `.githooks/pre-commit` runs the secret scan and server self-check locally.
- `.github/workflows/ci.yml` runs secret scan, compile, self-check, contract
  tests, and installer smoke checks on GitHub Actions.
- `.github/dependabot.yml` keeps pip and GitHub Actions dependencies fresh.
- `.github/pull_request_template.md` requires checks and architecture/doc
  updates to be acknowledged.

No paid service is required for these checks on the public GitHub repository.

## Pipeline model

Pipelines are intentionally simple JSON state machines. The default flow is:

```text
intake -> plan -> implement -> test -> review -> handover
```

This is enough for Cowork/Codex coordination without owning a full job queue.

## Gate model

Gates are hardcoded on purpose. The first version is boring and inspectable:

| Step | Gate |
| --- | --- |
| `intake` | session owner, active task, recent task/session activity |
| `plan` | current plan and at least one goal |
| `implement` | git status works and there is diff or file-event evidence |
| `test` | recent successful `run_check` result |
| `review` | recent successful `verify_file_refs` evidence |
| `handover` | `last_output`, `next_steps`, and token usage exist |

`gate_check` evaluates a step. `gate_advance` evaluates the step and only then
marks it `done` in the pipeline. If the gate fails, the pipeline does not
advance and a `blocked` activity entry is written.

`verify_file_refs` follows a strict rule: it verifies that file:line coordinates
exist and optional snippets appear at those coordinates. It does not interpret
the code or validate the claim. This prevents hallucinated references while
keeping review responsibility explicit.

## Token measurement

When provider usage is available, callers should log exact values with
`token_log`.

When exact usage is unavailable, `estimate_tokens` uses a rough local estimate.
It is good enough for trends, not billing.

## Learning loop

The MCP does not "learn" by changing model weights. It learns operationally:

1. Record errors with `learning_log_error`.
2. Record reusable lessons with `learning_log_lesson`.
3. Search prior lessons before similar work with `learning_search`.
4. Keep goal state in `goals.json` so agents can orient around outcomes, not
   just steps.
5. Ask for occasional user feedback with `feedback_maybe`; the returned local
   URL records clickable feedback in `feedback.json`.

This keeps learning inspectable, editable, and portable.

## Change policy

Architecture changes must be reflected in this file and pushed to GitHub with
the code change. This prevents the live MCP behavior, README, and handover
rules from drifting apart.
