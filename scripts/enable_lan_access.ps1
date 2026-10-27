# Lets office PCs on the same Wi-Fi/LAN open the HR Attendance app (opt-in, off by default).
# DEFAULT IS PRINT-ONLY: with no switches it prints what it would do and changes nothing.
#   .\enable_lan_access.ps1             print the plan and the URL staff would use
#   .\enable_lan_access.ps1 -Apply      create the firewall rule (needs an Administrator PowerShell) and the LAN marker file
#   .\enable_lan_access.ps1 -Remove     print what would be removed;  -Remove -Apply  really remove it
# The firewall rule allows inbound TCP on the app port from the LOCAL SUBNET only.
# Document version 1, created 2026-10-18, supersedes: none.
param(
    [string]$ProjectRoot = "",
    [int]$Port = 8765,
    [switch]$Apply,
    [switch]$Remove
)

if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
$ProjectRoot = $ProjectRoot.TrimEnd('\')
$RuleName = "HR Attendance (LAN)"
$Marker   = Join-Path $ProjectRoot "07_LOGS\webapp\lan.enabled"

$ip = $null
try {
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Select-Object -First 1 -ExpandProperty IPAddress
} catch { }
$Url = if ($ip) { "http://${ip}:$Port/" } else { "http://<this-PC-LAN-address>:$Port/" }

$fw = 'New-NetFirewallRule -DisplayName "{0}" -Direction Inbound -Action Allow -Protocol TCP -LocalPort {1} -RemoteAddress LocalSubnet -Profile Private,Domain' -f $RuleName, $Port

if ($Remove) {
    if ($Apply) {
        Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $Marker -Force -ErrorAction SilentlyContinue
        Write-Host "Removed firewall rule and LAN marker. The app is localhost-only again after the next restart."
    } else {
        Write-Host "PRINT-ONLY: would remove firewall rule '$RuleName' and file $Marker. Add -Apply to do it."
    }
    exit 0
}

if ($Apply) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { Write-Host "Run this in an Administrator PowerShell to create the firewall rule."; exit 1 }
    New-Item -ItemType Directory -Path (Split-Path $Marker) -Force | Out-Null
    Set-Content -LiteralPath $Marker -Value "LAN access enabled" -Encoding ascii
    Invoke-Expression $fw | Out-Null
    Write-Host "Done. Restart the app (launch_app.ps1 -Stop, then open it again). Staff open: $Url"
    exit 0
}

Write-Host "PRINT-ONLY: nothing is changed. Add -Apply (as Administrator) to enable office access."
Write-Host "Marker file   : $Marker"
Write-Host "Firewall rule : $fw"
Write-Host "Staff URL     : $Url"
Write-Host "Notes: office devices on the same network only; login required; the connection is plain HTTP, so use a trusted office network."
exit 0
