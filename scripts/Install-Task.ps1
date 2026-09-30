<#
.SYNOPSIS
    Registers the watcher as a hidden scheduled task that starts at logon.

.DESCRIPTION
    Does not require elevation: the task runs as the current interactive user,
    which is enough for USB device notifications and network access.

.EXAMPLE
    .\scripts\Install-Task.ps1
    .\scripts\Install-Task.ps1 -SyncOnStart
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'Samsung Input Switcher',
    [switch] $SyncOnStart,
    [switch] $StartNow
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$watcher = Join-Path $root 'scripts\Watcher.ps1'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

foreach ($p in @($watcher, $venvPython)) {
    if (-not (Test-Path $p)) { throw "Missing $p - run .\scripts\Setup.ps1 first." }
}

$argParts = @(
    '-NoProfile'
    '-ExecutionPolicy Bypass'
    '-WindowStyle Hidden'
    "-File `"$watcher`""
)
if ($SyncOnStart) { $argParts += '-SyncOnStart' }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ($argParts -join ' ') -WorkingDirectory $root

$triggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "Log file: $env:LOCALAPPDATA\SamsungInputSwitcher\watcher.log"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'Task started.'
}
else {
    Write-Host "Start it with:  Start-ScheduledTask -TaskName '$TaskName'"
}
