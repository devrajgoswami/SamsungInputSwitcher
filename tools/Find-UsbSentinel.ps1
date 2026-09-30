<#
.SYNOPSIS
    Identifies which USB devices are attached to the KVM/USB switch.

.DESCRIPTION
    Logs USB arrivals and removals for a fixed period. Press the switch button
    while this runs; everything that disappears and reappears together belongs
    to the switch. Use the switch's own hub as the sentinel - it is the first
    device to enumerate and the last to leave, so it gives the fastest and most
    stable signal.

.EXAMPLE
    .\tools\Find-UsbSentinel.ps1 -Seconds 60
#>
[CmdletBinding()]
param(
    [int] $Seconds = 60,
    [string] $LogPath = "$env:LOCALAPPDATA\SamsungInputSwitcher\usb-changes.log"
)

$ErrorActionPreference = 'SilentlyContinue'
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null

function Get-UsbSnapshot {
    Get-PnpDevice -PresentOnly |
        Where-Object { $_.InstanceId -like 'USB\*' -or $_.InstanceId -like 'HID\*' } |
        ForEach-Object { '{0}`t{1}`t{2}' -f $_.Class, $_.InstanceId, $_.FriendlyName }
}

Write-Host "Watching USB changes for $Seconds seconds. Press the switch button now." -ForegroundColor Cyan
'=== watch started {0:yyyy-MM-dd HH:mm:ss} for {1}s ===' -f (Get-Date), $Seconds | Set-Content $LogPath

$prev = Get-UsbSnapshot
$deadline = (Get-Date).AddSeconds($Seconds)

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    $cur = Get-UsbSnapshot

    $diff = Compare-Object $prev $cur
    foreach ($d in $diff) {
        $tag = if ($d.SideIndicator -eq '=>') { '+ ADDED  ' } else { '- REMOVED' }
        $line = '{0:HH:mm:ss}  {1}  {2}' -f (Get-Date), $tag, $d.InputObject
        $line | Add-Content $LogPath
        Write-Host $line
    }

    $prev = $cur
}

'=== watch finished {0:yyyy-MM-dd HH:mm:ss} ===' -f (Get-Date) | Add-Content $LogPath
Write-Host ''
Write-Host "Full log: $LogPath" -ForegroundColor Cyan
Write-Host 'Look for a "Generic USB Hub" that leaves and returns with your keyboard/mouse.'
Write-Host 'Put its VID/PID prefix into config.json as usbSwitch.sentinelInstanceId.'
