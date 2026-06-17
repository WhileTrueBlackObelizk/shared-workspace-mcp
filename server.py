#!/usr/bin/env python3
"""
Shared Workspace MCP Server.

Tools for Cowork/Codex handover, code workspace inspection, simple pipelines,
and token usage logging. Storage is UTF-8 JSON under:
  ~/.claude/shared-workspace/
"""

from __future__ import annotations

import json
import logging
import random
import re
import subprocess
import sys
import threading
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


PORT = 8765
HOME = Path.home().resolve()
STORAGE_DIR = HOME / ".claude" / "shared-workspace"
KV_FILE = STORAGE_DIR / "kv.json"
LOG_FILE = STORAGE_DIR / "activity.json"
FILE_EVENTS_FILE = STORAGE_DIR / "file_events.json"
PIPELINES_FILE = STORAGE_DIR / "pipelines.json"
TOKEN_LOG_FILE = STORAGE_DIR / "token_usage.json"
LEARNING_FILE = STORAGE_DIR / "learning.json"
GOALS_FILE = STORAGE_DIR / "goals.json"
FEEDBACK_FILE = STORAGE_DIR / "feedback.json"
CHECK_RUNS_FILE = STORAGE_DIR / "check_runs.json"
GATE_RESULTS_FILE = STORAGE_DIR / "gate_results.json"
EVIDENCE_FILE = STORAGE_DIR / "evidence.json"
WATCH_PATH = HOME / "Claude" / "Projects" / "Skills"
MAX_LOG = 500
MAX_FILE_EVENTS = 200
MAX_TOKEN_LOG = 1000
MAX_LEARNING = 1000
MAX_FEEDBACK = 1000
MAX_CHECK_RUNS = 1000
MAX_GATE_RESULTS = 1000
MAX_EVIDENCE = 1000
DEFAULT_PIPELINE_STEPS = ["intake", "plan", "implement", "test", "review", "handover"]
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
# Gate policy. Cross-disciplinary, not just "industry-expected" presence checks:
#  - Chain-of-custody / sample integrity (forensics, clinical labs) + cache
#    invalidation (CS): evidence only counts if it POSTDATES the last relevant
#    change. A green test from before the last edit no longer certifies the code.
#  - Minimum Equipment List (aviation): each item is [hard] (blocks) or
#    [advisory] (warns but lets the line run).
#  - Four-eyes / read-back (aviation CRM) + segregation of duties (accounting):
#    the actor advancing a gate should not be the only source of its evidence.
#  - Pre-registration (open science): "done" (acceptance_criteria) is fixed in
#    the plan step, before implement, so the goalposts cannot move.
#  - Structured sign-out / surgical time-out (medicine, SBAR): a handover must
#    state outcome, next steps, and known risks explicitly.
GATE_POLICY = {
    "intake": [
        "[hard] session_owner exists",
        "[hard] active_task exists and is not '-'",
        "[hard] recent task_start or session_start activity exists",
    ],
    "plan": [
        "[hard] current_plan has content (>=20 chars)",
        "[hard] at least one goal exists",
        "[hard] acceptance_criteria pre-registered (>=20 chars)",
    ],
    "implement": [
        "[hard] repo_status can run",
        "[hard] relevant file event or git diff exists",
    ],
    "test": [
        "[hard] a successful check exists AND postdates the last relevant change (freshness)",
        "[advisory] certifying check ran under a different source than the advancing actor",
    ],
    "review": [
        "[hard] file:line evidence passed AND postdates the last relevant change",
        "[advisory] evidence verified by a different source than the advancing actor (four-eyes)",
        "[advisory] acceptance_criteria still on record to review against",
    ],
    "handover": [
        "[hard] last_output is substantial (>=30 chars)",
        "[hard] next_steps is substantial (>=30 chars)",
        "[hard] handover_notes/blockers states risks or 'none' (structured sign-out)",
        "[advisory] recent token_log exists",
    ],
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_file_lock = threading.Lock()


def now() -> str:
    return datetime.now().isoformat()


def _ensure() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    _ensure()
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + f".bad-{datetime.now():%Y%m%d%H%M%S}")
        path.replace(backup)
        logger.warning("Invalid JSON moved to %s", backup)
        return default


def _write_json(path: Path, data: Any) -> None:
    _ensure()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_kv() -> dict[str, Any]:
    return _read_json(KV_FILE, {})


def save_kv(data: dict[str, Any]) -> None:
    _write_json(KV_FILE, data)


def load_log() -> list[dict[str, Any]]:
    return _read_json(LOG_FILE, [])


def save_log(entries: list[dict[str, Any]]) -> None:
    _write_json(LOG_FILE, entries[-MAX_LOG:])


def append_log(source: str, action: str, detail: str = "") -> None:
    entries = load_log()
    entries.append({"ts": now(), "source": source, "action": action, "detail": detail})
    save_log(entries)


def load_file_events() -> list[dict[str, str]]:
    return _read_json(FILE_EVENTS_FILE, [])


def save_file_events(events: list[dict[str, str]]) -> None:
    _write_json(FILE_EVENTS_FILE, events[-MAX_FILE_EVENTS:])


def append_file_event(event_type: str, path: str) -> None:
    if should_skip(Path(path)):
        return
    with _file_lock:
        events = load_file_events()
        events.append({"ts": now(), "type": event_type, "path": path})
        save_file_events(events)


def load_pipelines() -> dict[str, Any]:
    return _read_json(PIPELINES_FILE, {})


def save_pipelines(data: dict[str, Any]) -> None:
    _write_json(PIPELINES_FILE, data)


def load_token_log() -> list[dict[str, Any]]:
    return _read_json(TOKEN_LOG_FILE, [])


def save_token_log(entries: list[dict[str, Any]]) -> None:
    _write_json(TOKEN_LOG_FILE, entries[-MAX_TOKEN_LOG:])


def load_learning() -> list[dict[str, Any]]:
    return _read_json(LEARNING_FILE, [])


def save_learning(entries: list[dict[str, Any]]) -> None:
    _write_json(LEARNING_FILE, entries[-MAX_LEARNING:])


def load_goals() -> dict[str, Any]:
    return _read_json(GOALS_FILE, {})


def save_goals(data: dict[str, Any]) -> None:
    _write_json(GOALS_FILE, data)


def load_feedback() -> list[dict[str, Any]]:
    return _read_json(FEEDBACK_FILE, [])


def save_feedback(entries: list[dict[str, Any]]) -> None:
    _write_json(FEEDBACK_FILE, entries[-MAX_FEEDBACK:])


def load_check_runs() -> list[dict[str, Any]]:
    return _read_json(CHECK_RUNS_FILE, [])


def save_check_runs(entries: list[dict[str, Any]]) -> None:
    _write_json(CHECK_RUNS_FILE, entries[-MAX_CHECK_RUNS:])


def load_gate_results() -> list[dict[str, Any]]:
    return _read_json(GATE_RESULTS_FILE, [])


def save_gate_results(entries: list[dict[str, Any]]) -> None:
    _write_json(GATE_RESULTS_FILE, entries[-MAX_GATE_RESULTS:])


def load_evidence() -> list[dict[str, Any]]:
    return _read_json(EVIDENCE_FILE, [])


def save_evidence(entries: list[dict[str, Any]]) -> None:
    _write_json(EVIDENCE_FILE, entries[-MAX_EVIDENCE:])


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def path_under_home(value: str | None, default: Path | None = None) -> Path:
    raw = Path(value).expanduser() if value else (default or WATCH_PATH)
    path = raw if raw.is_absolute() else (default or WATCH_PATH) / raw
    resolved = path.resolve(strict=False)
    if not _is_under(resolved, HOME):
        raise ValueError(f"Path must stay under {HOME}")
    return resolved


def text_response(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def clipped(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[truncated: {len(text) - max_chars} chars omitted]"


def run_cmd(args: list[str], cwd: Path, timeout: int = 60, max_chars: int = 12000) -> str:
    result = run_cmd_result(args, cwd, timeout, max_chars)
    return result["text"]


def run_cmd_result(args: list[str], cwd: Path, timeout: int = 60, max_chars: int = 12000) -> dict[str, Any]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        shell=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    text = clipped(f"$ {' '.join(args)}\nexit={proc.returncode}\n{output}".strip(), max_chars)
    return {"args": args, "cwd": str(cwd), "exit_code": proc.returncode, "output": output, "text": text, "ts": now()}


def append_check_run(check: str, root: Path, result: dict[str, Any], source: str = "unknown") -> dict[str, Any]:
    entries = load_check_runs()
    entry = {
        "ts": result["ts"],
        "check": check,
        "root": str(root),
        "exit_code": result["exit_code"],
        "passed": result["exit_code"] == 0,
        "command": result["args"],
        "source": source,
    }
    entries.append(entry)
    save_check_runs(entries)
    return entry


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # ponytail: rough local estimate; replace with provider usage when available.
    words = len(re.findall(r"\S+", text))
    chars = max(1, len(text))
    return max(1, round(max(words * 1.35, chars / 4)))


def slug(value: str, fallback: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-") or fallback


def make_pipeline(name: str, steps: list[str], source: str) -> dict[str, Any]:
    return {
        "id": slug(name, f"pipeline-{int(datetime.now().timestamp())}"),
        "name": name,
        "created_at": now(),
        "updated_at": now(),
        "by": source,
        "status": "active",
        "current_step": steps[0] if steps else "",
        "steps": [{"name": s, "status": "pending", "note": "", "updated_at": ""} for s in steps],
    }


def update_pipeline_step(pipeline: dict[str, Any], step: str, status: str, note: str) -> dict[str, Any]:
    found = False
    for item in pipeline["steps"]:
        if item["name"] == step:
            item.update({"status": status, "note": note, "updated_at": now()})
            found = True
            break
    if not found:
        raise ValueError(f"Unknown step: {step}")
    next_pending = next((s["name"] for s in pipeline["steps"] if s["status"] == "pending"), "")
    pipeline["current_step"] = next_pending
    pipeline["status"] = "done" if not next_pending else "active"
    pipeline["updated_at"] = now()
    return pipeline


def make_goal(objective: str, source: str, success_criteria: str = "", pipeline_id: str = "") -> dict[str, Any]:
    return {
        "id": slug(objective, f"goal-{int(datetime.now().timestamp())}"),
        "objective": objective,
        "success_criteria": success_criteria,
        "pipeline_id": pipeline_id,
        "status": "active",
        "created_at": now(),
        "updated_at": now(),
        "by": source,
        "history": [{"ts": now(), "source": source, "status": "active", "note": "started"}],
    }


def append_learning(entry: dict[str, Any]) -> dict[str, Any]:
    entries = load_learning()
    entry = {"ts": now(), **entry}
    entries.append(entry)
    save_learning(entries)
    append_log(entry.get("source", "unknown"), f"learning_{entry.get('type', 'note')}", entry.get("lesson") or entry.get("error", ""))
    return entry


def record_feedback(prompt_id: str, rating: str, note: str = "", source: str = "user") -> dict[str, Any]:
    entries = load_feedback()
    entry = next((e for e in entries if e.get("id") == prompt_id), None)
    if entry is None:
        entry = {"id": prompt_id or f"feedback-{int(datetime.now().timestamp())}", "ts": now(), "source": source}
        entries.append(entry)
    entry.update({
        "answered_at": now(),
        "status": "answered",
        "rating": rating,
        "note": note,
        "source": source,
    })
    save_feedback(entries)
    append_log(source, "feedback", f"{entry['id']}={rating}")
    return entry


def feedback_url(prompt_id: str, rating: str = "") -> str:
    base = f"http://localhost:{PORT}/feedback?id={quote(prompt_id)}"
    return f"{base}&rating={quote(rating)}" if rating else base


COORD_RE = re.compile(
    r"(?<![\w:/.-])(?P<file>(?:[A-Za-z]:[\\/])?(?:[\w.-]+[\\/])*[\w.-]+\.[A-Za-z0-9_+-]+):(?P<start>\d+)(?:-(?P<end>\d+))?"
)


def parse_file_refs(text: str) -> list[dict[str, Any]]:
    refs = []
    for match in COORD_RE.finditer(text):
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        refs.append({"raw": match.group(0), "file": match.group("file"), "start": start, "end": end})
    return refs


def verify_refs(text: str, root: Path, snippet: str = "", source: str = "unknown") -> dict[str, Any]:
    refs = parse_file_refs(text)
    results = []
    for ref in refs:
        file_path = Path(ref["file"])
        path = file_path.resolve(strict=False) if file_path.is_absolute() else (root / file_path).resolve(strict=False)
        item = {**ref, "path": str(path), "exists": path.exists(), "line_range_ok": False, "snippet_ok": not snippet}
        if item["exists"] and path.is_file() and _is_under(path, HOME):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            item["line_count"] = len(lines)
            item["line_range_ok"] = 1 <= ref["start"] <= ref["end"] <= len(lines)
            if snippet and item["line_range_ok"]:
                selected = "\n".join(lines[ref["start"] - 1:ref["end"]])
                item["snippet_ok"] = snippet in selected
        results.append(item)
    passed = bool(results) and all(r["exists"] and r["line_range_ok"] and r["snippet_ok"] for r in results)
    return {"ts": now(), "type": "file_refs", "root": str(root), "source": source, "passed": passed, "refs": results}


def append_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    entries = load_evidence()
    entries.append(entry)
    save_evidence(entries)
    return entry


def append_gate_result(entry: dict[str, Any]) -> dict[str, Any]:
    entries = load_gate_results()
    entries.append(entry)
    save_gate_results(entries)
    return entry


def recent_activity_actions(limit: int = 50) -> set[str]:
    return {entry.get("action", "") for entry in load_log()[-limit:]}


def recent_successful_check(root: Path) -> dict[str, Any] | None:
    root_text = str(root)
    for entry in reversed(load_check_runs()):
        if entry.get("root") == root_text and entry.get("passed"):
            return entry
    return None


def recent_verified_evidence(root: Path) -> dict[str, Any] | None:
    root_text = str(root)
    for entry in reversed(load_evidence()):
        if entry.get("type") == "file_refs" and entry.get("root") == root_text and entry.get("passed"):
            return entry
    return None


def git_dirty(root: Path) -> bool:
    try:
        result = run_cmd_result(["git", "status", "--short"], root, 10, 12000)
    except Exception:
        return False
    return result["exit_code"] == 0 and bool(result["output"].strip())


def relevant_file_event(root: Path) -> bool:
    root_text = str(root)
    return any(event.get("path", "").startswith(root_text) for event in load_file_events()[-50:])


def latest_relevant_event_ts(root: Path) -> str:
    """Timestamp of the most recent file change under root (chain-of-custody boundary)."""
    root_text = str(root)
    stamps = [e.get("ts", "") for e in load_file_events() if e.get("path", "").startswith(root_text)]
    return max(stamps) if stamps else ""


def is_fresh(entry: dict[str, Any], root: Path) -> bool:
    """Evidence is fresh only if it postdates the last relevant change.

    If nothing changed under root (no boundary), any evidence is trivially fresh.
    ISO timestamps share one format, so lexical comparison matches chronological.
    """
    boundary = latest_relevant_event_ts(root)
    return (not boundary) or (entry.get("ts", "") >= boundary)


def gate_checks(step: str, root: Path, actor: str = "") -> list[dict[str, Any]]:
    """Evaluate gate items. Each item carries a severity: 'hard' blocks advance,
    'advisory' only warns. `actor` is the source trying to advance, used for the
    four-eyes / segregation-of-duties advisories."""
    kv = load_kv()
    actions = recent_activity_actions()
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str, severity: str = "hard") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail, "severity": severity})

    if step == "intake":
        add("session_owner", bool(kv.get("session_owner")), "session_owner exists")
        active = kv.get("active_task", {}).get("value", "")
        add("active_task", bool(active and active != "-"), "active_task exists and is not '-'")
        add("session_activity", bool(actions & {"task_start", "session_start"}), "task_start/session_start activity exists")
    elif step == "plan":
        plan = kv.get("current_plan", {}).get("value", "")
        add("current_plan", len(plan.strip()) >= 20, "current_plan has content (>=20 chars)")
        add("goal", bool(load_goals()), "at least one goal exists")
        # Pre-registration (open science): fix the definition of done before implement.
        criteria = kv.get("acceptance_criteria", {}).get("value", "")
        add("acceptance_criteria", len(criteria.strip()) >= 20, "acceptance_criteria pre-registered (>=20 chars)")
    elif step == "implement":
        status = run_cmd_result(["git", "status", "--short"], root, 10, 12000)
        append_check_run("git_status", root, status, "system")
        add("repo_status", status["exit_code"] == 0, "git status can run")
        add("scope_evidence", bool(status["output"].strip()) or relevant_file_event(root), "git diff/status or file event exists")
    elif step == "test":
        check = recent_successful_check(root)
        fresh = bool(check) and is_fresh(check, root)
        if not check:
            detail = "no successful check recorded for root"
        elif not fresh:
            detail = f"stale: last check ({check.get('check')} @ {check.get('ts')}) predates last change @ {latest_relevant_event_ts(root)}"
        else:
            detail = f"fresh successful check: {check.get('check')} @ {check.get('ts')}"
        add("fresh_successful_check", fresh, detail)
        if check and actor:
            add("independent_check", check.get("source", "unknown") != actor,
                f"check source '{check.get('source', 'unknown')}' differs from advancing actor '{actor}'", "advisory")
    elif step == "review":
        evidence = recent_verified_evidence(root)
        fresh = bool(evidence) and is_fresh(evidence, root)
        if not evidence:
            detail = "no passing verify_file_refs evidence for root"
        elif not fresh:
            detail = f"stale: evidence @ {evidence.get('ts')} predates last change @ {latest_relevant_event_ts(root)}"
        else:
            detail = f"fresh file:line evidence @ {evidence.get('ts')}"
        add("fresh_file_line_evidence", fresh, detail)
        if evidence and actor:
            add("independent_review", evidence.get("source", "unknown") != actor,
                f"evidence source '{evidence.get('source', 'unknown')}' differs from advancing actor '{actor}'", "advisory")
        add("acceptance_criteria_present", bool(kv.get("acceptance_criteria", {}).get("value", "").strip()),
            "acceptance_criteria still on record to review against", "advisory")
    elif step == "handover":
        # Structured sign-out / surgical time-out: outcome, next steps, and risks.
        last_output = kv.get("last_output", {}).get("value", "")
        next_steps = kv.get("next_steps", {}).get("value", "")
        risk = kv.get("handover_notes", {}).get("value", "") or kv.get("blockers", {}).get("value", "")
        add("last_output", len(last_output.strip()) >= 30, "last_output is substantial (>=30 chars)")
        add("next_steps", len(next_steps.strip()) >= 30, "next_steps is substantial (>=30 chars)")
        add("risk_signout", bool(risk.strip()), "handover_notes/blockers states risks or 'none' (sign-out)")
        add("token_log", bool(load_token_log()), "token_log has entries", "advisory")
    else:
        raise ValueError(f"Unknown gate step: {step}")
    return checks


def evaluate_gate(step: str, root: Path, pipeline_id: str = "", actor: str = "") -> dict[str, Any]:
    checks = gate_checks(step, root, actor)
    hard = [c for c in checks if c.get("severity", "hard") == "hard"]
    advisory_fails = [c for c in checks if c.get("severity") == "advisory" and not c["passed"]]
    result = {
        "ts": now(),
        "pipeline_id": pipeline_id,
        "step": step,
        "root": str(root),
        "actor": actor,
        "passed": all(c["passed"] for c in hard),
        "checks": checks,
        "advisories": [{"name": c["name"], "detail": c["detail"]} for c in advisory_fails],
        "policy": GATE_POLICY.get(step, []),
    }
    append_gate_result(result)
    return result


def andon_log(result: dict[str, Any], source: str) -> None:
    """Andon cord / Jidoka: a blocked gate stops the line and records the defect
    with its root cause, so failures become lessons instead of silent retries."""
    failed = [c for c in result["checks"] if c.get("severity", "hard") == "hard" and not c["passed"]]
    if not failed:
        return
    append_learning({
        "type": "error",
        "source": source or "system",
        "task": f"gate:{result['step']}",
        "error": f"gate {result['step']} blocked: {', '.join(c['name'] for c in failed)}",
        "cause": "; ".join(c["detail"] for c in failed),
        "fix": "",
        "lesson": "",
        "severity": "high",
        "tags": ["gate", "andon", result["step"]],
    })


class _Handler(FileSystemEventHandler):
    def _push(self, event_type: str, path: str, dest_path: str = "") -> None:
        if should_skip(Path(path)) or (dest_path and should_skip(Path(dest_path))):
            return
        append_file_event(event_type, f"{path} -> {dest_path}" if dest_path else path)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._push("created", event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._push("modified", event.src_path)

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._push("deleted", event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._push("moved", event.src_path, event.dest_path)


def start_watcher() -> None:
    if not WATCH_PATH.exists():
        logger.warning("Watch path not found: %s; file watcher disabled", WATCH_PATH)
        return
    observer = Observer()
    observer.schedule(_Handler(), str(WATCH_PATH), recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("Watching: %s", WATCH_PATH)


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def repo_root(root_arg: str | None) -> Path:
    root = path_under_home(root_arg, WATCH_PATH)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Root does not exist or is not a directory: {root}")
    return root


server = Server("shared-workspace")


def tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": properties, "required": required or []},
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        tool("workspace_write", "Write a value into shared workspace memory.", {
            "key": {"type": "string"},
            "value": {"type": "string"},
            "source": {"type": "string", "default": "unknown"},
        }, ["key", "value"]),
        tool("workspace_read", "Read a value from shared workspace memory.", {"key": {"type": "string"}}, ["key"]),
        tool("workspace_list", "List all shared workspace keys.", {}),
        tool("workspace_dump", "Dump all shared workspace values.", {}),
        tool("workspace_delete", "Delete a shared workspace key.", {"key": {"type": "string"}}, ["key"]),
        tool("log_activity", "Append an activity entry for handover.", {
            "source": {"type": "string"},
            "action": {"type": "string"},
            "detail": {"type": "string", "default": ""},
        }, ["source", "action"]),
        tool("get_recent_activity", "Read recent activity entries.", {"n": {"type": "integer", "default": 20}}),
        tool("get_file_events", f"Read recent persisted file events under {WATCH_PATH}.", {
            "n": {"type": "integer", "default": 20},
            "filter": {"type": "string", "default": ""},
        }),
        tool("handover_prepare", "Prepare a complete Cowork/Codex handover.", {
            "target": {"type": "string", "description": "codex or cowork"},
            "reason": {"type": "string"},
            "last_output": {"type": "string"},
            "next_steps": {"type": "string"},
            "notes": {"type": "string", "default": ""},
            "source": {"type": "string", "default": "unknown"},
        }, ["target", "reason", "last_output", "next_steps"]),
        tool("handover_takeover", "Read workspace, activity, and file events for takeover.", {
            "agent": {"type": "string", "description": "codex or cowork"},
            "n": {"type": "integer", "default": 10},
        }, ["agent"]),
        tool("repo_status", "Show git branch and short status for a repo under the user home.", {
            "root": {"type": "string", "default": str(WATCH_PATH)},
        }),
        tool("git_diff", "Show git diff stats and diff for a repo under the user home.", {
            "root": {"type": "string", "default": str(WATCH_PATH)},
            "max_chars": {"type": "integer", "default": 12000},
        }),
        tool("search_code", "Search text files under a repo/root without reading everything.", {
            "root": {"type": "string", "default": str(WATCH_PATH)},
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 50},
        }, ["query"]),
        tool("read_file", "Read a bounded slice of a file under the user home.", {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "default": 1},
            "end_line": {"type": "integer", "default": 200},
            "max_chars": {"type": "integer", "default": 12000},
        }, ["path"]),
        tool("run_check", "Run a safe preset check, not an arbitrary shell command.", {
            "root": {"type": "string", "default": str(WATCH_PATH)},
            "check": {"type": "string", "description": "git_status, python_compile, python_self_check, pytest, npm_test, npm_build"},
            "path": {"type": "string", "default": ""},
            "timeout": {"type": "integer", "default": 60},
            "max_chars": {"type": "integer", "default": 12000},
            "source": {"type": "string", "default": "unknown", "description": "actor running the check (for four-eyes gates)"},
        }, ["check"]),
        tool("pipeline_create", "Create a simple task pipeline.", {
            "name": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}, "default": DEFAULT_PIPELINE_STEPS},
            "source": {"type": "string", "default": "unknown"},
        }, ["name"]),
        tool("pipeline_status", "Show one pipeline or all pipelines.", {
            "pipeline_id": {"type": "string", "default": ""},
        }),
        tool("pipeline_next", "Show the current pipeline step.", {"pipeline_id": {"type": "string"}}, ["pipeline_id"]),
        tool("pipeline_update_step", "Update one pipeline step.", {
            "pipeline_id": {"type": "string"},
            "step": {"type": "string"},
            "status": {"type": "string", "description": "pending, active, done, blocked"},
            "note": {"type": "string", "default": ""},
            "source": {"type": "string", "default": "unknown"},
        }, ["pipeline_id", "step", "status"]),
        tool("pipeline_finish", "Mark a pipeline done.", {
            "pipeline_id": {"type": "string"},
            "note": {"type": "string", "default": ""},
            "source": {"type": "string", "default": "unknown"},
        }, ["pipeline_id"]),
        tool("estimate_tokens", "Estimate token count for text locally.", {"text": {"type": "string"}}, ["text"]),
        tool("token_log", "Log exact or estimated token usage for a task.", {
            "task": {"type": "string"},
            "agent": {"type": "string", "default": "unknown"},
            "input_tokens": {"type": "integer", "default": 0},
            "output_tokens": {"type": "integer", "default": 0},
            "text": {"type": "string", "default": ""},
            "files_read": {"type": "integer", "default": 0},
            "commands_run": {"type": "integer", "default": 0},
            "result": {"type": "string", "default": ""},
        }, ["task"]),
        tool("token_summary", "Summarize recent token usage.", {"n": {"type": "integer", "default": 20}}),
        tool("context_snapshot", "Write compact handover keys and log estimated snapshot size.", {
            "source": {"type": "string", "default": "unknown"},
            "summary": {"type": "string"},
            "next_steps": {"type": "string", "default": ""},
            "blockers": {"type": "string", "default": ""},
        }, ["summary"]),
        tool("learning_log_error", "Record an error and the lesson learned from it.", {
            "source": {"type": "string", "default": "unknown"},
            "task": {"type": "string", "default": ""},
            "error": {"type": "string"},
            "cause": {"type": "string", "default": ""},
            "fix": {"type": "string", "default": ""},
            "lesson": {"type": "string", "default": ""},
            "severity": {"type": "string", "default": "medium"},
            "tags": {"type": "array", "items": {"type": "string"}, "default": []},
        }, ["error"]),
        tool("learning_log_lesson", "Record a reusable lesson without an error.", {
            "source": {"type": "string", "default": "unknown"},
            "task": {"type": "string", "default": ""},
            "lesson": {"type": "string"},
            "trigger": {"type": "string", "default": ""},
            "tags": {"type": "array", "items": {"type": "string"}, "default": []},
        }, ["lesson"]),
        tool("learning_search", "Search recorded lessons/errors.", {
            "query": {"type": "string"},
            "n": {"type": "integer", "default": 10},
        }, ["query"]),
        tool("learning_recent", "Read recent lessons/errors.", {"n": {"type": "integer", "default": 10}}),
        tool("goal_start", "Start a goal with success criteria and optional pipeline link.", {
            "objective": {"type": "string"},
            "success_criteria": {"type": "string", "default": ""},
            "pipeline_id": {"type": "string", "default": ""},
            "source": {"type": "string", "default": "unknown"},
        }, ["objective"]),
        tool("goal_update", "Append progress to a goal.", {
            "goal_id": {"type": "string"},
            "status": {"type": "string", "description": "active, blocked, done"},
            "note": {"type": "string", "default": ""},
            "source": {"type": "string", "default": "unknown"},
        }, ["goal_id", "status"]),
        tool("goal_status", "Show one goal or all goals.", {
            "goal_id": {"type": "string", "default": ""},
        }),
        tool("goal_complete", "Complete a goal and record the outcome.", {
            "goal_id": {"type": "string"},
            "outcome": {"type": "string", "default": ""},
            "source": {"type": "string", "default": "unknown"},
        }, ["goal_id"]),
        tool("feedback_maybe", "Maybe ask the user for feedback with clickable local links.", {
            "source": {"type": "string", "default": "unknown"},
            "topic": {"type": "string", "default": "agent-session"},
            "question": {"type": "string", "default": "War diese Agent-Antwort hilfreich?"},
            "chance": {"type": "number", "default": 0.25},
            "force": {"type": "boolean", "default": False},
            "min_hours": {"type": "number", "default": 8},
        }),
        tool("feedback_log", "Record feedback directly without the browser page.", {
            "source": {"type": "string", "default": "user"},
            "topic": {"type": "string", "default": "agent-session"},
            "rating": {"type": "string", "description": "good, mixed, bad"},
            "note": {"type": "string", "default": ""},
        }, ["rating"]),
        tool("feedback_summary", "Summarize recent feedback.", {"n": {"type": "integer", "default": 20}}),
        tool("verify_file_refs", "Verify file:line coordinates exist; does not judge interpretation.", {
            "root": {"type": "string", "default": str(WATCH_PATH)},
            "text": {"type": "string"},
            "snippet": {"type": "string", "default": ""},
            "source": {"type": "string", "default": "unknown", "description": "actor verifying (for four-eyes gates)"},
        }, ["text"]),
        tool("check_run_history", "Show recent safe check results.", {
            "root": {"type": "string", "default": ""},
            "n": {"type": "integer", "default": 20},
        }),
        tool("gate_policy", "Show hardcoded pipeline gate policy.", {
            "step": {"type": "string", "default": ""},
        }),
        tool("gate_check", "Evaluate hard + advisory gates for one pipeline step.", {
            "step": {"type": "string"},
            "pipeline_id": {"type": "string", "default": ""},
            "root": {"type": "string", "default": str(WATCH_PATH)},
            "source": {"type": "string", "default": "", "description": "advancing actor (enables four-eyes advisories)"},
        }, ["step"]),
        tool("gate_status", "Show recent gate results.", {
            "pipeline_id": {"type": "string", "default": ""},
            "n": {"type": "integer", "default": 20},
        }),
        tool("gate_advance", "Mark a pipeline step done only if its hard gate passes.", {
            "pipeline_id": {"type": "string"},
            "step": {"type": "string", "default": ""},
            "root": {"type": "string", "default": str(WATCH_PATH)},
            "source": {"type": "string", "default": "unknown"},
        }, ["pipeline_id"]),
        tool("drift_report", "Summarize goal, plan, checks, evidence, handover, feedback, and gates.", {
            "pipeline_id": {"type": "string", "default": ""},
            "root": {"type": "string", "default": str(WATCH_PATH)},
        }),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        return _call_tool(name, arguments)
    except subprocess.TimeoutExpired:
        return text_response("Check timed out.")
    except Exception as exc:
        return text_response(f"Error: {exc}")


def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "workspace_write":
        kv = load_kv()
        key, value, source = arguments["key"], arguments["value"], arguments.get("source", "unknown")
        kv[key] = {"value": value, "updated_at": now(), "by": source}
        save_kv(kv)
        append_log(source, "write", f"{key} = {value[:80]}")
        return text_response(f"OK: '{key}' saved.")

    if name == "workspace_read":
        kv = load_kv()
        key = arguments["key"]
        if key not in kv:
            return text_response(f"Key '{key}' not found.")
        entry = kv[key]
        return text_response(f"{entry['value']}\n[by: {entry['by']} @ {entry['updated_at']}]")

    if name == "workspace_list":
        kv = load_kv()
        if not kv:
            return text_response("Workspace is empty.")
        return text_response("\n".join(f"- {k} (by {v['by']} @ {v['updated_at']})" for k, v in kv.items()))

    if name == "workspace_dump":
        kv = load_kv()
        if not kv:
            return text_response("Workspace is empty.")
        blocks = [f"### {k}\n{v['value']}\n[by: {v['by']} @ {v['updated_at']}]" for k, v in kv.items()]
        return text_response("\n\n".join(blocks))

    if name == "workspace_delete":
        kv = load_kv()
        key = arguments["key"]
        if key not in kv:
            return text_response(f"Key '{key}' not found.")
        del kv[key]
        save_kv(kv)
        append_log("system", "delete", key)
        return text_response(f"'{key}' deleted.")

    if name == "log_activity":
        append_log(arguments["source"], arguments["action"], arguments.get("detail", ""))
        return text_response("Logged.")

    if name == "get_recent_activity":
        entries = load_log()[-int(arguments.get("n", 20)):]
        if not entries:
            return text_response("No activity logged yet.")
        lines = [f"[{e['ts']}] {e['source']}: {e['action']} - {e['detail']}" for e in reversed(entries)]
        return text_response("\n".join(lines))

    if name == "get_file_events":
        events = load_file_events()
        filter_type = arguments.get("filter", "")
        if filter_type:
            events = [e for e in events if e["type"] == filter_type]
        events = events[-int(arguments.get("n", 20)):]
        if not events:
            return text_response("No file events recorded yet.")
        lines = [f"[{e['ts']}] {e['type'].upper()}: {e['path']}" for e in reversed(events)]
        return text_response("\n".join(lines))

    if name == "handover_prepare":
        target = arguments["target"].lower()
        if target not in {"codex", "cowork"}:
            return text_response("target must be 'codex' or 'cowork'.")
        source = arguments.get("source", "unknown")
        ts = now()
        kv = load_kv()
        for key, value in {
            "last_output": arguments["last_output"],
            "next_steps": arguments["next_steps"],
            "handover_notes": arguments.get("notes", "") or arguments["reason"],
            "session_owner": target,
        }.items():
            kv[key] = {"value": value, "updated_at": ts, "by": source}
        save_kv(kv)
        append_log(source, "handover", f"to {target}: {arguments['reason']}")
        return text_response(f"Handover prepared for {target}.\nNext owner: {target}")

    if name == "handover_takeover":
        agent = arguments["agent"].lower()
        if agent not in {"codex", "cowork"}:
            return text_response("agent must be 'codex' or 'cowork'.")
        n = int(arguments.get("n", 10))
        kv = load_kv()
        owner = kv.get("session_owner", {}).get("value", "")
        owner_line = "Owner OK." if owner == agent else f"Owner warning: session_owner is '{owner or 'unset'}', not '{agent}'."
        append_log(agent, "session_start", "takeover from MCP")

        workspace = "Workspace is empty."
        if kv:
            workspace = "\n\n".join(f"### {k}\n{v['value']}\n[by: {v['by']} @ {v['updated_at']}]" for k, v in kv.items())

        activity_entries = load_log()[-n:]
        activity = "No activity logged yet."
        if activity_entries:
            activity = "\n".join(f"[{e['ts']}] {e['source']}: {e['action']} - {e['detail']}" for e in reversed(activity_entries))

        events = load_file_events()[-n:]
        file_events = "No file events recorded yet."
        if events:
            file_events = "\n".join(f"[{e['ts']}] {e['type'].upper()}: {e['path']}" for e in reversed(events))

        return text_response(f"{owner_line}\n\n## workspace_dump\n{workspace}\n\n## get_recent_activity {n}\n{activity}\n\n## get_file_events {n}\n{file_events}")

    if name == "repo_status":
        root = repo_root(arguments.get("root"))
        branch = run_cmd(["git", "branch", "--show-current"], root, 10, 2000)
        status = run_cmd(["git", "status", "--short"], root, 10, 12000)
        return text_response(f"{branch}\n\n{status}")

    if name == "git_diff":
        root = repo_root(arguments.get("root"))
        max_chars = int(arguments.get("max_chars", 12000))
        stat = run_cmd(["git", "diff", "--stat"], root, 20, max_chars)
        diff = run_cmd(["git", "diff"], root, 30, max_chars)
        return text_response(clipped(f"{stat}\n\n{diff}", max_chars))

    if name == "search_code":
        root = repo_root(arguments.get("root"))
        query = arguments["query"].lower()
        max_results = int(arguments.get("max_results", 50))
        results: list[str] = []
        for path in root.rglob("*"):
            if len(results) >= max_results:
                break
            if should_skip(path) or not path.is_file():
                continue
            try:
                for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query in line.lower():
                        rel = path.relative_to(root)
                        results.append(f"{rel}:{idx}: {line.strip()[:240]}")
                        break
            except OSError:
                continue
        return text_response("\n".join(results) if results else "No matches.")

    if name == "read_file":
        path = path_under_home(arguments["path"])
        if not path.exists() or not path.is_file():
            return text_response(f"File not found: {path}")
        start = max(1, int(arguments.get("start_line", 1)))
        end = max(start, int(arguments.get("end_line", 200)))
        max_chars = int(arguments.get("max_chars", 12000))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = [f"{i}: {line}" for i, line in enumerate(lines[start - 1:end], start)]
        return text_response(clipped("\n".join(selected), max_chars))

    if name == "run_check":
        root = repo_root(arguments.get("root"))
        check = arguments["check"]
        timeout = int(arguments.get("timeout", 60))
        max_chars = int(arguments.get("max_chars", 12000))
        extra_path = arguments.get("path", "")
        source = arguments.get("source", "unknown")
        def checked(args: list[str]) -> list[TextContent]:
            result = run_cmd_result(args, root, timeout, max_chars)
            append_check_run(check, root, result, source)
            return text_response(result["text"])
        if check == "git_status":
            return checked(["git", "status", "--short"])
        if check == "python_compile":
            target = path_under_home(extra_path, root) if extra_path else root
            files = [target] if target.is_file() else [p for p in target.rglob("*.py") if not should_skip(p)]
            if not files:
                return text_response("No Python files found.")
            return checked([sys.executable, "-m", "py_compile", *map(str, files)])
        if check == "python_self_check":
            target = path_under_home(extra_path, root) if extra_path else root / "server.py"
            return checked([sys.executable, str(target), "--self-check"])
        if check == "pytest":
            return checked([sys.executable, "-m", "pytest"])
        if check == "npm_test":
            return checked(["npm", "test"])
        if check == "npm_build":
            return checked(["npm", "run", "build"])
        return text_response(f"Unknown check: {check}")

    if name == "pipeline_create":
        data = load_pipelines()
        steps = arguments.get("steps") or DEFAULT_PIPELINE_STEPS
        pipeline = make_pipeline(arguments["name"], steps, arguments.get("source", "unknown"))
        base_id = pipeline["id"]
        suffix = 2
        while pipeline["id"] in data:
            pipeline["id"] = f"{base_id}-{suffix}"
            suffix += 1
        data[pipeline["id"]] = pipeline
        save_pipelines(data)
        append_log(arguments.get("source", "unknown"), "pipeline_create", pipeline["id"])
        return text_response(json.dumps(pipeline, indent=2, ensure_ascii=False))

    if name == "pipeline_status":
        data = load_pipelines()
        pipeline_id = arguments.get("pipeline_id", "")
        if pipeline_id:
            return text_response(json.dumps(data.get(pipeline_id, {"error": "not found"}), indent=2, ensure_ascii=False))
        return text_response(json.dumps(data, indent=2, ensure_ascii=False) if data else "No pipelines.")

    if name == "pipeline_next":
        data = load_pipelines()
        pipeline = data.get(arguments["pipeline_id"])
        if not pipeline:
            return text_response("Pipeline not found.")
        return text_response(pipeline.get("current_step") or "No pending step.")

    if name == "pipeline_update_step":
        data = load_pipelines()
        pipeline_id = arguments["pipeline_id"]
        if pipeline_id not in data:
            return text_response("Pipeline not found.")
        data[pipeline_id] = update_pipeline_step(data[pipeline_id], arguments["step"], arguments["status"], arguments.get("note", ""))
        save_pipelines(data)
        append_log(arguments.get("source", "unknown"), "pipeline_update", f"{pipeline_id}:{arguments['step']}={arguments['status']}")
        return text_response(json.dumps(data[pipeline_id], indent=2, ensure_ascii=False))

    if name == "pipeline_finish":
        data = load_pipelines()
        pipeline_id = arguments["pipeline_id"]
        if pipeline_id not in data:
            return text_response("Pipeline not found.")
        pipeline = data[pipeline_id]
        for step in pipeline["steps"]:
            if step["status"] == "pending":
                step.update({"status": "done", "note": arguments.get("note", ""), "updated_at": now()})
        pipeline.update({"status": "done", "current_step": "", "updated_at": now()})
        save_pipelines(data)
        append_log(arguments.get("source", "unknown"), "pipeline_finish", pipeline_id)
        return text_response(json.dumps(pipeline, indent=2, ensure_ascii=False))

    if name == "estimate_tokens":
        return text_response(str(estimate_tokens(arguments["text"])))

    if name == "token_log":
        entries = load_token_log()
        text = arguments.get("text", "")
        input_tokens = int(arguments.get("input_tokens", 0))
        output_tokens = int(arguments.get("output_tokens", 0))
        estimated = False
        if not input_tokens and text:
            input_tokens = estimate_tokens(text)
            estimated = True
        entry = {
            "ts": now(),
            "task": arguments["task"],
            "agent": arguments.get("agent", "unknown"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated": estimated,
            "files_read": int(arguments.get("files_read", 0)),
            "commands_run": int(arguments.get("commands_run", 0)),
            "result": arguments.get("result", ""),
        }
        entries.append(entry)
        save_token_log(entries)
        return text_response(json.dumps(entry, indent=2, ensure_ascii=False))

    if name == "token_summary":
        entries = load_token_log()[-int(arguments.get("n", 20)):]
        if not entries:
            return text_response("No token usage logged.")
        total = sum(int(e.get("total_tokens", 0)) for e in entries)
        lines = [f"entries={len(entries)} total_tokens={total}"]
        lines += [f"- {e['ts']} {e['agent']} {e['task']}: {e['total_tokens']} tokens" for e in reversed(entries)]
        return text_response("\n".join(lines))

    if name == "context_snapshot":
        source = arguments.get("source", "unknown")
        kv = load_kv()
        kv["context"] = {"value": arguments["summary"], "updated_at": now(), "by": source}
        if arguments.get("next_steps"):
            kv["next_steps"] = {"value": arguments["next_steps"], "updated_at": now(), "by": source}
        if arguments.get("blockers"):
            kv["blockers"] = {"value": arguments["blockers"], "updated_at": now(), "by": source}
        save_kv(kv)
        snapshot_text = "\n".join(v for v in [arguments["summary"], arguments.get("next_steps", ""), arguments.get("blockers", "")] if v)
        entries = load_token_log()
        entries.append({
            "ts": now(),
            "task": "context_snapshot",
            "agent": source,
            "input_tokens": estimate_tokens(snapshot_text),
            "output_tokens": 0,
            "total_tokens": estimate_tokens(snapshot_text),
            "estimated": True,
            "files_read": 0,
            "commands_run": 0,
            "result": "snapshot",
        })
        save_token_log(entries)
        append_log(source, "context_snapshot", f"{estimate_tokens(snapshot_text)} estimated tokens")
        return text_response(f"Snapshot saved. Estimated tokens: {estimate_tokens(snapshot_text)}")

    if name == "learning_log_error":
        entry = append_learning({
            "type": "error",
            "source": arguments.get("source", "unknown"),
            "task": arguments.get("task", ""),
            "error": arguments["error"],
            "cause": arguments.get("cause", ""),
            "fix": arguments.get("fix", ""),
            "lesson": arguments.get("lesson", ""),
            "severity": arguments.get("severity", "medium"),
            "tags": arguments.get("tags", []),
        })
        return text_response(json.dumps(entry, indent=2, ensure_ascii=False))

    if name == "learning_log_lesson":
        entry = append_learning({
            "type": "lesson",
            "source": arguments.get("source", "unknown"),
            "task": arguments.get("task", ""),
            "lesson": arguments["lesson"],
            "trigger": arguments.get("trigger", ""),
            "tags": arguments.get("tags", []),
        })
        return text_response(json.dumps(entry, indent=2, ensure_ascii=False))

    if name == "learning_search":
        query = arguments["query"].lower()
        entries = [
            e for e in load_learning()
            if query in json.dumps(e, ensure_ascii=False).lower()
        ][-int(arguments.get("n", 10)):]
        if not entries:
            return text_response("No matching lessons.")
        lines = []
        for entry in reversed(entries):
            label = entry.get("lesson") or entry.get("error", "")
            lines.append(f"[{entry['ts']}] {entry.get('type')} {entry.get('task', '')}: {label}")
        return text_response("\n".join(lines))

    if name == "learning_recent":
        entries = load_learning()[-int(arguments.get("n", 10)):]
        if not entries:
            return text_response("No lessons recorded.")
        lines = []
        for entry in reversed(entries):
            label = entry.get("lesson") or entry.get("error", "")
            lines.append(f"[{entry['ts']}] {entry.get('type')} {entry.get('task', '')}: {label}")
        return text_response("\n".join(lines))

    if name == "goal_start":
        data = load_goals()
        goal = make_goal(
            arguments["objective"],
            arguments.get("source", "unknown"),
            arguments.get("success_criteria", ""),
            arguments.get("pipeline_id", ""),
        )
        base_id = goal["id"]
        suffix = 2
        while goal["id"] in data:
            goal["id"] = f"{base_id}-{suffix}"
            suffix += 1
        data[goal["id"]] = goal
        save_goals(data)
        append_log(arguments.get("source", "unknown"), "goal_start", goal["id"])
        return text_response(json.dumps(goal, indent=2, ensure_ascii=False))

    if name == "goal_update":
        data = load_goals()
        goal_id = arguments["goal_id"]
        if goal_id not in data:
            return text_response("Goal not found.")
        goal = data[goal_id]
        goal.update({"status": arguments["status"], "updated_at": now()})
        goal.setdefault("history", []).append({
            "ts": now(),
            "source": arguments.get("source", "unknown"),
            "status": arguments["status"],
            "note": arguments.get("note", ""),
        })
        save_goals(data)
        append_log(arguments.get("source", "unknown"), "goal_update", f"{goal_id}:{arguments['status']}")
        return text_response(json.dumps(goal, indent=2, ensure_ascii=False))

    if name == "goal_status":
        data = load_goals()
        goal_id = arguments.get("goal_id", "")
        if goal_id:
            return text_response(json.dumps(data.get(goal_id, {"error": "not found"}), indent=2, ensure_ascii=False))
        return text_response(json.dumps(data, indent=2, ensure_ascii=False) if data else "No goals.")

    if name == "goal_complete":
        data = load_goals()
        goal_id = arguments["goal_id"]
        if goal_id not in data:
            return text_response("Goal not found.")
        goal = data[goal_id]
        goal.update({"status": "done", "outcome": arguments.get("outcome", ""), "updated_at": now()})
        goal.setdefault("history", []).append({
            "ts": now(),
            "source": arguments.get("source", "unknown"),
            "status": "done",
            "note": arguments.get("outcome", ""),
        })
        save_goals(data)
        append_log(arguments.get("source", "unknown"), "goal_complete", goal_id)
        return text_response(json.dumps(goal, indent=2, ensure_ascii=False))

    if name == "feedback_maybe":
        entries = load_feedback()
        source = arguments.get("source", "unknown")
        topic = arguments.get("topic", "agent-session")
        question = arguments.get("question", "War diese Agent-Antwort hilfreich?")
        force = bool(arguments.get("force", False))
        chance = float(arguments.get("chance", 0.25))
        min_hours = float(arguments.get("min_hours", 8))
        latest_prompt = next((e for e in reversed(entries) if e.get("type") == "prompt" and e.get("topic") == topic), None)
        cooldown_ok = True
        if latest_prompt:
            age_hours = (datetime.now() - datetime.fromisoformat(latest_prompt["ts"])).total_seconds() / 3600
            cooldown_ok = age_hours >= min_hours
        if not force and (not cooldown_ok or random.random() > chance):
            return text_response("No feedback requested this time.")
        prompt_id = f"feedback-{int(datetime.now().timestamp())}-{random.randint(1000, 9999)}"
        entry = {
            "id": prompt_id,
            "type": "prompt",
            "status": "pending",
            "ts": now(),
            "source": source,
            "topic": topic,
            "question": question,
        }
        entries.append(entry)
        save_feedback(entries)
        append_log(source, "feedback_prompt", prompt_id)
        return text_response(
            f"{question}\n\n"
            f"[Gut]({feedback_url(prompt_id, 'good')})  "
            f"[Gemischt]({feedback_url(prompt_id, 'mixed')})  "
            f"[Schlecht]({feedback_url(prompt_id, 'bad')})  "
            f"[Mehr sagen]({feedback_url(prompt_id)})"
        )

    if name == "feedback_log":
        prompt_id = f"direct-{int(datetime.now().timestamp())}-{random.randint(1000, 9999)}"
        entry = record_feedback(prompt_id, arguments["rating"], arguments.get("note", ""), arguments.get("source", "user"))
        entry.update({"type": "direct", "topic": arguments.get("topic", "agent-session")})
        entries = load_feedback()
        for idx, existing in enumerate(entries):
            if existing.get("id") == entry["id"]:
                entries[idx] = entry
                break
        save_feedback(entries)
        return text_response(json.dumps(entry, indent=2, ensure_ascii=False))

    if name == "feedback_summary":
        entries = [e for e in load_feedback() if e.get("rating")][-int(arguments.get("n", 20)):]
        if not entries:
            return text_response("No feedback recorded.")
        counts = {"good": 0, "mixed": 0, "bad": 0}
        for entry in entries:
            if entry.get("rating") in counts:
                counts[entry["rating"]] += 1
        lines = [f"feedback={len(entries)} good={counts['good']} mixed={counts['mixed']} bad={counts['bad']}"]
        lines += [
            f"- {e.get('answered_at', e.get('ts'))} {e.get('rating')} {e.get('topic', '')}: {e.get('note', '')}"
            for e in reversed(entries)
        ]
        return text_response("\n".join(lines))

    if name == "verify_file_refs":
        root = repo_root(arguments.get("root"))
        source = arguments.get("source", "unknown")
        result = verify_refs(arguments["text"], root, arguments.get("snippet", ""), source)
        append_evidence(result)
        append_log(source, "verify_file_refs", f"passed={result['passed']} refs={len(result['refs'])}")
        return text_response(json.dumps(result, indent=2, ensure_ascii=False))

    if name == "check_run_history":
        root_arg = arguments.get("root", "")
        entries = load_check_runs()
        if root_arg:
            root = repo_root(root_arg)
            entries = [e for e in entries if e.get("root") == str(root)]
        entries = entries[-int(arguments.get("n", 20)):]
        if not entries:
            return text_response("No check runs recorded.")
        lines = [
            f"[{e['ts']}] {e['check']} passed={e['passed']} exit={e['exit_code']} root={e['root']}"
            for e in reversed(entries)
        ]
        return text_response("\n".join(lines))

    if name == "gate_policy":
        step = arguments.get("step", "")
        if step:
            return text_response(json.dumps({step: GATE_POLICY.get(step, [])}, indent=2, ensure_ascii=False))
        return text_response(json.dumps(GATE_POLICY, indent=2, ensure_ascii=False))

    if name == "gate_check":
        root = repo_root(arguments.get("root"))
        actor = arguments.get("source", "")
        result = evaluate_gate(arguments["step"], root, arguments.get("pipeline_id", ""), actor)
        if not result["passed"]:
            append_log(actor or "system", "blocked", f"gate {arguments['step']} failed")
            andon_log(result, actor or "system")
        return text_response(json.dumps(result, indent=2, ensure_ascii=False))

    if name == "gate_status":
        entries = load_gate_results()
        pipeline_id = arguments.get("pipeline_id", "")
        if pipeline_id:
            entries = [e for e in entries if e.get("pipeline_id") == pipeline_id]
        entries = entries[-int(arguments.get("n", 20)):]
        if not entries:
            return text_response("No gate results recorded.")
        lines = [
            f"[{e['ts']}] {e.get('pipeline_id', '')}:{e['step']} passed={e['passed']}"
            for e in reversed(entries)
        ]
        return text_response("\n".join(lines))

    if name == "gate_advance":
        root = repo_root(arguments.get("root"))
        data = load_pipelines()
        pipeline_id = arguments["pipeline_id"]
        if pipeline_id not in data:
            return text_response("Pipeline not found.")
        pipeline = data[pipeline_id]
        step = arguments.get("step") or pipeline.get("current_step", "")
        if not step:
            return text_response("No pending step.")
        result = evaluate_gate(step, root, pipeline_id, arguments.get("source", ""))
        if not result["passed"]:
            append_log(arguments.get("source", "unknown"), "blocked", f"gate_advance blocked {pipeline_id}:{step}")
            andon_log(result, arguments.get("source", "unknown"))
            return text_response(json.dumps({"advanced": False, "gate": result}, indent=2, ensure_ascii=False))
        data[pipeline_id] = update_pipeline_step(pipeline, step, "done", "gate passed")
        save_pipelines(data)
        append_log(arguments.get("source", "unknown"), "gate_advance", f"{pipeline_id}:{step}=done")
        return text_response(json.dumps({"advanced": True, "gate": result, "pipeline": data[pipeline_id]}, indent=2, ensure_ascii=False))

    if name == "drift_report":
        root = repo_root(arguments.get("root"))
        kv = load_kv()
        pipeline_id = arguments.get("pipeline_id", "")
        pipelines = load_pipelines()
        pipeline = pipelines.get(pipeline_id) if pipeline_id else None
        latest_gate = next((e for e in reversed(load_gate_results()) if not pipeline_id or e.get("pipeline_id") == pipeline_id), None)
        latest_check = recent_successful_check(root)
        latest_evidence = recent_verified_evidence(root)
        recent_feedback = next((e for e in reversed(load_feedback()) if e.get("rating")), None)
        report = {
            "ts": now(),
            "root": str(root),
            "pipeline": {
                "id": pipeline_id,
                "status": pipeline.get("status") if pipeline else "",
                "current_step": pipeline.get("current_step") if pipeline else "",
            },
            "goal_count": len(load_goals()),
            "has_plan": bool(kv.get("current_plan", {}).get("value")),
            "has_active_task": bool(kv.get("active_task", {}).get("value") and kv.get("active_task", {}).get("value") != "-"),
            "latest_successful_check": latest_check,
            "latest_check_fresh": bool(latest_check) and is_fresh(latest_check, root),
            "latest_verified_evidence": bool(latest_evidence),
            "latest_evidence_fresh": bool(latest_evidence) and is_fresh(latest_evidence, root),
            "last_change_ts": latest_relevant_event_ts(root),
            "latest_gate": latest_gate,
            "handover_ready": bool(kv.get("last_output", {}).get("value") and kv.get("next_steps", {}).get("value")),
            "recent_feedback": recent_feedback,
        }
        return text_response(json.dumps(report, indent=2, ensure_ascii=False))

    return text_response(f"Unknown tool: {name}")


def run_self_check() -> None:
    assert estimate_tokens("one two three") >= 4
    pipeline = make_pipeline("Demo Pipeline", ["plan", "test"], "self")
    pipeline = update_pipeline_step(pipeline, "plan", "done", "ok")
    assert pipeline["current_step"] == "test"
    assert "review" in GATE_POLICY
    assert "acceptance_criteria" in json.dumps(GATE_POLICY)
    # Freshness with no change boundary under a nonexistent root is trivially fresh.
    assert is_fresh({"ts": "2999-01-01T00:00:00"}, Path("/nonexistent-root-xyz"))
    refs = verify_refs("server.py:1-5", Path(__file__).resolve().parent)
    assert refs["passed"]
    assert refs["source"] == "unknown"
    goal = make_goal("Reach the target", "self", "done means done")
    assert goal["status"] == "active"
    lesson = {"type": "lesson", "source": "self", "lesson": "Keep checks close to behavior."}
    assert "checks" in json.dumps(lesson)
    feedback = {"id": "self", "rating": "good", "note": "ok"}
    assert feedback["rating"] == "good"
    assert _is_under(HOME, HOME)
    try:
        path_under_home(str(HOME.parent.parent if HOME.parent != HOME else Path("C:/")))
    except ValueError:
        pass
    else:
        raise AssertionError("path guard failed")
    print("self-check OK")


sse = SseServerTransport("/messages/")


async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def handle_stdio():
    async with stdio_server() as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


def feedback_page(prompt_id: str, question: str, message: str = "") -> str:
    safe_id = escape(prompt_id)
    safe_question = escape(question or "War diese Agent-Antwort hilfreich?")
    safe_message = f"<p class='message'>{escape(message)}</p>" if message else ""
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shared Workspace MCP Feedback</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 48px auto; padding: 0 20px; line-height: 1.5; }}
    .buttons {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 20px 0; }}
    a, button {{ border: 1px solid #222; border-radius: 6px; padding: 10px 14px; color: #111; background: #fff; text-decoration: none; cursor: pointer; }}
    textarea {{ width: 100%; min-height: 110px; margin: 12px 0; }}
    .message {{ background: #eef7ee; border: 1px solid #bfd8bf; padding: 10px 12px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>Feedback</h1>
  {safe_message}
  <p>{safe_question}</p>
  <div class="buttons">
    <a href="/feedback?id={quote(prompt_id)}&rating=good">Gut</a>
    <a href="/feedback?id={quote(prompt_id)}&rating=mixed">Gemischt</a>
    <a href="/feedback?id={quote(prompt_id)}&rating=bad">Schlecht</a>
  </div>
  <form method="post" action="/feedback">
    <input type="hidden" name="id" value="{safe_id}">
    <label for="rating">Bewertung</label>
    <select id="rating" name="rating">
      <option value="good">Gut</option>
      <option value="mixed">Gemischt</option>
      <option value="bad">Schlecht</option>
    </select>
    <label for="note"><br>Was war gut oder schlecht?</label>
    <textarea id="note" name="note"></textarea>
    <button type="submit">Feedback speichern</button>
  </form>
</body>
</html>"""


async def handle_feedback(request):
    if request.method == "POST":
        raw = (await request.body()).decode("utf-8", errors="replace")
        params = {k: v[0] for k, v in parse_qs(raw).items()}
    else:
        params = dict(request.query_params)

    prompt_id = params.get("id", "")
    rating = params.get("rating", "")
    note = params.get("note", "")
    entries = load_feedback()
    prompt = next((e for e in entries if e.get("id") == prompt_id), {})
    question = prompt.get("question", "War diese Agent-Antwort hilfreich?")

    if prompt_id and rating:
        record_feedback(prompt_id, rating, note)
        return HTMLResponse(feedback_page(prompt_id, question, "Danke, Feedback gespeichert."))

    return HTMLResponse(feedback_page(prompt_id or f"feedback-{int(datetime.now().timestamp())}", question))


app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Route("/feedback", endpoint=handle_feedback, methods=["GET", "POST"]),
    Mount("/messages/", app=sse.handle_post_message),
])


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        run_self_check()
        raise SystemExit(0)
    if "--stdio" in sys.argv:
        import anyio

        start_watcher()
        anyio.run(handle_stdio)
        raise SystemExit(0)
    start_watcher()
    logger.info("Shared Workspace MCP -> http://localhost:%s/sse", PORT)
    logger.info("Storage: %s", STORAGE_DIR)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
