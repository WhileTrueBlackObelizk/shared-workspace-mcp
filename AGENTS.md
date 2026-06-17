# Agent Instructions

Source of truth for how agents work in this repo. Two parts: an **operating
contract** (portable — copy to any project) and **this repo** (specifics).
Keep everything ponytail-small.

## Operating contract (portable)

### Decide vs. ask
Default to deciding. Only stop and ask for the rows on the right. If a question
has an obvious default, take it and say so in one line — don't block on it.

| Decide yourself (no question) | Ask first |
|---|---|
| Naming, layout, formatting that match existing patterns | Adding a new dependency |
| Which stdlib / already-installed dep to use (ponytail ladder) | Breaking a tool contract or storage schema |
| Writing the smallest test / self-check for new logic | Deleting data or files; anything irreversible |
| Reading, searching, running checks and gates | Security-relevant change (auth, network exposure, secrets) |
| Commit to a branch, open a PR, update required docs | Acceptance criteria genuinely ambiguous and undefaultable |

### Autonomy levels
How much the agent does unattended. The gates enforce it; this just names the
setting. Default: **L2**.

| Level | The agent may, unattended | Stops for sign-off at |
|---|---|---|
| **L0** suggest | Read, search, propose — change nothing | any write |
| **L1** branch | Edit, run checks/gates, commit to a branch, open a PR | merge / push to main |
| **L2** ship | L1 + merge/push to `main` once all hard gates pass | the "ask first" rows below |
| **L3** unattended | L2 + act on schedules (dependency triage, nightly drift) without a prompt | same |

No level ever skips the "Ask first" column (new dep, schema/contract break,
delete, deploy, security/network). Those always need a human.

### Workflow: gated by default
Run real work through the pipeline gates instead of self-declaring done:

1. **intake** — `learning_search` the task first, so past mistakes aren't repeated.
2. **plan** — pre-register `acceptance_criteria` (the definition of done).
3. **implement** — the smallest diff that works.
4. **test** — `run_check`; evidence must postdate your last edit (freshness).
5. **review** — `verify_file_refs` for any file:line claim you make.
6. **handover** — `context_snapshot` / `handover_prepare`: outcome, next steps, risks.

Advance with `gate_advance` (pass `source=`), not manual `pipeline_update_step`.
A blocked gate auto-logs a lesson — read it, fix the cause, don't retry blindly.

### Token discipline
- `search → read the slice → act`. Don't re-read what you've already seen.
- `context_snapshot` between sessions instead of re-deriving context.
- Cheap model for search/classify/summarize; strong model for reasoning/coding.
- Check `token_summary` to find the expensive pattern, then cut it — don't guess.

## This repo

Stack: Python (CI targets 3.12), MCP server, stdlib-first. No new dependencies
unless the stdlib cannot do the job safely. Storage: UTF-8 JSON under
`~/.claude/shared-workspace/`. Security: binds `127.0.0.1`; paths stay under
home; `run_check` is fixed presets, never arbitrary shell.

When changing behavior, architecture, setup, persistence, tool contracts, or
handover conventions:

1. Update `README.md` if user-facing setup or usage changes.
2. Update `ARCHITECTURE.md` if storage, tools, security boundaries, pipeline
   behavior, or token measurement changes.
3. Update `handover-protocol.md` if the Cowork/Codex workflow changes.
4. Run `python server.py --self-check` and `python scripts/test_contract.py`.
5. Run `python scripts/check_secrets.py` before every push.
6. Conventional commits (`feat:`, `fix:`, `docs:`, …).

Autonomy level here is **L2** (agent may push to `main` on green gates). Drop to
**L1** for PR-only sign-off, or raise to **L3** for unattended scheduled runs.
