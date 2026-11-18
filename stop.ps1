# Stops the co-company server (use when the launcher window was already closed). ASCII only.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "server-common.ps1")
$cfg = Read-DotEnv (Join-Path $Root ".env")
$Port = if ($cfg["PORT"]) { [int]$cfg["PORT"] } else { 8765 }
if (Stop-ProjectServer -Root $Root -Port $Port) { Write-Host "[stop] port $Port is free" } else { exit 1 }
