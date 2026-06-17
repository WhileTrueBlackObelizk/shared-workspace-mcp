#!/usr/bin/env python3
"""Small stdlib secret scanner for commits and CI."""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
SKIP_EXTS = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2"}
ALLOWLIST = {
    "https://raw.githubusercontent.com/WhileTrueBlackObelizk/shared-workspace-mcp/main/install.ps1",
    "https://raw.githubusercontent.com/WhileTrueBlackObelizk/shared-workspace-mcp/main/install.sh",
}

PATTERNS = [
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Generic credential assignment", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"][^'\"\n]{12,}['\"]")),
]


def tracked_files() -> list[Path]:
    try:
        out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
        files = [ROOT / line for line in out.splitlines() if line.strip()]
    except Exception:
        files = [p for p in ROOT.rglob("*") if p.is_file()]
    return [p for p in files if not should_skip(p)]


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return any(part in SKIP_DIRS for part in rel.parts) or path.suffix.lower() in SKIP_EXTS


def entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {ch: value.count(ch) for ch in set(value)}
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    findings: list[str] = []
    for idx, line in enumerate(text.splitlines(), 1):
        if any(allowed in line for allowed in ALLOWLIST):
            continue
        for label, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(f"{path.relative_to(ROOT)}:{idx}: {label}")

        # ponytail: catches pasted opaque secrets without owning a full scanner.
        for token in re.findall(r"[A-Za-z0-9_/\-+=]{32,}", line):
            if "/" in token:
                continue
            if entropy(token) >= 4.2 and not token.startswith(("http", "localhost")):
                findings.append(f"{path.relative_to(ROOT)}:{idx}: high-entropy token")
                break
    return findings


def main() -> int:
    findings = [item for path in tracked_files() for item in scan_file(path)]
    if findings:
        print("Potential secrets found:\n" + "\n".join(findings), file=sys.stderr)
        return 1
    print("secret scan OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
