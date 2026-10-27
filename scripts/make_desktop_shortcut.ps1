# Creates the desktop icon "HR Attendance" that opens the web app like a normal program.
# DEFAULT IS PRINT-ONLY: with no switches it prints what it would create and changes nothing.
#   .\make_desktop_shortcut.ps1            print the shortcut details
#   .\make_desktop_shortcut.ps1 -Create    really create the .lnk on the current user's Desktop
param(
    [string]$ProjectRoot = "",
    [string]$DesktopDir  = "",
    [switch]$Create
)

if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
$ProjectRoot = $ProjectRoot.TrimEnd('\')
if (-not $DesktopDir) { $DesktopDir = [Environment]::GetFolderPath("Desktop") }

$Name      = "HR Attendance"
$LnkPath   = Join-Path $DesktopDir "$Name.lnk"
$Launcher  = Join-Path $ProjectRoot "scripts\launch_app.ps1"
$IconPath  = Join-Path $ProjectRoot "scripts\assets\hr_attendance.ico"
$Target    = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Arguments = '-WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $Launcher

if ($Create) {
    if (-not (Test-Path -LiteralPath $IconPath)) {
        Write-Host "Icon missing: $IconPath. Run: .venv\Scripts\python.exe scripts\make_icon.py"
        exit 1
    }
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($LnkPath)
    $lnk.TargetPath       = $Target
    $lnk.Arguments        = $Arguments
    $lnk.WorkingDirectory = $ProjectRoot
    $lnk.IconLocation     = "$IconPath,0"
    $lnk.Description      = "Open the HR Attendance app"
    $lnk.WindowStyle      = 7
    $lnk.Save()
    Write-Host "Created $LnkPath"
    exit 0
}

Write-Host "PRINT-ONLY: nothing is created. Add -Create to make the desktop icon."
Write-Host "Shortcut file : $LnkPath"
Write-Host "Target        : $Target"
Write-Host "Arguments     : $Arguments"
Write-Host "Start in      : $ProjectRoot"
Write-Host "Icon          : $IconPath"
exit 0
