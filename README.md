# Samsung Input Switcher

Automatically switches a **Samsung Smart Monitor M70D** (`LS32DM700U`) between
its **HDMI1** and **USB-C** inputs so the picture follows a **Lemorele USB
switch**.

Press the button on the USB switch: the keyboard and mouse move between the
desktop PC and the laptop, and about **3.5 seconds later** the monitor shows
the machine that now owns them.

It runs at logon as a **system tray app** — no console window, no visible
process.

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

The list **does not wrap at either end**. That makes a deterministic sequence
possible from any starting point: press `UP` (length − 1) times to pin the
highlight to the first entry, or `DOWN` (length − 1) times to pin it to the
last. The switcher homes from whichever end is **nearer** the target:

```
KEY_SOURCE  KEY_DOWN  KEY_DOWN            KEY_ENTER   ->  USB-C   (1.9s)
KEY_SOURCE  KEY_UP    KEY_UP    KEY_DOWN  KEY_ENTER   ->  HDMI1   (2.2s)
KEY_SOURCE  KEY_UP    KEY_UP              KEY_ENTER   ->  Samsung TV
```

Homing to the top for USB-C would need four navigation keys instead of two, and
the Tizen UI needs ~0.3 s per key — so picking the nearer end takes 0.6 s off
every switch to the laptop.

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

| Method                             | Time per check |
| ---------------------------------- | -------------- |
| `Get-PnpDevice -PresentOnly`       | ~310 ms        |
| `CM_Get_Device_ID_List` (cfgmgr32) | ~0.3 ms        |

At a third of a millisecond a check, polling every 120 ms costs ~0.6% of a
core, and there is no dependence on WMI device-change notifications being
delivered.

> **Gotcha (historical):** the watcher used to be a PowerShell loop, where
> `Start-Sleep -Seconds` takes an `[int]` on Windows PowerShell 5.1 — so
> `-Seconds 0.4` rounded to **0** and the loop became a busy-wait at ~97% of a
> core. The loop is now Python, where `float` sleeps behave, but the same trap
> applies to any PowerShell added here.

Several sentinels are OR'd together. The switch's SuperSpeed hub enumerates
about a second before the keyboard, so including it makes the reaction faster.

---

## The tray app

At logon a scheduled task starts the watcher as a tray application. Right-click
the icon for:

- where the keyboard and mouse currently are, and which input was last selected
- **Switch to Samsung TV / HDMI1 / USB-C** — manual override
- **Pause following the USB switch** — stop reacting without quitting
- **Resync monitor to keyboard** — re-send the input for the current state
- **Open log** / **Exit**

The icon is colour-coded: green = keyboard on this PC, blue = on the laptop,
grey = paused, red = the last switch failed.

> Windows 11 hides new tray icons by default. Click the `^` arrow next to the
> clock and drag the Samsung Input Switcher icon onto the taskbar to keep it
> visible.

**Why `pythonw.exe`?** `powershell.exe` and `python.exe` are console-subsystem
binaries. On Windows 11 the console is handed to the default terminal app,
which **ignores `-WindowStyle Hidden`** — that is why a terminal window used to
appear at logon and stay there. `pythonw.exe` is a GUI-subsystem binary, so no
console is ever created and there is nothing to hide.

Set `tray.enabled` to `false` in `config.json` (or install with
`.\scripts\Install-Task.ps1 -NoTray`) to run headless, logging only.

Only one watcher can run at a time; a second one exits immediately rather than
fighting the first for the monitor.


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

Watcher, run by hand (stop the scheduled task first, or the two will fight):

```powershell
Stop-ScheduledTask -TaskName 'Samsung Input Switcher'

.\scripts\Watcher.ps1                 # normal, logs to this console
.\scripts\Watcher.ps1 -WhatIfSwitch   # log decisions, never touch the monitor
.\scripts\Watcher.ps1 -SyncOnStart    # also align the monitor on startup
.\scripts\Watcher.ps1 -Tray           # show the tray icon as well
```

`-WhatIfSwitch` is the right way to check detection without disturbing your
screen. The script is a thin wrapper around `-m samsung_input watch`, so the
logon task and manual runs share one implementation.

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
| `usbSwitch.pollIntervalSeconds` | USB presence poll interval (default 0.12). |
| `usbSwitch.settleSeconds` | Wait after a change before acting, then re-read (default 0.35). |
| `usbSwitch.debounceSeconds` | Minimum gap between two monitor commands (default 3). |
| `usbSwitch.presentInput` / `absentInput` | Inputs for each state. |
| `tray.enabled` | Show the tray icon. `false` runs headless. |
| `tray.notifyOnError` | Balloon notification when a switch fails. |

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

A button press takes about **3 seconds** end to end, down from ~5.6 s.

| Stage | Before | Now | Why |
| ----- | ------ | --- | --- |
| Detect the USB change | ~200 ms | ~70 ms | Poll every 120 ms instead of 400 ms; a check costs 0.3 ms. |
| Settle before acting | 1500 ms | 350 ms | Removal is simultaneous and arrival only needs the *first* sentinel, so this only has to survive a momentary drop. |
| Spawn `python.exe` + imports | ~330 ms | 0 ms | The watcher is one long-lived process and calls the switch in-process instead of launching a CLI per press. |
| WebSocket connect | ~1350 ms | ~1050 ms | Started as soon as the change is seen, so it overlaps the settle wait. |
| Source sequence → USB-C | ~2500 ms | ~1900 ms | Homed from the nearer end of the list: two navigation keys instead of four. |
| Source sequence → HDMI1 | ~2200 ms | ~2200 ms | Already minimal. |

Detection itself is *not* a bottleneck in either direction. Measured at 20 ms
resolution across a real button press:

| Direction | Button → watcher reacts |
| --------- | ----------------------- |
| PC → laptop (all sentinels removed) | **69 ms** |
| laptop → PC (first sentinel enumerates) | **~0 ms** from first USB event |

Removal really is simultaneous — every sentinel disappears within 70 ms. On
arrival the switch's SuperSpeed hub enumerates ~1.1 s before the keyboard and
~5.1 s before the Logitech receiver, which is why including the hub matters.

> **Switching to the laptop still *feels* slower than switching back.** Most of
> what remains is on the laptop, not here: its USB-C DisplayPort link has to be
> re-established and Windows has to reconfigure the display. The PC's HDMI
> output stays live the whole time, so HDMI1 appears the instant the monitor
> selects it.

Two library-level traps, both already handled:

`samsungtvws` sleeps **1 second after every key press** by default, which made
a six-key sequence take over 9 seconds. The switcher passes `key_press_delay`
explicitly so the library's own pause *is* the configured delay, rather than
sleeping twice per key.

Lowering `timing.keyDelaySeconds` below ~0.3 s risks the Tizen UI dropping
navigation presses. `timing.menuDelaySeconds` (1.0 s) is the remaining lever,
but check on screen that the Source list still appears before the first key.

### Why the connection is not kept open

Holding the WebSocket open between presses would remove the ~1 s handshake, but
the monitor does not allow it. It **pings every ~6 seconds** and, after three
unanswered pings, closes the socket with `notack`:

```
 6s  b'\x89\x01\x00'                  <- ping
12s  b'\x89\x01\x00'                  <- ping
18s  b'\x88\x08\x03\xednotack'        <- close
```

Nothing in the send-only path ever reads the socket, so no pong is sent. Worse,
the drop is not visible locally: `is_alive()` still reports `True` and the next
write succeeds into a half-open socket, with the failure surfacing several keys
later — mid-sequence, leaving the Source list open at an unknown position.

Keeping it alive would mean a reader thread ponging forever. The connection is
therefore opened per switch and closed afterwards.

---

## Troubleshooting

**I can't see the tray icon.**
Windows 11 puts new tray icons in the hidden overflow. Click the `^` arrow next
to the clock and drag it onto the taskbar. If it is not in the overflow either,
check the log — the watcher keeps running even when the icon cannot be added.

**A terminal window opens at logon.**
The task is still registered with the old `powershell.exe` action. Re-run
`.\scripts\Install-Task.ps1`; it registers `pythonw.exe`, which never creates a
console. `-WindowStyle Hidden` does not help, because Windows 11's default
terminal app ignores it.

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
AGENTS.md                       context for AI agents working on this repo
pyproject.toml                  package metadata
src/samsung_input/
    cli.py                      command line interface
    config.py                   config loading, state paths
    discovery.py                LAN scan / device identification
    monitor.py                  WebSocket client, input selection
    usb.py                      fast cfgmgr32 presence check
    watcher.py                  poll/settle/debounce loop
    tray.py                     Win32 tray icon and menu
scripts/
    Setup.ps1                   venv + dependencies
    Watcher.ps1                 console wrapper around `-m samsung_input watch`
    UsbPresence.ps1             fast cfgmgr32 presence check (for the tools)
    Install-Task.ps1            register logon task (pythonw.exe)
    Uninstall-Task.ps1          remove logon task
tools/
    Find-UsbSentinel.ps1        identify devices behind the USB switch
    Test-DdcSupport.ps1         demonstrate the lack of DDC/CI
```
