# Samsung Input Switcher

Automatically switches a **Samsung Smart Monitor M70D** (`LS32DM700U`) between
its **HDMI1** and **USB-C** inputs so the picture follows a **Lemorele USB
switch**.

Press the button on the USB switch: the keyboard and mouse move between the
desktop PC and the laptop, and a few seconds later the monitor shows the
machine that now owns them.

```
            ┌──────────────┐
  keyboard  │  Lemorele    │──── USB ───▶ Desktop PC ──── HDMI1 ────┐
  mouse ───▶│  USB switch  │                                        ▼
  webcam    │  (button)    │──── USB ───▶ Monitor hub ──┐      ┌─────────┐
  speakers  └──────────────┘                            │      │ M70D    │
                                                        └─ USB-C ─▶      │
                                          Laptop ───────────────┘ monitor│
                                                               └─────────┘
```

---

## Why this is not just a DDC/CI script

The normal way to change a monitor's input is DDC/CI — VCP code `0x60` via
`SetVCPFeature`, which is what ControlMyMonitor and `ddcutil` use.

**The M70D does not implement DDC/CI.** It is a Tizen device internally, and
every request fails at the I²C layer:

```
> .\tools\Test-DdcSupport.ps1

Index Description         DdcResponds InputValue Win32Error
----- -----------         ----------- ---------- ----------
    0 Generic PnP Monitor       False            0xC0262582
```

`0xC0262582` is `ERROR_GRAPHICS_I2C_ERROR_TRANSMITTING_DATA`. It fails for
every VCP code, on every retry — this is a firmware-level absence, not
flakiness.

So this project instead drives the monitor's **Tizen WebSocket remote API** —
the same interface the SmartThings app uses — over the network.

That has a useful consequence: **control does not travel over the video
cable**, so a single machine can switch the monitor *to* an input it is not
currently displaying. Only the desktop PC needs to run the watcher; it drives
both directions. Nothing has to be installed on the laptop.

---

## How an input is selected

The remote API has no "set input" command. Inputs are chosen by driving the
on-screen **Source** list:

```
  Samsung TV     index 0
  HDMI1          index 1
  USB-C          index 2
```

The list **does not wrap** at its ends. That makes a deterministic sequence
possible from any starting point — press `UP` enough times to pin the highlight
to the top entry, then step down:

```
KEY_SOURCE  KEY_UP  KEY_UP  KEY_DOWN  KEY_DOWN  KEY_ENTER   ->  USB-C
```

Because homing is absolute, the switcher never needs to know which input is
currently active. Verify any sequence without touching the monitor:

```powershell
.\.venv\Scripts\python.exe -m samsung_input sequence usbc
```

---

## How the USB switch is detected

Pressing the switch button detaches a group of USB devices from this PC and
reattaches them to the monitor's USB-C hub. Those devices are a proxy for where
the keyboard and mouse are:

| Sentinel devices present | Keyboard/mouse are on | Monitor shows |
| ------------------------ | --------------------- | ------------- |
| yes                      | this PC               | HDMI1         |
| no                       | the laptop            | USB-C         |

Two details matter:

**1. Sentinels must be unambiguous.** The switch contains a Genesys Logic hub
with ID `USB\VID_05E3&PID_0610` — but this PC has *three* identical hubs with
that same VID/PID. Using it as a prefix would match permanently-attached
hardware, so the switch would never appear to move. Use a full instance ID, or
a device with a unique VID/PID. IDs ending in a serial number (for example
`USB\VID_046D&PID_C336\066634543336`) also survive being re-plugged into a
different port.

`scripts\Watcher.ps1` warns at startup if a configured sentinel matches more
than one distinct physical device.

**2. Presence is polled with `cfgmgr32`, not `Get-PnpDevice`.**

| Method                           | Time per check |
| -------------------------------- | -------------- |
| `Get-PnpDevice -PresentOnly`     | ~310 ms        |
| `CM_Get_Device_ID_List` (cfgmgr32) | ~1.2 ms      |

At ~1 ms a check, polling every 400 ms is free, and there is no dependence on
WMI device-change notifications being delivered.

> **Gotcha:** `Start-Sleep -Seconds` takes an `[int]` on Windows PowerShell
> 5.1, so `-Seconds 0.4` rounds to **0** and the poll loop becomes a busy-wait
> (measured at ~97% of a core). `Watcher.ps1` converts the interval to
> milliseconds before sleeping; it now idles at ~0.7% CPU.

Several sentinels are OR'd together. The switch's SuperSpeed hub enumerates
about a second before the keyboard, so including it makes the reaction faster.

---

## Install

Requires Python 3.9+ and Windows PowerShell.

```powershell
cd C:\repo\SamsungInputSwitcher

# 1. Create the virtualenv and install the package
.\scripts\Setup.ps1

# 2. Pair with the monitor (accept the prompt that appears on screen)
.\.venv\Scripts\python.exe -m samsung_input pair

# 3. Check it can see the monitor
.\.venv\Scripts\python.exe -m samsung_input info

# 4. Run at logon
.\scripts\Install-Task.ps1 -StartNow
```

The scheduled task runs as the current user and **does not need
administrator rights**.

Remove it with `.\scripts\Uninstall-Task.ps1`.

---

## Usage

```powershell
$py = ".\.venv\Scripts\python.exe"

& $py -m samsung_input switch usbc      # show the laptop
& $py -m samsung_input switch hdmi1     # show the PC
& $py -m samsung_input switch tv        # show the built-in Samsung TV

& $py -m samsung_input info             # monitor + config summary
& $py -m samsung_input discover         # scan the LAN for Samsung devices
& $py -m samsung_input sequence usbc    # print key sequence, send nothing
& $py -m samsung_input keys KEY_SOURCE KEY_DOWN KEY_ENTER
```

Watcher, run by hand:

```powershell
.\scripts\Watcher.ps1                 # normal
.\scripts\Watcher.ps1 -WhatIfSwitch   # log decisions, never touch the monitor
.\scripts\Watcher.ps1 -SyncOnStart    # also align the monitor on startup
```

`-WhatIfSwitch` is the right way to check detection without disturbing your
screen.

---

## Configuration — `config.json`

| Key | Meaning |
| --- | --- |
| `monitor.host` | Monitor IP. Used first; if unreachable the LAN is scanned. |
| `monitor.deviceId` / `wifiMac` | Identity used to re-find the monitor after a DHCP change. |
| `sourceList.inputs.*.index` | Position in the on-screen Source list, from the top. |
| `sourceList.length` | Number of entries; determines how many `UP` presses home the list. |
| `timing.menuDelaySeconds` | Pause after `KEY_SOURCE` while the menu appears. |
| `timing.keyDelaySeconds` | Pause after each navigation key. |
| `usbSwitch.sentinelInstanceIds` | Device ID prefixes that move with the switch. Any match = devices are here. |
| `usbSwitch.pollIntervalSeconds` | USB presence poll interval. |
| `usbSwitch.presentInput` / `absentInput` | Inputs for each state. |

### DHCP resilience

The monitor is on Wi-Fi, so its address can change. If `monitor.host` stops
responding, the tool scans the local subnets for port 8001 and matches the
device by `deviceId`/`wifiMac`, then caches the new address in
`%LOCALAPPDATA%\SamsungInputSwitcher\resolved_host.json`. Assigning a DHCP
reservation is still worthwhile.

### Files kept outside the repo

`%LOCALAPPDATA%\SamsungInputSwitcher\`

| File | Purpose |
| ---- | ------- |
| `token.txt` | Pairing token. **Secret** — never commit. |
| `resolved_host.json` | Cached IP after rediscovery. |
| `watcher.log` | Watcher activity, rotated at 1 MB. |

---

## Re-detecting the sentinels

If you move the switch to a different USB port or change what is plugged into
it:

```powershell
.\tools\Find-UsbSentinel.ps1 -Seconds 60
```

Press the switch button while it runs. Everything that disappears and returns
together belongs to the switch. Put the unambiguous IDs into
`usbSwitch.sentinelInstanceIds`.

---

## Performance notes

A switch takes about **2.5–4 seconds** end to end.

`samsungtvws` sleeps **1 second after every key press** by default, which made
a six-key sequence take over 9 seconds. The switcher passes `key_press_delay`
explicitly so the library's own pause *is* the configured delay, rather than
sleeping twice per key.

| Stage | Time |
| ----- | ---- |
| Python startup + imports | ~0.2 s |
| WebSocket connect | ~1.4 s |
| Six-key source sequence | ~1.0–2.5 s |

Lowering `timing.keyDelaySeconds` below ~0.3 s risks the Tizen UI dropping
navigation presses.

---

## Troubleshooting

**Nothing happens when I press the button.**
Check `%LOCALAPPDATA%\SamsungInputSwitcher\watcher.log`. If there is no
"keyboard/mouse are now on ..." line, the sentinels are wrong — run
`tools\Find-UsbSentinel.ps1`. A startup `WARNING` about an ambiguous sentinel
is the usual cause.

**"Monitor not reachable ... and not found on the local network".**
The monitor is off, asleep, or on a different subnet from the PC. Confirm with
`-m samsung_input discover`. A monitor in deep standby drops off Wi-Fi.

**It switches to the wrong input.**
The Source list changed (for example a device was added on HDMI2). Re-open it,
note the new order, and update `sourceList` in `config.json`.

**Pairing prompt never appears / token is rejected.**
Delete `%LOCALAPPDATA%\SamsungInputSwitcher\token.txt`, remove this PC from the
monitor's *Connected Devices* list, and pair again.

**The PC's windows rearrange when the monitor switches away.**
Windows sees the display disconnect. That is a property of the monitor, not of
this tool — it happens with the physical remote too. A display-persistence
utility or an HDMI EDID emulator is the usual workaround.

---

## Project layout

```
config.json                     all tunable settings
pyproject.toml                  package metadata
src/samsung_input/
    cli.py                      command line interface
    config.py                   config loading, state paths
    discovery.py                LAN scan / device identification
    monitor.py                  WebSocket client, input selection
scripts/
    Setup.ps1                   venv + dependencies
    Watcher.ps1                 USB watcher, drives the switch
    UsbPresence.ps1             fast cfgmgr32 presence check
    Install-Task.ps1            register logon task
    Uninstall-Task.ps1          remove logon task
tools/
    Find-UsbSentinel.ps1        identify devices behind the USB switch
    Test-DdcSupport.ps1         demonstrate the lack of DDC/CI
```
