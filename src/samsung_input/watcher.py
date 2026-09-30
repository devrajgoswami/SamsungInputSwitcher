"""Make the monitor follow the Lemorele USB switch.

Pressing the switch button detaches a group of USB devices from this PC and
reattaches them to the monitor's USB-C hub, and so to the laptop. Those devices
are therefore a reliable proxy for where the keyboard and mouse are:

    sentinel PRESENT on this PC  ->  keyboard/mouse are here   ->  show HDMI1
    sentinel ABSENT  on this PC  ->  keyboard/mouse went away  ->  show USB-C

The monitor has no DDC/CI support, so the input is changed over the network via
its Tizen remote API. That path does not depend on the video cable, which is why
this single watcher can drive both directions.

The remote WebSocket is opened for each switch and closed again; the monitor
drops idle sockets, so there is nothing to be gained by holding one. The
handshake is started as soon as a change is seen, so it overlaps the settle wait
rather than being paid after it.
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
import threading
import time
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Callable, List, Optional

from . import usb
from .config import Config, log_path
from .monitor import SamsungMonitor

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s  %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
MAX_LOG_BYTES = 1024 * 1024

_ERROR_ALREADY_EXISTS = 183
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE


def acquire_single_instance(name: str = "Local\\SamsungInputSwitcherWatcher"):
    """Return a handle, or None when a watcher is already running.

    Two watchers fight over the monitor, and with a tray icon the duplicate is
    easy to start by accident.
    """
    handle = _kernel32.CreateMutexW(None, True, name)
    if not handle:
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def setup_logging(path: Optional[Path] = None, verbose: bool = False) -> Path:
    """Log to the rotating watcher log, and to the console when there is one.

    Under pythonw.exe there is no console at all and sys.stderr is None, so a
    stream handler would raise on every record.
    """
    target = Path(path) if path else log_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    file_handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=MAX_LOG_BYTES, backupCount=1, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    return target


class InputWatcher:
    """Polls the USB sentinels and drives the monitor to match."""

    def __init__(
        self,
        config: Config,
        *,
        sentinels: Optional[List[str]] = None,
        present_input: Optional[str] = None,
        absent_input: Optional[str] = None,
        poll_interval: Optional[float] = None,
        settle_interval: Optional[float] = None,
        debounce_interval: Optional[float] = None,
        what_if: bool = False,
        sync_on_start: bool = False,
        on_change: Optional[Callable[["InputWatcher"], None]] = None,
    ):
        self.config = config
        self.sentinels = list(sentinels or config.sentinel_instance_ids)
        self.present_input = present_input or config.present_input
        self.absent_input = absent_input or config.absent_input
        self.poll_interval = max(poll_interval or config.poll_interval, 0.02)
        self.settle_interval = max(
            settle_interval if settle_interval is not None else config.settle_interval, 0.0
        )
        self.debounce_interval = (
            debounce_interval if debounce_interval is not None else config.debounce_interval
        )
        self.what_if = what_if
        self.sync_on_start = sync_on_start
        self.on_change = on_change

        if not self.sentinels:
            raise ValueError("No sentinel devices configured (usbSwitch.sentinelInstanceIds).")

        self.present = False
        self.paused = False
        self.commanded_input: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_switch_at: Optional[float] = None

        self._monitor = SamsungMonitor(config) if not what_if else None
        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._last_fired = float("-inf")

    # ------------------------------------------------------------- properties

    @property
    def target_input(self) -> str:
        return self.present_input if self.present else self.absent_input

    @property
    def location(self) -> str:
        return "this PC" if self.present else "the laptop"

    # ---------------------------------------------------------- external API

    def request_switch(self, input_key: str) -> None:
        """Ask the watch loop to switch now (used by the tray menu)."""
        self._commands.put(("switch", input_key))

    def request_pause(self, paused: bool) -> None:
        self._commands.put(("pause", paused))

    def request_resync(self) -> None:
        self._commands.put(("resync", None))

    def stop(self) -> None:
        self._stop.set()
        self._commands.put(("stop", None))

    # --------------------------------------------------------------- internals

    def _read_present(self) -> bool:
        return usb.any_present(self.sentinels)

    def read_present(self) -> bool:
        """Current sentinel presence. Cheap, and touches nothing else."""
        return self._read_present()

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception:  # noqa: BLE001
                log.debug("Tray update failed", exc_info=True)

    def _get_monitor(self) -> SamsungMonitor:
        # Built in __init__ rather than lazily: the watch loop and the
        # pre-connect thread would otherwise race to create it and could end up
        # with two, one of which nothing can ever close.
        if self._monitor is None:
            raise RuntimeError("No monitor connection in WhatIf mode")
        return self._monitor

    def _begin_connect(self) -> None:
        """Start the WebSocket handshake while the USB bus settles.

        The handshake costs well over a second. Running it during the settle
        wait hides part of that without holding a socket open between presses.
        select_input() takes the same lock, so it simply waits for this to
        finish instead of opening a second connection.
        """
        if self.what_if or self.paused:
            return

        def _open() -> None:
            try:
                self._get_monitor().ensure_connected()
            except Exception as exc:  # noqa: BLE001
                log.debug("Pre-connect failed (%s); retrying when switching", exc)

        threading.Thread(target=_open, daemon=True, name="pre-connect").start()

    def _switch(self, input_key: str, *, manual: bool = False) -> None:
        if self.paused and not manual:
            log.info("  -> paused; not switching to %s", input_key)
            self._close_monitor()
            return

        if self.what_if:
            log.info("  -> [WhatIf] would switch monitor to %s", input_key)
            self.commanded_input = input_key
            self._last_fired = time.monotonic()
            self._notify()
            return

        started = time.monotonic()
        try:
            self._get_monitor().select_input(input_key)
            self.commanded_input = input_key
            self.last_error = None
            self.last_switch_at = time.time()
            log.info("  -> monitor switched to %s (%.1fs)", input_key, time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            # The sequence may have stopped anywhere, so what the monitor is
            # showing is now unknown. Leaving the old value here would let the
            # debounce coalescing suppress the corrective switch.
            self.commanded_input = None
            log.error("  -> FAILED switching to %s: %s", input_key, self.last_error)
        finally:
            # Never hold the socket between presses; the monitor closes idle
            # ones anyway and a half-dead socket fails mid-sequence.
            self._close_monitor()
            self._last_fired = time.monotonic()
            self._notify()

    def _close_monitor(self) -> None:
        if self._monitor is not None:
            self._monitor.close()

    def _handle_command(self, name: str, value) -> bool:
        """Returns False when the loop should exit."""
        if name == "stop":
            return False
        if name == "pause":
            self.paused = bool(value)
            log.info("watching %s", "paused" if self.paused else "resumed")
            self._notify()
        elif name == "switch":
            log.info("manual switch requested: %s", value)
            self._switch(value, manual=True)
        elif name == "resync":
            self.present = self._read_present()
            log.info("resync requested; keyboard/mouse on %s", self.location)
            self._switch(self.target_input, manual=True)
        return True

    def _warn_about_ambiguous_sentinels(self) -> None:
        for sentinel, roots in usb.ambiguous_sentinels(self.sentinels):
            log.warning(
                "WARNING: sentinel '%s' matches %d distinct physical devices; it cannot tell "
                "the switch apart from identical hardware. Use a full instance ID instead.",
                sentinel,
                len(roots),
            )
            for root in roots:
                log.warning("         %s", root)

    # -------------------------------------------------------------- main loop

    def run(self) -> int:
        log.info("=== watcher started ===")
        log.info("sentinels: %s", " | ".join(self.sentinels))
        log.info(
            "present->%s  absent->%s  poll=%dms  settle=%dms  debounce=%.1fs%s",
            self.present_input,
            self.absent_input,
            round(self.poll_interval * 1000),
            round(self.settle_interval * 1000),
            self.debounce_interval,
            "  [WhatIf]" if self.what_if else "",
        )

        self._warn_about_ambiguous_sentinels()

        self.present = self._read_present()
        log.info("initial state: keyboard/mouse on %s", self.location)
        self._notify()

        if self.sync_on_start:
            self._switch(self.target_input, manual=True)

        try:
            while not self._stop.is_set():
                # Waiting on the command queue doubles as the poll interval, so
                # tray actions are handled without adding latency.
                try:
                    name, value = self._commands.get(timeout=self.poll_interval)
                except queue.Empty:
                    pass
                else:
                    if not self._handle_command(name, value):
                        break
                    continue

                now = self._read_present()
                if now == self.present:
                    continue

                # The handshake is slower than the settle wait, so start it now
                # and let the two overlap.
                self._begin_connect()

                # Let the USB bus settle, then re-read. Removal is simultaneous
                # and arrival only needs the first sentinel, so this is short -
                # it only guards against a momentary drop.
                if self.settle_interval and self._stop.wait(self.settle_interval):
                    break
                now = self._read_present()
                if now == self.present:
                    log.info("transient change; state unchanged after settle")
                    self._close_monitor()
                    continue

                self.present = now
                log.info("keyboard/mouse are now on %s", self.location)
                self._notify()

                remaining = self.debounce_interval - (time.monotonic() - self._last_fired)
                if remaining > 0:
                    # Coalesce rather than drop: a switch skipped here would
                    # leave the monitor showing the wrong machine.
                    log.info("  -> within debounce window; waiting %.1fs", remaining)
                    if self._stop.wait(remaining):
                        break
                    self.present = self._read_present()
                    if self.target_input == self.commanded_input:
                        log.info("  -> state returned to %s; nothing to do", self.location)
                        self._close_monitor()
                        self._notify()
                        continue
                    self._notify()

                self._switch(self.target_input)
        finally:
            self._stop.set()
            self._close_monitor()
            log.info("=== watcher stopped ===")

        return 0
