<#
.SYNOPSIS
    Makes the Samsung Smart Monitor follow the Lemorele USB switch.

.DESCRIPTION
    Pressing the switch button detaches a group of USB devices from this PC and
    reattaches them to the monitor's USB-C hub, and so to the laptop. Those
    devices are therefore a reliable proxy for where the keyboard and mouse are:

        sentinel PRESENT on this PC  ->  keyboard/mouse are here   ->  show HDMI1
        sentinel ABSENT  on this PC  ->  keyboard/mouse went away  ->  show USB-C

    The monitor has no DDC/CI support, so the input is changed over the network
    via its Tizen remote API. That path does not depend on the video cable,
    which is why this single watcher can drive both directions.

    Presence is polled with cfgmgr32 (about 1 ms per check) rather than
    Get-PnpDevice (about 300 ms), so a sub-second poll costs nothing and no WMI
    event subscription is required.

.EXAMPLE
    .\scripts\Watcher.ps1 -SyncOnStart

.EXAMPLE
    .\scripts\Watcher.ps1 -WhatIfSwitch    # log decisions without touching the monitor
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
    # Absorbs the multi-second USB re-enumeration burst and rapid double-taps.
    [double] $SettleSeconds = 1.5,

    # Minimum gap between two monitor commands.
    [double] $DebounceSeconds = 3,

    # Also set the monitor to match the current state when the watcher starts.
    [switch] $SyncOnStart,

    # Log what would happen without sending anything to the monitor.
    [switch] $WhatIfSwitch,

    [string] $LogPath
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $root 'config.json' }
if (-not $PythonExe)  { $PythonExe  = Join-Path $root '.venv\Scripts\python.exe' }
if (-not $LogPath)    { $LogPath    = Join-Path $env:LOCALAPPDATA 'SamsungInputSwitcher\watcher.log' }

. (Join-Path $PSScriptRoot 'UsbPresence.ps1')

New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null

function Write-Log {
    param([string] $Message)
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Message
    try { Add-Content -Path $LogPath -Value $line -ErrorAction Stop } catch { }
    Write-Host $line
}

# Keep the log bounded.
if ((Test-Path $LogPath) -and ((Get-Item $LogPath).Length -gt 1MB)) {
    Move-Item $LogPath "$LogPath.old" -Force
}

foreach ($p in @($PythonExe, $ConfigPath)) {
    if (-not (Test-Path $p)) { throw "Required file not found: $p" }
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$usb = $config.usbSwitch

if (-not $SentinelIds) {
    if ($usb.sentinelInstanceIds) { $SentinelIds = @($usb.sentinelInstanceIds) }
    elseif ($usb.sentinelInstanceId) { $SentinelIds = @($usb.sentinelInstanceId) }
}
if (-not $PresentInput) { $PresentInput = $usb.presentInput }
if (-not $AbsentInput)  { $AbsentInput  = $usb.absentInput }
if ($PollIntervalSeconds -le 0) {
    $PollIntervalSeconds = if ($usb.pollIntervalSeconds) { [double]$usb.pollIntervalSeconds } else { 0.4 }
}

if (-not $SentinelIds) { throw 'No sentinel devices configured (usbSwitch.sentinelInstanceIds).' }

# Start-Sleep -Seconds takes an [int] on Windows PowerShell 5.1, so sub-second
# values round to zero and turn the poll loop into a busy-wait. Convert to
# milliseconds up front.
$pollMs   = [int][math]::Max([math]::Round($PollIntervalSeconds * 1000), 50)
$settleMs = [int][math]::Max([math]::Round($SettleSeconds * 1000), 100)

function Test-SentinelPresent {
    $ids = Get-PresentUsbIds
    foreach ($sentinel in $SentinelIds) {
        foreach ($id in $ids) {
            if ($id.StartsWith($sentinel, [StringComparison]::OrdinalIgnoreCase)) { return $true }
        }
    }
    return $false
}

function Set-MonitorInput {
    param([string] $InputName)

    if ($WhatIfSwitch) {
        Write-Log "  -> [WhatIf] would switch monitor to $InputName"
        return
    }

    try {
        $out = & $PythonExe -m samsung_input --config $ConfigPath --quiet switch $InputName 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "  -> monitor switched to $InputName"
        }
        else {
            Write-Log "  -> FAILED switching to ${InputName}: $out"
        }
    }
    catch {
        Write-Log "  -> EXCEPTION switching to ${InputName}: $_"
    }
}

Write-Log '=== watcher started ==='
Write-Log "sentinels: $($SentinelIds -join ' | ')"
Write-Log "present->$PresentInput  absent->$AbsentInput  poll=${pollMs}ms  settle=${settleMs}ms"

# Warn about sentinels that cannot distinguish the switch from other hardware.
# Only root device nodes are counted: a composite device such as a wireless
# receiver legitimately produces several &MI_xx interface nodes that all belong
# to the same physical device, whereas two identical hubs produce two roots.
$allIds = Get-PresentUsbIds
foreach ($sentinel in $SentinelIds) {
    # A sentinel that includes an instance path is already specific.
    if (($sentinel -split '\\').Count -gt 2) { continue }

    $roots = @(
        $allIds |
        Where-Object { $_.StartsWith($sentinel, [StringComparison]::OrdinalIgnoreCase) } |
        Where-Object { ($_ -split '\\')[1] -match '^VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4}$' }
    )
    if ($roots.Count -gt 1) {
        Write-Log "WARNING: sentinel '$sentinel' matches $($roots.Count) distinct physical devices; it cannot tell the switch apart from identical hardware. Use a full instance ID instead."
        $roots | ForEach-Object { Write-Log "         $_" }
    }
}

$state = Test-SentinelPresent
Write-Log "initial state: keyboard/mouse on $(if ($state) { 'this PC' } else { 'the laptop' })"

if ($SyncOnStart) {
    Set-MonitorInput $(if ($state) { $PresentInput } else { $AbsentInput })
}

$lastFired = [datetime]::MinValue

try {
    while ($true) {
        Start-Sleep -Milliseconds $pollMs

        $now = Test-SentinelPresent
        if ($now -eq $state) { continue }

        # Let the USB bus settle, then re-read: the devices enumerate over
        # several seconds and the user may double-tap the button.
        Start-Sleep -Milliseconds $settleMs
        $now = Test-SentinelPresent

        if ($now -eq $state) {
            Write-Log 'transient change; state unchanged after settle'
            continue
        }

        $state = $now
        $where = if ($state) { 'this PC' } else { 'the laptop' }
        Write-Log "keyboard/mouse are now on $where"

        if (((Get-Date) - $lastFired).TotalSeconds -lt $DebounceSeconds) {
            Write-Log '  -> within debounce window; skipping'
            continue
        }

        $lastFired = Get-Date
        Set-MonitorInput $(if ($state) { $PresentInput } else { $AbsentInput })
    }
}
finally {
    Write-Log '=== watcher stopped ==='
}
