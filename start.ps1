# co-company launcher (ASCII only). Stops a previous server of THIS project, then starts uvicorn.
# -OpenBrowser: open the app in the default browser once the server accepts connections (used by start.bat).
param([switch]$OpenBrowser)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
. (Join-Path $Root "server-common.ps1")

$cfg = Read-DotEnv (Join-Path $Root ".env")
$HostAddr = if ($cfg["HOST"]) { $cfg["HOST"] } else { "127.0.0.1" }
$Port = if ($cfg["PORT"]) { [int]$cfg["PORT"] } else { 8765 }
$Host.UI.RawUI.WindowTitle = "co-company server :$Port"

if (-not (Stop-ProjectServer -Root $Root -Port $Port)) { exit 1 }

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "[ERROR] .venv not found. Run: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt"
    exit 1
}
$Db = Join-Path $Root "data\app.db"
if (-not (Test-Path $Db)) {
    Write-Host "[start] first run: importing the original Excel files"
    & $Py -m app.importer
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

$Url = "http://${HostAddr}:$Port/"
# same console: uvicorn logs show here and Ctrl+C reaches it
$server = Start-Process -FilePath $Py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $HostAddr, "--port", $Port `
    -WorkingDirectory $Root -NoNewWindow -PassThru
Write-Host "[start] PID $($server.Id)  URL $Url  listen ${HostAddr}:$Port"

if ($OpenBrowser) {
    $ready = $false
    for ($i = 0; $i -lt 60 -and -not $server.HasExited; $i++) {
        try { $c = New-Object Net.Sockets.TcpClient($HostAddr, $Port); $c.Close(); $ready = $true; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if ($ready) {
        Start-Process $Url
        Write-Host "[start] opened $Url in the browser"
    } else {
        Write-Host "[ERROR] server did not start within 30 seconds; browser not opened"
    }
}

$server.WaitForExit()
exit $server.ExitCode
