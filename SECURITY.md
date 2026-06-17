<p align="center"><img src="assets/banner.svg" alt="Cairn" width="100%"></p>

# Security Policy

Shared Workspace MCP is local-first. It binds to `127.0.0.1` and stores data in
`~/.claude/shared-workspace/`.

## Threat model & trust boundary

This is a **local, single-user tool**. Treat anything that can reach
`127.0.0.1:8765` as trusted, and run it only on a machine you control.

- **No authentication.** The server trusts the loopback interface. Do not run it
  on a shared or multi-user host, and do not expose the port beyond localhost.
- **Filesystem reach.** `read_file`, `search_code`, and `verify_file_refs` can
  read any file under your home directory (path traversal outside `$HOME`,
  including via symlinks, is rejected). Callers can therefore read sensitive
  files such as `~/.ssh` if they can reach the server.
- **Code execution via checks.** `run_check` with `pytest`, `ruff`, or `mypy`
  executes code from the target repository. Only point it at repositories you
  trust.
- **Browser protection.** A built-in guard rejects requests whose `Host` is not
  loopback or whose `Origin` is not localhost, blocking DNS-rebinding and
  cross-origin browser access. Non-browser MCP clients (no `Origin`) are
  unaffected.

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
