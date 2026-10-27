# Registers the Client attendance agent jobs in Windows Task Scheduler.
# DEFAULT IS PRINT-ONLY: with no switches it prints the schtasks commands and changes nothing.
#   .\install_tasks.ps1                  print the commands
#   .\install_tasks.ps1 -Register        really create the tasks (current user, interactive logon, no password)
#   .\install_tasks.ps1 -Unregister      print what would be removed
#   .\install_tasks.ps1 -Unregister -Confirm   remove tasks named AIC-Attendance-Agent-*  (only those)
# Scan time 12:00 is an ASSUMED default (Q5 unanswered).
param(
    [string]$ProjectRoot = "",
    [string]$ScanTime    = "12:00",   # daily
    [string]$WeeklyTime  = "09:00",   # Mondays
    [string]$MonthlyTime = "09:00",   # 1st of each month
    [switch]$Register,
    [switch]$Unregister,
    [switch]$Confirm
)

$Prefix = "AIC-Attendance-Agent-"
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
$ProjectRoot = $ProjectRoot.TrimEnd('\')
$User = "$env:USERDOMAIN\$env:USERNAME"

function Get-AgentJobs {
    $py = "$ProjectRoot\.venv\Scripts\python.exe"
    @(
        @{ Name = "Scan";    Module = "src.agents.drop_scanner";   Schedule = "/SC DAILY";            Time = $ScanTime;    When = "daily at $ScanTime" },
        @{ Name = "Weekly";  Module = "src.agents.weekly_report";  Schedule = "/SC WEEKLY /D MON";    Time = $WeeklyTime;  When = "Mondays at $WeeklyTime" },
        @{ Name = "Monthly"; Module = "src.agents.monthly_report"; Schedule = "/SC MONTHLY /D 1";     Time = $MonthlyTime; When = "day 1 of each month at $MonthlyTime" }
    ) | ForEach-Object {
        $log = "$ProjectRoot\07_LOGS\task_$($_.Name).log"
        # cmd /c: change to the project root, run the module via -m (propagates sys.exit), append stdout and stderr to the log.
        $inner = 'cmd /c "cd /d "{0}" && "{1}" -m {2} >> "{3}" 2>&1"' -f $ProjectRoot, $py, $_.Module, $log
        $tr = '"' + ($inner -replace '"', '\"') + '"'
        $_.TaskName = "$Prefix$($_.Name)"
        $_.Command  = 'schtasks /Create /TN "{0}" /TR {1} {2} /ST {3} /RU "{4}" /IT' -f $_.TaskName, $tr, $_.Schedule, $_.Time, $User
        $_
    }
}

function Get-ExistingAgentTasks {
    $out = & schtasks.exe /Query /FO CSV /NH 2>$null
    $names = foreach ($line in $out) {
        if ($line -match '^"\?([^"]+)"') { $matches[1] }
    }
    @($names | Where-Object { $_ -like "$Prefix*" })
}

function Invoke-Schtasks([string]$line) {
    $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c $line" -Wait -NoNewWindow -PassThru
    if ($p.ExitCode -ne 0) { Write-Host "FAILED (exit $($p.ExitCode)): $line"; $script:failed = $true }
}

$failed = $false

if ($Unregister) {
    $existing = Get-ExistingAgentTasks
    if (-not $existing) { Write-Host "No tasks starting with $Prefix exist. Nothing to remove."; exit 0 }
    foreach ($t in $existing) {
        $cmd = 'schtasks /Delete /TN "{0}" /F' -f $t
        if ($Confirm) { Write-Host "Removing $t"; Invoke-Schtasks $cmd } else { Write-Host "[print-only] $cmd" }
    }
    if (-not $Confirm) { Write-Host "Nothing removed. Add -Confirm to remove the tasks listed above."; }
    if ($failed) { exit 1 } else { exit 0 }
}

$jobs = Get-AgentJobs
if ($Register) {
    $logs = Join-Path $ProjectRoot "07_LOGS"
    if (-not (Test-Path -LiteralPath $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }
    foreach ($j in $jobs) { Write-Host "Registering $($j.TaskName) ($($j.When))"; Invoke-Schtasks $j.Command }
    if ($failed) { exit 1 } else { exit 0 }
}

Write-Host "PRINT-ONLY: nothing is registered. Add -Register to create these tasks."
Write-Host "Runs as $User, interactive logon only, no password stored. Project root: $ProjectRoot"
Write-Host ""
foreach ($j in $jobs) {
    Write-Host "# $($j.TaskName): $($j.When)"
    Write-Host $j.Command
    Write-Host ""
}
exit 0
