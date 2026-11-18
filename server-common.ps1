# Shared by start.ps1 / stop.ps1. ASCII only.

function Read-DotEnv([string]$Path) {
    $map = @{}
    if (Test-Path $Path) {
        foreach ($line in Get-Content $Path) {
            $t = $line.Trim()
            if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
                $k, $v = $t.Split("=", 2)
                $map[$k.Trim()] = $v.Trim()
            }
        }
    }
    return $map
}

function Stop-Tree([int]$ProcessId) {
    foreach ($child in Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId") {
        Stop-Tree -ProcessId $child.ProcessId
    }
    try { Stop-Process -Id $ProcessId -Force -ErrorAction Stop } catch {}
}

# Returns $true when the port is free (after stopping our own server), $false if another app holds it.
function Stop-ProjectServer([string]$Root, [int]$Port) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($l in $listeners) {
        $procId = [int]$l.OwningProcess
        $p = Get-CimInstance Win32_Process -Filter "ProcessId = $procId"
        if (-not $p) { continue }
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ParentProcessId)"
        $lines = @($p.CommandLine, $p.ExecutablePath)
        if ($parent) { $lines += @($parent.CommandLine, $parent.ExecutablePath) }
        $ours = $false
        foreach ($s in $lines) { if ($s -and $s.ToLower().Contains($Root.ToLower())) { $ours = $true } }
        if (-not $ours) {
            Write-Host "[ERROR] port $Port is used by another app (PID $procId, $($p.ExecutablePath))"
            return $false
        }
        Write-Host "[start] stopping previous server PID $procId"
        if ($parent -and $parent.CommandLine -and $parent.CommandLine.ToLower().Contains($Root.ToLower()) -and $parent.Name -like "python*") {
            Stop-Tree -ProcessId $parent.ProcessId
        } else {
            Stop-Tree -ProcessId $procId
        }
    }
    for ($i = 0; $i -lt 10; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "[ERROR] port $Port is still in use after 5 seconds"
    return $false
}
