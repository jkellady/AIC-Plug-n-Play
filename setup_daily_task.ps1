# Creates the daily task. Run once in PowerShell, as the Windows user that will run the check.
# To remove it later:  Unregister-ScheduledTask -TaskName "AIC Daily Check" -Confirm:$false
$taskName = "AIC Daily Check"
$root     = $PSScriptRoot
$time     = "06:00"        # change if Client prefers another time

$action    = New-ScheduledTaskAction -Execute "$root\run_daily.bat" -WorkingDirectory $root
$trigger   = New-ScheduledTaskTrigger -Daily -At $time
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                 -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
                 -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Daily attendance check for Client. Reads the month folders in 01_DROP_FOLDER." -Force

Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Write-Host "Task created. It runs daily at $time while this user is logged on (or at the next logon if the PC was off)."
Write-Host "To run it when nobody is logged on: Task Scheduler > AIC Daily Check > Properties > 'Run whether user is logged on or not' (the PC owner types their own password)."
