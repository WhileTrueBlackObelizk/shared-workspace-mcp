# Agent Instructions

This repo is the source of truth for the Shared Workspace MCP server.

When changing behavior, architecture, setup, persistence, tool contracts, or
handover conventions:

1. Update `README.md` if user-facing setup or usage changes.
2. Update `ARCHITECTURE.md` if storage, tools, security boundaries, pipeline
   behavior, or token measurement changes.
3. Update `handover-protocol.md` if Cowork/Codex workflow changes.
4. Run the smallest relevant check:
   `python server.py --self-check`
5. Push the architectural change to GitHub.

Keep changes ponytail-small: no new dependencies unless the stdlib cannot do
the job safely.
