#!/usr/bin/env bash
set -euo pipefail

repo="https://github.com/WhileTrueBlackObelizk/shared-workspace-mcp.git"
install_dir="${HOME}/Claude/Projects/Skills/shared-mcp"

command -v git >/dev/null || {
  echo "git is required." >&2
  exit 1
}

if [ -d "${install_dir}/.git" ]; then
  git -C "${install_dir}" pull --ff-only
elif [ -d "${install_dir}" ]; then
  echo "${install_dir} exists but is not a git checkout." >&2
  exit 1
else
  mkdir -p "$(dirname "${install_dir}")"
  git clone "${repo}" "${install_dir}"
fi

chmod +x "${install_dir}/start.sh"
chmod +x "${install_dir}/.githooks/pre-commit" 2>/dev/null || true
"${install_dir}/start.sh" --setup-only
git -C "${install_dir}" config core.hooksPath .githooks

if command -v claude >/dev/null; then
  claude mcp remove shared-workspace -s user >/dev/null 2>&1 || true
  claude mcp add --scope user shared-workspace -- "${install_dir}/.venv/bin/python" "${install_dir}/server.py" --stdio >/dev/null
fi

if command -v systemctl >/dev/null && systemctl --user status >/dev/null 2>&1; then
  mkdir -p "${HOME}/.config/systemd/user"
  cat > "${HOME}/.config/systemd/user/shared-workspace-mcp.service" <<EOF
[Unit]
Description=Shared Workspace MCP

[Service]
WorkingDirectory=${install_dir}
ExecStart=${install_dir}/start.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now shared-workspace-mcp.service
else
  nohup "${install_dir}/start.sh" >/tmp/shared-workspace-mcp.log 2>&1 &
  echo "systemd user service unavailable; started background process instead."
fi

echo "Shared Workspace MCP installed."
echo "URL: http://localhost:8765/sse"
