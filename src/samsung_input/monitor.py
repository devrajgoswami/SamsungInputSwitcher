"""Talk to the Samsung Smart Monitor's Tizen remote-control WebSocket API."""

from __future__ import annotations

import logging
import warnings
from typing import Iterable, List, Optional

import urllib3
from samsungtvws import SamsungTVWS

from . import discovery
from .config import Config, token_path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")

log = logging.getLogger(__name__)


class MonitorError(RuntimeError):
    pass


class SamsungMonitor:
    """Thin wrapper that knows how to drive the on-screen Source list.

    The monitor exposes no API for selecting an input directly, so the input is
    chosen by navigating the Source menu. The menu does not wrap at its ends,
    which makes it possible to reach a known position from any starting point:
    press UP enough times to land on the first entry, then step down.
    """

    def __init__(self, config: Config, host: Optional[str] = None):
        self.config = config
        self.host = host or config.effective_host()
        self._tv: Optional[SamsungTVWS] = None

    # ---------------------------------------------------------------- connect

    def _build(self, host: str) -> SamsungTVWS:
        return SamsungTVWS(
            host=host,
            port=self.config.port,
            token_file=str(token_path()),
            name=self.config.client_name,
            timeout=self.config.connect_timeout,
            # The library sleeps after every key press; default is 1s, which
            # makes a 7-key sequence take 7s. Timing is controlled explicitly
            # per call instead - see send_keys().
            key_press_delay=0,
        )

    def connect(self) -> None:
        """Open the WebSocket, rediscovering the monitor if the IP moved."""
        try:
            self._tv = self._build(self.host)
            self._tv.open()
            return
        except Exception as first_error:  # noqa: BLE001
            self._tv = None
            if not self.config.discovery_enabled:
                raise MonitorError(f"Cannot reach monitor at {self.host}: {first_error}")
            log.warning("Monitor unreachable at %s (%s); scanning LAN...", self.host, first_error)

        found = discovery.find_monitor(
            device_id=self.config.device_id,
            wifi_mac=self.config.wifi_mac,
            timeout=self.config.scan_timeout / 3,
        )
        if not found:
            raise MonitorError(
                f"Monitor not reachable at {self.host} and not found on the local network. "
                "Check that it is powered on and connected to Wi-Fi."
            )

        log.info("Monitor rediscovered at %s", found)
        self.host = found
        self.config.remember_host(found)
        self._tv = self._build(found)
        self._tv.open()

    def close(self) -> None:
        if self._tv is not None:
            try:
                self._tv.close()
            except Exception:  # noqa: BLE001
                pass
            self._tv = None

    def __enter__(self) -> "SamsungMonitor":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------- keys

    def send_keys(self, keys: Iterable[str], delay: Optional[float] = None) -> None:
        """Send keys, pausing `delay` seconds after each one.

        The pause is handed to the library rather than applied here, so each
        key costs one delay instead of two.
        """
        if self._tv is None:
            raise MonitorError("Not connected; call connect() first.")
        gap = self.config.key_delay if delay is None else delay
        for key in keys:
            self._tv.send_key(key, key_press_delay=gap)
            log.debug("sent %s (+%.2fs)", key, gap)

    # ----------------------------------------------------------------- inputs

    def key_sequence_for(self, input_key: str) -> List[str]:
        """Deterministic key sequence that selects `input_key` from any state."""
        spec = self.config.inputs.get(input_key)
        if spec is None:
            valid = ", ".join(sorted(self.config.inputs))
            raise MonitorError(f"Unknown input '{input_key}'. Valid inputs: {valid}")

        keys = ["KEY_SOURCE"]
        # Home to the top of the list. The list does not wrap, so pressing UP
        # (length - 1) times lands on the first entry from any starting point
        # and makes the current input irrelevant.
        keys += ["KEY_UP"] * max(self.config.list_length - 1, 1)
        keys += ["KEY_DOWN"] * spec.index
        keys.append("KEY_ENTER")
        return keys

    def select_input(self, input_key: str) -> None:
        spec = self.config.inputs[input_key]
        keys = self.key_sequence_for(input_key)

        if self._tv is None:
            raise MonitorError("Not connected; call connect() first.")

        # The Source menu needs longer to appear than a navigation step.
        self._tv.send_key(keys[0], key_press_delay=self.config.menu_delay)
        self.send_keys(keys[1:])
        log.info("Selected %s (%s)", input_key, spec.label)

    # -------------------------------------------------------------- inspection

    def device_info(self) -> Optional[dict]:
        return discovery.device_info(self.host)
