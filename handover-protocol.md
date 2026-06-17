# Handover Protocol - Cowork <-> Codex

This protocol defines how Cowork and Codex exchange context through the Shared
Workspace MCP. Keep it short, explicit, and machine-readable.

## 1. Session start

Run these first:

```text
workspace_dump
get_recent_activity n=10
get_file_events n=10
workspace_write key=session_owner value=[cowork|codex] source=[cowork|codex]
log_activity source=[cowork|codex] action=session_start detail=[short intent]
```

Then decide whether to continue existing work or start a new task.

## 2. Standard keys

Do not invent new handover keys unless both clients are updated.

| Key | Meaning | Writer |
| --- | --- | --- |
| `session_owner` | Active client: `cowork` or `codex` | each client on start |
| `current_plan` | Current plan as short phases/steps | planner |
| `active_task` | One-line task name | task starter |
| `context` | Compact background summary | whoever knows it |
| `blockers` | Current blocker and reason | blocked client |
| `next_steps` | Numbered concrete next steps | session ender |
| `last_output` | Last result / file / decision | deliverer |
| `handover_notes` | Free text for next client | handover sender |

## 3. Activity log

Use `log_activity` for:

| Event | action | detail |
| --- | --- | --- |
| Session start | `session_start` | short intent |
| Task start | `task_start` | task name |
| Decision | `decision` | decision and reason |
| Blocker | `blocked` | blocker and context |
| Deliverable | `delivered` | what and where |
| Session end | `session_end` | short summary |
| Handover | `handover` | target and task |

## 4. Pipeline convention

Use pipelines for tasks with more than five meaningful steps or handover risk.

Default steps:

```text
intake -> plan -> implement -> test -> review -> handover
```

Minimal flow:

```text
pipeline_create name=[task] source=[cowork|codex]
pipeline_next pipeline_id=[id]
pipeline_update_step pipeline_id=[id] step=[step] status=[pending|active|done|blocked] note=[short note]
pipeline_finish pipeline_id=[id] note=[summary]
```

Keep `current_plan` and `next_steps` human-readable even when a pipeline exists.

## 5. Code workspace tools

Prefer these MCP tools before free-form shell access:

```text
repo_status
git_diff
search_code
read_file
run_check
```

`run_check` only supports safe presets:

```text
git_status
python_compile
python_self_check
pytest
npm_test
npm_build
```

Paths must stay under the user's home directory.

## 6. Token efficiency

Use MCP memory for state. Keep the chat prompt small.

Best practice:

```text
search_code -> read_file only for relevant files
context_snapshot after substantial work
token_log for substantial tasks
token_summary during reviews
```

When exact provider usage is available, log exact numbers:

```text
token_log task=[task] agent=[agent] input_tokens=[n] output_tokens=[n]
```

When exact usage is unavailable, pass representative text and use the local
estimate:

```text
estimate_tokens text=[text]
token_log task=[task] agent=[agent] text=[representative context]
```

## 7. Goals, learning, and feedback

For outcome-heavy tasks, start a goal before implementation:

```text
goal_start objective=[goal] success_criteria=[done means] source=[cowork|codex]
goal_update goal_id=[id] status=[active|blocked|done] note=[short progress]
goal_complete goal_id=[id] outcome=[result]
```

When something fails, log the reusable lesson:

```text
learning_log_error task=[task] error=[what failed] cause=[why] fix=[what fixed it] lesson=[reuse next time]
learning_search query=[similar problem]
```

Occasionally ask for feedback, especially after handovers or confusing work:

```text
feedback_maybe source=[cowork|codex] topic=[handover-quality] chance=0.25
feedback_summary n=20
```

The feedback prompt returns local clickable links backed by `feedback.json`.

## 8. Handover flows

Cowork to Codex:

```text
workspace_write active_task [task] source=cowork
workspace_write current_plan [steps] source=cowork
workspace_write context [summary] source=cowork
workspace_write next_steps [1..N] source=cowork
workspace_write handover_notes [notes] source=cowork
workspace_write session_owner codex source=cowork
log_activity source=cowork action=handover detail="to codex: [task]"
```

Codex to Cowork:

```text
workspace_write last_output [result] source=codex
workspace_write next_steps [1..N] source=codex
workspace_write handover_notes [notes] source=codex
workspace_write session_owner cowork source=codex
log_activity source=codex action=handover detail="to cowork: [task]"
```

## 9. Cleanup

After a task is really complete:

```text
workspace_delete blockers
workspace_delete handover_notes
workspace_write active_task "-" source=[cowork|codex]
pipeline_finish pipeline_id=[id] note=[summary]
```

## 10. Before pushing repo changes

Run the cheap checks:

```text
python scripts/check_secrets.py
python server.py --self-check
python scripts/test_contract.py
```

If architecture, setup, tool contracts, learning, pipeline, token tracking, or
feedback behavior changed, update these before pushing:

```text
README.md
ARCHITECTURE.md
handover-protocol.md
```

Server URL:

```text
http://localhost:8765/sse
```
