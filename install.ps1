$ErrorActionPreference = "Stop"

$repo = "https://github.com/WhileTrueBlackObelizk/shared-workspace-mcp.git"
$installDir = Join-Path $HOME "Claude\Projects\Skills\shared-mcp"

function Set-JsonProperty {
  param(
    [Parameter(Mandatory = $true)] [pscustomobject] $Object,
    [Parameter(Mandatory = $true)] [string] $Name,
    [Parameter(Mandatory = $true)] $Value
  )

  $property = $Object.PSObject.Properties[$Name]
  if ($property) {
    $property.Value = $Value
  } else {
    Add-Member -InputObject $Object -NotePropertyName $Name -NotePropertyValue $Value
  }
}

function Update-ClaudeDesktopConfig {
  param(
    [Parameter(Mandatory = $true)] [string] $ConfigPath,
    [Parameter(Mandatory = $true)] [string] $PythonPath,
    [Parameter(Mandatory = $true)] [string] $ServerPath
  )

  $parent = Split-Path -Parent $ConfigPath
  if (-not (Test-Path $parent)) {
    return $false
  }

  try {
    if (Test-Path $ConfigPath) {
      $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    } else {
      $config = [pscustomobject]@{}
    }
  } catch {
    Write-Warning "Skipped invalid Claude Desktop config: $ConfigPath"
    return $false
  }

  if ($config -isnot [pscustomobject]) {
    Write-Warning "Skipped non-object Claude Desktop config: $ConfigPath"
    return $false
  }

  $servers = $config.PSObject.Properties["mcpServers"].Value
  if ($servers -isnot [pscustomobject]) {
    $servers = [pscustomobject]@{}
    Set-JsonProperty -Object $config -Name "mcpServers" -Value $servers
  }

  Set-JsonProperty -Object $servers -Name "shared-workspace" -Value ([pscustomobject]@{
    command = $PythonPath
    args = @($ServerPath, "--stdio")
  })

  $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding utf8
  return $true
}

function Install-ClaudeCoworkExtension {
  param(
    [Parameter(Mandatory = $true)] [string] $ClaudeRoot,
    [Parameter(Mandatory = $true)] [string] $InstallDir,
    [Parameter(Mandatory = $true)] [string] $PythonPath,
    [Parameter(Mandatory = $true)] [string] $ServerPath
  )

  if (-not (Test-Path $ClaudeRoot)) {
    return $false
  }

  $script = Join-Path $InstallDir "scripts\install_claude_extension.py"
  & $PythonPath $script `
    --claude-root $ClaudeRoot `
    --python $PythonPath `
    --server $ServerPath | Out-Null
  return $true
}

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
git -C $installDir config core.hooksPath .githooks

$pythonPath = Join-Path $installDir ".venv\Scripts\python.exe"
$serverPath = Join-Path $installDir "server.py"

if (Get-Command claude -ErrorAction SilentlyContinue) {
  claude mcp remove shared-workspace -s user 2>$null | Out-Null
  claude mcp add --scope user shared-workspace -- $pythonPath $serverPath --stdio | Out-Null
}

$desktopConfigPaths = @()
if ($env:APPDATA) {
  $desktopConfigPaths += Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
}
if ($env:LOCALAPPDATA) {
  $packageRoot = Join-Path $env:LOCALAPPDATA "Packages"
  if (Test-Path $packageRoot) {
    $desktopConfigPaths += Get-ChildItem -Path $packageRoot -Directory -Filter "Claude_*" |
      ForEach-Object { Join-Path $_.FullName "LocalCache\Roaming\Claude\claude_desktop_config.json" }
  }
}

$desktopConfigsUpdated = 0
foreach ($configPath in ($desktopConfigPaths | Sort-Object -Unique)) {
  if (Update-ClaudeDesktopConfig -ConfigPath $configPath -PythonPath $pythonPath -ServerPath $serverPath) {
    $desktopConfigsUpdated++
  }
}

$coworkRoots = @()
if ($env:APPDATA) {
  $coworkRoots += Join-Path $env:APPDATA "Claude"
}
if ($env:LOCALAPPDATA) {
  $packageRoot = Join-Path $env:LOCALAPPDATA "Packages"
  if (Test-Path $packageRoot) {
    $coworkRoots += Get-ChildItem -Path $packageRoot -Directory -Filter "Claude_*" |
      ForEach-Object { Join-Path $_.FullName "LocalCache\Roaming\Claude" }
  }
}

$coworkExtensionsUpdated = 0
foreach ($root in ($coworkRoots | Sort-Object -Unique)) {
  if (Install-ClaudeCoworkExtension -ClaudeRoot $root -InstallDir $installDir -PythonPath $pythonPath -ServerPath $serverPath) {
    $coworkExtensionsUpdated++
  }
}

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
if ($desktopConfigsUpdated -gt 0) {
  Write-Host "Claude Desktop/Cowork config updated. Restart Claude to load the MCP tools."
}
if ($coworkExtensionsUpdated -gt 0) {
  Write-Host "Claude Desktop/Cowork extension installed. Restart Claude to load the MCP tools."
}
Write-Host "URL: http://localhost:8765/sse"
