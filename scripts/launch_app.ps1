# Opens the HR Attendance web app like a normal desktop program.
# Starts the local server hidden (LAN-accessible), waits for it, then opens Edge in app mode.
#   .\launch_app.ps1                 start (or reuse) the server and open the app window
#   .\launch_app.ps1 -WhatIf         print the commands only; starts nothing, opens nothing
#   .\launch_app.ps1 -Stop           stop ONLY the server process recorded in 07_LOGS\webapp\server.pid
# No admin rights, no passwords. Logs and the Edge profile live under 07_LOGS\webapp.
param(
    [string]$ProjectRoot = "",
    [int]$Port = 8765,
    [int]$TimeoutSec = 20,
    [switch]$Stop,
    [switch]$WhatIf
)

if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
$ProjectRoot = $ProjectRoot.TrimEnd('\')
$Py      = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir  = Join-Path $ProjectRoot "07_LOGS\webapp"
$PidFile = Join-Path $LogDir "server.pid"
$Profile = Join-Path $LogDir "edge-profile"
$OutLog  = Join-Path $LogDir "server.out.log"
$ErrLog  = Join-Path $LogDir "server.err.log"
# Only pass --port when it is not the module's default, so the default launch is exactly the plain module call.
$ServerArgs = @("-m", "src.webapp.server", "--lan")
if ($Port -ne 8765) { $ServerArgs += @("--port", "$Port") }

# Detect LAN IP so other devices on the same network can use the same URL.
$LanIp = try {
    $s = [System.Net.Sockets.UdpClient]::new(); $s.Connect("10.255.255.255", 9)
    $ip = ($s.Client.LocalEndPoint.Address.ToString()); $s.Close(); $ip
} catch { "127.0.0.1" }
if ($LanIp -notmatch '^\d+\.\d+\.\d+\.\d+$' -or $LanIp -eq '0.0.0.0') { $LanIp = "127.0.0.1" }
$Url = "http://${LanIp}:$Port/"

function Show-Problem([string]$text) {
    Write-Host $text
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show($text, "HR Attendance")
    } catch { }
}

function Test-ServerUp {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch {
        # An HTTP error status still means something answered on the port.
        return ($null -ne $_.Exception.Response)
    }
}

function Find-Browser {
    $bases = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ }
    $candidates = @(
        @{ Name = "Edge";   Rel = "Microsoft\Edge\Application\msedge.exe" },
        @{ Name = "Chrome"; Rel = "Google\Chrome\Application\chrome.exe" }
    )
    foreach ($c in $candidates) {
        foreach ($b in $bases) {
            $p = Join-Path $b $c.Rel
            if (Test-Path -LiteralPath $p) { return @{ Name = $c.Name; Path = $p } }
        }
    }
    return $null
}

function Get-AppArgs {
    # Quoted so a project root with spaces survives.
    return @("--app=$Url", ('--user-data-dir="{0}"' -f $Profile))
}

# ---- -Stop: stop only the recorded process -------------------------------------------------------
if ($Stop) {
    if ($WhatIf) { Write-Host "[WhatIf] would stop the process whose id is in `"$PidFile`""; exit 0 }
    if (-not (Test-Path -LiteralPath $PidFile)) { Write-Host "No pid file at $PidFile. Nothing to stop."; exit 0 }
    $recorded = (Get-Content -LiteralPath $PidFile -TotalCount 1).Trim()
    $proc = $null
    if ($recorded -match '^\d+$') { $proc = Get-Process -Id ([int]$recorded) -ErrorAction SilentlyContinue }
    if ($proc -and $proc.ProcessName -like "python*") {
        Stop-Process -Id $proc.Id -Force
        Write-Host "Stopped server process $($proc.Id)."
    } else {
        Write-Host "Recorded process '$recorded' is not a running python process. Nothing stopped."
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

# ---- -WhatIf: print only -------------------------------------------------------------------------
if ($WhatIf) {
    $b = Find-Browser
    Write-Host "PRINT-ONLY: nothing is started or opened. Project root: $ProjectRoot"
    Write-Host ""
    Write-Host "# 1. Reuse the server if $Url already answers or the pid in `"$PidFile`" is alive; otherwise start it hidden:"
    Write-Host ('"{0}" {1}   (working dir "{2}", output to "{3}" and "{4}")' -f $Py, ($ServerArgs -join ' '), $ProjectRoot, $OutLog, $ErrLog)
    Write-Host "# 2. Wait up to $TimeoutSec s for $Url (on failure: message box pointing to `"$LogDir`")"
    Write-Host "# 3. Open the app window:"
    if ($b) { Write-Host ('"{0}" {1}' -f $b.Path, ((Get-AppArgs) -join ' ')) }
    else    { Write-Host ('msedge.exe {0}   (Edge or Chrome not found here; would fall back to the default browser: {1})' -f ((Get-AppArgs) -join ' '), $Url) }
    exit 0
}

# ---- Real launch ---------------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

if (-not (Test-ServerUp)) {
    if (-not (Test-Path -LiteralPath $Py)) {
        Show-Problem "Cannot find the project's Python at:`n$Py`n`nAsk the developer to check the install."
        exit 1
    }
    $started = Start-Process -FilePath $Py -ArgumentList $ServerArgs -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
    Set-Content -LiteralPath $PidFile -Value $started.Id -Encoding ascii

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $up = $false
    while ((Get-Date) -lt $deadline) {
        if ($started.HasExited) { break }
        if (Test-ServerUp) { $up = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $up) {
        Show-Problem "The attendance app did not start within $TimeoutSec seconds.`n`nPlease send the files in this folder to the developer:`n$LogDir"
        exit 1
    }
}

$browser = Find-Browser
if ($browser) {
    if (-not (Test-Path -LiteralPath $Profile)) { New-Item -ItemType Directory -Path $Profile -Force | Out-Null }
    Start-Process -FilePath $browser.Path -ArgumentList (Get-AppArgs)
} else {
    Start-Process $Url
}
exit 0
