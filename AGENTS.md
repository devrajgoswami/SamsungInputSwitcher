# AGENTS.md

Context for AI agents working on this repository. Read this before changing
anything — several obvious-looking approaches here are dead ends that were
already tried and measured.

---

## What this project does

Switches a **Samsung Smart Monitor M70D** (`LS32DM700UWXXL`, 32" Smart Monitor
M7) between **HDMI1** and **USB-C** so the picture follows a **Lemorele USB
switch** (Amazon IN `B0BTSHZMXQ`).

Physical setup:

- **Desktop PC** (MSI `MS-7E51`, Windows 11) → monitor **HDMI1**
- **Laptop** (Windows 11) → monitor **USB-C**
- **Lemorele USB switch**: input = keyboard/mouse/webcam/speakers;
  outputs = PC and the monitor's USB hub. Pressing its button moves all those
  devices between the PC and the monitor hub (and therefore the laptop).

Only the **desktop PC** runs the watcher. It drives both directions.

---

## Hard constraints — do not relitigate these

### 1. The monitor has no DDC/CI. None.

Do **not** propose `SetVCPFeature`, ControlMyMonitor, `ddcutil`, MCCS, VCP code
`0x60`, or any monitor-control library built on them.

Measured: VCP codes `0x60`, `0x10`, `0xDF`, `0x02`, six attempts each, all fail
with `0xC0262582` (`ERROR_GRAPHICS_I2C_ERROR_TRANSMITTING_DATA`). The
capabilities string comes back empty. This is firmware absence, not flakiness.

`tools\Test-DdcSupport.ps1` re-verifies this in ~2 seconds. Run it before
suggesting any DDC-based idea.

### 2. Control travels over the network, not the video cable.

The monitor is a Tizen device (`type: "Samsung SmartTV"`, `TokenAuthSupport:
true`) reachable at `http://<ip>:8001/api/v2/` and
`wss://<ip>:8002/api/v2/channels/samsung.remote.control`.

The important consequence: **a machine can switch the monitor to an input it is
not currently displaying.** That is why one watcher on the PC handles both
directions and nothing is installed on the laptop. Do not "simplify" this into
a two-machine design.

### 3. There is no API to set an input directly, and no way to read the current input.

The remote API only sends remote-control key presses. Input selection is done
by driving the on-screen **Source** list:

```
index 0: Samsung TV
index 1: HDMI1
index 2: USB-C
```

The list **does not wrap**. That is the key property this project relies on:
pressing `UP` (length − 1) times pins the highlight to the top entry from any
starting position, so the sequence is deterministic without knowing the current
input.

```
KEY_SOURCE  KEY_UP KEY_UP  KEY_DOWN KEY_DOWN  KEY_ENTER   →  USB-C
```

**Verification requires a human.** There is no readback — `/api/v2/` does not
report the active source. An agent cannot confirm a switch landed correctly on
its own; it must ask the user to look at the screen. Plan tests accordingly.

### 4. `samsungtvws.app_list()` hangs forever on this monitor.

It waits on a channel the monitor never answers. Killed after 150s. Do not add
app enumeration, app launching, or anything that calls it. Only `send_key` and
the REST `/api/v2/` endpoint are known to work.

---

## Bugs already found and fixed — do not reintroduce

### `Start-Sleep -Seconds` is `[int]` on Windows PowerShell 5.1

`Start-Sleep -Seconds 0.4` sleeps **6.9 ms**, not 400 ms. The poll loop became a
busy-wait at **~97% of a core**. Always use `-Milliseconds` for sub-second
sleeps. `Watcher.ps1` converts up front (`$pollMs`, `$settleMs`). Idle cost is
now ~0.7% CPU.

### `samsungtvws` sleeps 1 second inside every `send_key`

`key_press_delay` defaults to `1`. Combined with a manual `time.sleep()` this
made a six-key sequence take 9.5s (11.1s total per switch). The client is now
built with `key_press_delay=0` and each `send_key` is passed an explicit delay,
so the library's own pause *is* the configured delay. A switch now takes
**2.5–4s**. Never add a `time.sleep()` next to a `send_key`.

Do not lower `timing.keyDelaySeconds` below ~0.3s — the Tizen UI starts
dropping navigation presses.

### USB sentinel prefixes are dangerously ambiguous

This PC has **three** hubs with ID prefix `USB\VID_05E3&PID_0610`; only one is
inside the Lemorele switch. Using that prefix meant the sentinel was always
"present" and the switch never appeared to move — the watcher logged nothing at
all, which looks exactly like "detection is broken".

Rules for sentinels:

- Prefer full instance IDs, or VID/PIDs that are unique on the machine.
- IDs ending in a **serial number** (e.g. `USB\VID_046D&PID_C336\066634543336`,
  the G213 keyboard) survive re-plugging into a different port. IDs ending in a
  port path (`\9&b020893&0&3`) do not.
- `Watcher.ps1` warns at startup when a sentinel matches more than one *root*
  device node. Composite devices legitimately produce several `&MI_xx`
  interface nodes for one physical device — the check counts only roots, so it
  does not fire on those. Don't "fix" it to count all matches.

### `Get-PnpDevice` is far too slow to poll

| Method | Per check |
| --- | --- |
| `Get-PnpDevice -PresentOnly` | ~310 ms |
| `CM_Get_Device_ID_List` (cfgmgr32, `scripts\UsbPresence.ps1`) | ~1.2 ms |

Use `Get-PresentUsbIds` / `Test-UsbDevicePresent`. Do not reintroduce
`Get-PnpDevice` into the poll loop.

### WMI device notifications were removed deliberately

An earlier version used `Register-CimIndicationEvent` on
`Win32_DeviceChangeEvent`. Once cfgmgr32 made polling essentially free, the
subscription added dependency and uncertainty (delivery guarantees, elevation
questions) for no benefit. Straight polling at 400 ms is simpler and proven.

### `$PSScriptRoot` is empty inside `param()` defaults

On Windows PowerShell 5.1 it is not populated when the script is launched with
`-File`. Resolve paths in the body, after `param()`.

---

## Environment

| Thing | Value |
| --- | --- |
| Project root | `C:\repo\SamsungInputSwitcher` |
| Python | 3.14.6 at `C:\Python314\python.exe`; venv at `.venv` |
| Shell | Windows PowerShell 5.1 — no `&&`, `\|\|`, `??`, `?.` |
| Monitor IP | `192.168.0.67` (DHCP, Wi-Fi) |
| Monitor UUID | `uuid:4aa2e924-8730-439b-90a6-875fd801c904` |
| Monitor MAC | `6C:70:CB:F0:76:F2` |
| PC subnet | `192.168.0.0/24` |
| Scheduled task | `Samsung Input Switcher` (logon, non-elevated) |

State outside the repo, in `%LOCALAPPDATA%\SamsungInputSwitcher\`:

| File | Notes |
| --- | --- |
| `token.txt` | Pairing token. **Secret.** Gitignored. Do not print it in commits or docs. |
| `resolved_host.json` | Cached IP after rediscovery; overrides `config.json`. Delete to reset. |
| `watcher.log` | Rotated at 1 MB. First place to look for any failure. |

The monitor is on DHCP. `discovery.py` re-finds it by UUID/MAC when
`monitor.host` stops answering, so a changed IP is handled — but if you are
debugging "cannot reach monitor", check `resolved_host.json` before assuming
`config.json` is authoritative.

---

## Testing safely

Switching the input **takes the user's screen away**. Be deliberate.

1. **Static checks first — zero side effects.**
   ```powershell
   .\.venv\Scripts\python.exe -m samsung_input sequence usbc   # prints keys, sends nothing
   .\.venv\Scripts\python.exe -m samsung_input info            # REST only, no key presses
   ```

2. **Detection without switching.**
   ```powershell
   .\scripts\Watcher.ps1 -WhatIfSwitch
   ```
   Logs every decision, never touches the monitor. Use this for all USB-side
   work. Ask the user to press the button, then read the log.

3. **Switching to the input the monitor is already on is a safe no-op.**
   If the PC is showing HDMI1, `switch hdmi1` exercises the whole path
   end-to-end while changing nothing visible.

4. **If you must switch away, make recovery automatic.** Send the target, sleep,
   then send `hdmi1` unconditionally. Because control is over the network it
   works even though you cannot see the screen. Warn the user first.

5. **Stop the scheduled task before running a watcher by hand**, or the two will
   fight:
   ```powershell
   Stop-ScheduledTask -TaskName 'Samsung Input Switcher'
   ```
   A test that "didn't work" already happened once because the task was stopped
   at the time. Always confirm task state before concluding anything.

6. **`ask_user` is a real test instrument here.** Since there is no readback,
   ask precisely: which entry is highlighted, what order the list is in, whether
   it landed on the right input. Vague questions produce vague data.

---

## Re-deriving the setup after hardware changes

- **Switch moved to another USB port, or devices added/removed behind it:**
  `tools\Find-UsbSentinel.ps1 -Seconds 60`, press the button while it runs.
  Everything that disappears and returns together belongs to the switch. Put
  unambiguous IDs in `usbSwitch.sentinelInstanceIds`. Prefer the switch's
  SuperSpeed hub — it enumerates ~1s before the keyboard, so it makes the
  reaction faster. Removal of all devices is simultaneous.

- **Source list changed** (e.g. something plugged into HDMI2): open the menu
  with `-m samsung_input keys KEY_SOURCE`, ask the user for the new top-to-bottom
  order, and update `sourceList` in `config.json`. Re-check that the list still
  does not wrap (send `KEY_UP` × length and ask what is highlighted); the homing
  trick depends on it.

- **Pairing lost:** delete `token.txt`, remove the PC from the monitor's
  *Connected Devices*, run `-m samsung_input pair`, and tell the user to accept
  the on-screen prompt.

---

## Conventions

- All tunables belong in `config.json`, not hardcoded in scripts. Both the
  PowerShell watcher and the Python package read it.
- Python owns network/monitor logic; PowerShell owns USB detection and the
  scheduled task. Keep that split.
- The scheduled task runs **non-elevated**. Don't add anything needing admin.
- Comment only what needs explaining — mostly the non-obvious hardware
  behaviour (no wrap, ambiguous prefixes, library sleep). Don't narrate code.
- Keep `README.md` (user-facing) and this file (agent-facing) in sync when
  behaviour changes.
