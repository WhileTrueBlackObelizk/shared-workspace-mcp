$ErrorActionPreference = "Stop"

$repo = "https://github.com/WhileTrueBlackObelizk/shared-workspace-mcp.git"
$installDir = Join-Path $HOME "Claude\Projects\Skills\shared-mcp"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "git is required. Install Git for Windows, then rerun this command."
}

if (Test-Path $installDir) {
  git -C $installDir pull --ff-only
} else {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $installDir) | Out-Null
  git clone $repo $installDir
}

& (Join-Path $installDir "start.bat") --setup-only

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$wrapper = Join-Path $installDir "start-forever.cmd"
$value = "cmd.exe /c start `"Shared Workspace MCP`" /min `"$wrapper`""
New-Item -Path $runKey -Force | Out-Null
Set-ItemProperty -Path $runKey -Name "SharedWorkspaceMCP" -Value $value

$existing = Get-CimInstance Win32_Process -Filter "name = 'cmd.exe'" |
  Where-Object { $_.CommandLine -like "*start-forever.cmd*" }

if (-not $existing) {
  Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "`"$wrapper`"") -WindowStyle Hidden
}

Write-Host "Shared Workspace MCP installed."
Write-Host "URL: http://localhost:8765/sse"
