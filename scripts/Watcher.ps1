<#
.SYNOPSIS
    Runs the input watcher in the current console, for debugging.

.DESCRIPTION
    The watcher itself lives in the Python package (samsung_input.watcher), so
    that one implementation serves both the logon tray app and this wrapper.
    At logon the watcher is started by the scheduled task through pythonw.exe
    instead, because that is a GUI-subsystem binary and therefore never creates
    a console window - see scripts\Install-Task.ps1.

    Use this script when you want the decisions printed in front of you.
    Remember to stop the scheduled task first, or the two will fight:

        Stop-ScheduledTask -TaskName 'Samsung Input Switcher'

.EXAMPLE
    .\scripts\Watcher.ps1 -WhatIfSwitch    # log decisions without touching the monitor

.EXAMPLE
    .\scripts\Watcher.ps1 -SyncOnStart
#>
[CmdletBinding()]
param(
    # Instance-ID prefixes of devices behind the switch. Defaults to config.json.
    [string[]] $SentinelIds,

    # Monitor inputs used for each state. Default to config.json.
    [string] $PresentInput,
    [string] $AbsentInput,

    [string] $ConfigPath,
    [string] $PythonExe,

    # How often to check for the sentinel devices.
    [double] $PollIntervalSeconds = 0,

    # Wait this long after a change before acting, then re-read the state.
    [double] $SettleSeconds = 0,

    # Minimum gap between two monitor commands.
    [double] $DebounceSeconds = 0,

    # Also set the monitor to match the current state when the watcher starts.
    [switch] $SyncOnStart,

    # Log what would happen without sending anything to the monitor.
    [switch] $WhatIfSwitch,

    # Show the tray icon as well as logging to this console.
    [switch] $Tray,

    [string] $LogPath
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is not populated inside param() defaults on Windows PowerShell
# 5.1 when the script is launched with -File, so resolve paths here.
$root = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $root 'config.json' }
if (-not $PythonExe)  { $PythonExe  = Join-Path $root '.venv\Scripts\python.exe' }

foreach ($p in @($PythonExe, $ConfigPath)) {
    if (-not (Test-Path $p)) { throw "Required file not found: $p" }
}

$watchArgs = @('-m', 'samsung_input', '--config', $ConfigPath, 'watch')

if ($Tray) { $watchArgs += '--tray' } else { $watchArgs += '--no-tray' }
if ($WhatIfSwitch) { $watchArgs += '--what-if' }
if ($SyncOnStart)  { $watchArgs += '--sync-on-start' }
if ($PollIntervalSeconds -gt 0) { $watchArgs += @('--poll', $PollIntervalSeconds) }
if ($SettleSeconds -gt 0)       { $watchArgs += @('--settle', $SettleSeconds) }
if ($DebounceSeconds -gt 0)     { $watchArgs += @('--debounce', $DebounceSeconds) }
if ($LogPath)                   { $watchArgs += @('--log', $LogPath) }
foreach ($id in $SentinelIds)   { $watchArgs += @('--sentinel', $id) }
if ($PresentInput)              { $watchArgs += @('--present-input', $PresentInput) }
if ($AbsentInput)               { $watchArgs += @('--absent-input', $AbsentInput) }

& $PythonExe @watchArgs
exit $LASTEXITCODE
