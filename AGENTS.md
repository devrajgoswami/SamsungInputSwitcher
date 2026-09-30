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

Only the **desktop PC** runs the watcher. It drives both directions. At logon it
starts as a tray app under `pythonw.exe` (see "‑WindowStyle Hidden does not hide
anything" below).

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

The list **does not wrap at either end** — verified on screen in both
directions. That is the key property this project relies on: pressing `UP`
(length − 1) times pins the highlight to the *first* entry and `DOWN`
(length − 1) times pins it to the *last*, from any starting position, so the
sequence is deterministic without knowing the current input.

`key_sequence_for()` homes from whichever end is **nearer** the target. At
0.3s per key that matters:

```
KEY_SOURCE  KEY_DOWN KEY_DOWN            KEY_ENTER   →  USB-C   (1.9s)
KEY_SOURCE  KEY_UP   KEY_UP    KEY_DOWN  KEY_ENTER   →  HDMI1   (2.2s)
KEY_SOURCE  KEY_UP   KEY_UP              KEY_ENTER   →  Samsung TV
```

Do not "simplify" this back to always homing to the top; that costs 0.6s on
every switch to the laptop. Bottom-homing is only safe because **USB-C is the
last entry with nothing below it** (no "Connection Guide"/"Edit" row). If the
source list ever changes, re-verify that before trusting `KEY_DOWN` homing.

**Verification requires a human.** There is no readback — `/api/v2/` does not
report the active source. An agent cannot confirm a switch landed correctly on
its own; it must ask the user to look at the screen. Plan tests accordingly.

### 4. `samsungtvws.app_list()` hangs forever on this monitor.

It waits on a channel the monitor never answers. Killed after 150s. Do not add
app enumeration, app launching, or anything that calls it. Only `send_key` and
the REST `/api/v2/` endpoint are known to work.

### 5. The remote WebSocket cannot be kept open.

Do not "optimise" the ~1s handshake away by holding a persistent connection.

Measured: the monitor **pings every ~6 seconds** and closes the socket with
`notack` after three unanswered pings:

```
 6s  b'\x89\x01\x00'                  <- ping
12s  b'\x89\x01\x00'                  <- ping
18s  b'\x88\x08\x03\xednotack'        <- close
```

The send-only path never reads the socket, so no pong is ever sent. Keeping the
connection alive would require a reader thread ponging forever, which the owner
has explicitly ruled out.

The drop is also *undetectable locally*: `is_alive()` keeps returning `True`
(it only checks a local flag), a `ping()` appears to succeed, and the first key
writes fine into a half-open socket. The `ConnectionAbortedError` surfaces
several keys later — mid-sequence, with the Source list open at an unknown
position, which is the one state the homing trick cannot recover from.

`watcher.py` therefore opens the connection per switch and closes it in a
`finally`. It starts the handshake the moment a change is detected so it
overlaps the settle wait; that is the only safe part of the saving.

---

## Bugs already found and fixed — do not reintroduce

### `-WindowStyle Hidden` does not hide anything on Windows 11

The logon task used to run `powershell.exe -WindowStyle Hidden -File
Watcher.ps1` and a terminal window appeared at logon and stayed. The hidden
style was not being ignored by Task Scheduler — Windows 11's **default terminal
app (Windows Terminal) ignores `SW_HIDE`** when it takes over the console of a
console-subsystem binary.

`powershell.exe` and `python.exe` are console binaries, so there is no argument
that fixes this. The task now runs **`pythonw.exe`**, a GUI-subsystem binary
that never allocates a console at all. Do not "simplify" it back to
`powershell.exe`, and do not reach for `wscript.exe`/`mshta.exe` shims — they
pass `SW_HIDE` too, and hit the same wall.

Confirmed by `Get-Process | Where-Object MainWindowTitle -ne ''`: with the old
task there was a `WindowsTerminal` process titled `...\powershell.exe`; with
the new one there is nothing.

### `Start-Sleep -Seconds` is `[int]` on Windows PowerShell 5.1

`Start-Sleep -Seconds 0.4` sleeps **6.9 ms**, not 400 ms. The old PowerShell
poll loop became a busy-wait at **~97% of a core**. Always use `-Milliseconds`
for sub-second sleeps in PowerShell. The loop is now Python (`watcher.py`), so
this no longer applies to the watcher itself, but it still applies to anything
added under `scripts\` or `tools\`.


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
- `watcher.py` warns at startup when a sentinel matches more than one *root*
  device node. Composite devices legitimately produce several `&MI_xx`
  interface nodes for one physical device — the check counts only roots, so it
  does not fire on those. Don't "fix" it to count all matches.

### Detection latency is not the bottleneck — don't go hunting there

Measured at 20 ms resolution across a real button press (all USB arrivals and
removals timestamped):

| Direction | Button → aggregate flips |
| --- | --- |
| PC → laptop (all sentinels removed) | **69 ms** |
| laptop → PC (first sentinel enumerates) | **~0 ms** from the first USB event |

Removal really is simultaneous: every device behind the switch disappears
within 70 ms. On arrival the order is hub → keyboard (+1.1s) → Logitech
receiver (+5.1s) → speakers (+6.1s), which is why the SuperSpeed hub must stay
in the sentinel list — presence is `any`, so arrival fires on the *first*
device and removal waits for the *last*.

If someone reports "switching to the laptop is slower", it is not the poll
interval or the settle time. It is the extra `KEY_DOWN` (now removed by
nearer-end homing) plus the laptop's own USB-C DisplayPort link training and
display reconfiguration, which this project cannot touch. The PC's HDMI output
stays live the whole time, so the other direction has no equivalent cost.

### `Get-PnpDevice` is far too slow to poll

| Method | Per check |
| --- | --- |
| `Get-PnpDevice -PresentOnly` | ~310 ms |
| `CM_Get_Device_ID_List` (cfgmgr32) | ~0.3 ms |

`samsung_input/usb.py` is the ctypes version used by the watcher;
`scripts\UsbPresence.ps1` is the PowerShell version still used by
`tools\Find-UsbSentinel.ps1`. Keep the two in step. Do not reintroduce
`Get-PnpDevice` into any poll loop.

### Dropping a switch inside the debounce window desynced the monitor

The old loop updated its state variable and then `continue`d when a second
change arrived within `DebounceSeconds`, so the switch was silently discarded
and the monitor kept showing the wrong machine. The loop now **waits out the
remainder of the window, re-reads, and then acts**. Do not turn it back into a
skip. The lower settle time makes this path easier to hit, not harder.


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
| Scheduled task | `Samsung Input Switcher` (logon, non-elevated, runs `pythonw.exe -m samsung_input watch --tray`) |

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

7. **Verifying the tray icon without eyes.** `Graphics.CopyFromScreen` returns
   black for the taskbar, so screenshots are useless here. Enumerate the real
   notification area with UI Automation instead — the icon's accessible name is
   its tooltip:

   ```powershell
   Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
   $root = [System.Windows.Automation.AutomationElement]::RootElement
   $c = New-Object System.Windows.Automation.PropertyCondition `
        ([System.Windows.Automation.AutomationElement]::NameProperty), 'Show Hidden Icons'
   $btn = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
   $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
   ```

   Then re-enumerate descendants and look for `SystemTray.*` buttons. Windows 11
   puts new icons in the hidden overflow, so they will not be in the visible
   tray. Press Escape afterwards to close the flyout. A quicker liveness check
   is `FindWindowW('SamsungInputSwitcherTray', $null)`.

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
  order, and update `sourceList` in `config.json`. Re-check **both** ends: send
  `KEY_UP` × length and ask what is highlighted, then `KEY_DOWN` × length and
  ask again. Nearer-end homing depends on neither end wrapping, and on nothing
  sitting below the last input.

- **Pairing lost:** delete `token.txt`, remove the PC from the monitor's
  *Connected Devices*, run `-m samsung_input pair`, and tell the user to accept
  the on-screen prompt.

---

## Conventions

- All tunables belong in `config.json`, not hardcoded in scripts. Both the
  PowerShell scripts and the Python package read it.
- Python owns the monitor/network logic, the watch loop and the tray UI.
  PowerShell owns the scheduled task, setup, and the standalone `tools\`.
  `scripts\Watcher.ps1` is only a console wrapper around
  `-m samsung_input watch` — don't reimplement the loop in it again.
- The tray UI is plain ctypes Win32 on purpose. Don't add `pystray`/`Pillow`
  for an icon that is drawn in twenty lines.
- The scheduled task runs **non-elevated**. Don't add anything needing admin.
- Comment only what needs explaining — mostly the non-obvious hardware
  behaviour (no wrap, ambiguous prefixes, library sleep). Don't narrate code.
- Keep `README.md` (user-facing) and this file (agent-facing) in sync when
  behaviour changes.
