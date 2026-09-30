<#
.SYNOPSIS
    Registers the watcher as a tray application that starts at logon.

.DESCRIPTION
    The task runs pythonw.exe, not powershell.exe. That matters: powershell.exe
    and python.exe are console binaries, and Windows 11 hands their console to
    the default terminal app, which ignores -WindowStyle Hidden - that is why a
    terminal window used to appear at logon. pythonw.exe is a GUI-subsystem
    binary, so no console is ever created.

    Does not require elevation: the task runs as the current interactive user,
    which is enough for USB device enumeration and network access.

.EXAMPLE
    .\scripts\Install-Task.ps1 -StartNow

.EXAMPLE
    .\scripts\Install-Task.ps1 -NoTray      # run headless, log only
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'Samsung Input Switcher',
    [switch] $SyncOnStart,
    [switch] $NoTray,
    [switch] $StartNow
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $root 'config.json'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'

foreach ($p in @($pythonw, $configPath)) {
    if (-not (Test-Path $p)) { throw "Missing $p - run .\scripts\Setup.ps1 first." }
}

$argParts = @(
    '-m samsung_input'
    "--config `"$configPath`""
    'watch'
)
if ($NoTray)      { $argParts += '--no-tray' } else { $argParts += '--tray' }
if ($SyncOnStart) { $argParts += '--sync-on-start' }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument ($argParts -join ' ') -WorkingDirectory $root

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
if (-not $NoTray) {
    Write-Host "Windows 11 hides new tray icons by default: click the '^' arrow next to the clock," -ForegroundColor Yellow
    Write-Host "then drag the Samsung Input Switcher icon onto the taskbar to keep it visible." -ForegroundColor Yellow
}

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'Task started.'
}
else {
    Write-Host "Start it with:  Start-ScheduledTask -TaskName '$TaskName'"
}
