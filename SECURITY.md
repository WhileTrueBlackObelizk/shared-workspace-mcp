<p align="center"><img src="assets/banner.svg" alt="Cairn" width="100%"></p>

# Security Policy

Shared Workspace MCP is local-first. It binds to `127.0.0.1` and stores data in
`~/.claude/shared-workspace/`.

## Supported branch

Security fixes land on `main`.

## Reporting

Do not open a public issue for credentials or exploitable details. Use GitHub's
private vulnerability reporting if available, or contact the repository owner
privately.

## Secret handling

This repo includes:

- a stdlib secret scanner: `python scripts/check_secrets.py`
- a GitHub Actions secret scan
- a local pre-commit hook in `.githooks/pre-commit`
- `.gitignore` entries for env files and runtime storage

The scanner is intentionally small. If it blocks a false positive, rewrite the
example as a placeholder rather than committing a real-looking credential.
