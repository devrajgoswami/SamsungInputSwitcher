"""Configuration loading and on-disk state paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

# Repo root is three levels up from this file: src/samsung_input/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


def state_dir() -> Path:
    """Directory for secrets and cached state, kept outside the repo."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(base) / "SamsungInputSwitcher"
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return state_dir() / "token.txt"


def resolved_host_path() -> Path:
    return state_dir() / "resolved_host.json"


def log_path() -> Path:
    return state_dir() / "switcher.log"


@dataclass
class InputSpec:
    key: str
    index: int
    label: str


@dataclass
class Config:
    raw: Dict[str, Any]
    path: Path

    host: str = ""
    port: int = 8002
    client_name: str = "SamsungInputSwitcher"
    device_id: str = ""
    wifi_mac: str = ""

    list_length: int = 3
    inputs: Dict[str, InputSpec] = field(default_factory=dict)

    menu_delay: float = 1.3
    key_delay: float = 0.45
    connect_timeout: int = 15

    discovery_enabled: bool = True
    scan_timeout: float = 3.0

    sentinel_instance_ids: List[str] = field(default_factory=list)
    poll_interval: float = 0.4
    present_input: str = "hdmi1"
    absent_input: str = "usbc"

    @classmethod
    def from_dict(cls, data: Dict[str, Any], path: Path) -> "Config":
        mon = data.get("monitor", {})
        src = data.get("sourceList", {})
        tim = data.get("timing", {})
        dis = data.get("discovery", {})
        usb = data.get("usbSwitch", {})

        inputs = {
            key: InputSpec(key=key, index=int(spec["index"]), label=spec.get("label", key))
            for key, spec in src.get("inputs", {}).items()
        }

        sentinels = usb.get("sentinelInstanceIds")
        if not sentinels:
            single = usb.get("sentinelInstanceId")
            sentinels = [single] if single else []

        return cls(
            raw=data,
            path=path,
            host=mon.get("host", ""),
            port=int(mon.get("port", 8002)),
            client_name=mon.get("clientName", "SamsungInputSwitcher"),
            device_id=mon.get("deviceId", ""),
            wifi_mac=(mon.get("wifiMac", "") or "").upper(),
            list_length=int(src.get("length", len(inputs) or 3)),
            inputs=inputs,
            menu_delay=float(tim.get("menuDelaySeconds", 1.3)),
            key_delay=float(tim.get("keyDelaySeconds", 0.45)),
            connect_timeout=int(tim.get("connectTimeoutSeconds", 15)),
            discovery_enabled=bool(dis.get("enabled", True)),
            scan_timeout=float(dis.get("portScanTimeoutSeconds", 3)),
            sentinel_instance_ids=list(sentinels),
            poll_interval=float(usb.get("pollIntervalSeconds", 0.4)),
            present_input=usb.get("presentInput", "hdmi1"),
            absent_input=usb.get("absentInput", "usbc"),
        )

    def effective_host(self) -> str:
        """Prefer a cached host discovered after the configured one failed."""
        cache = resolved_host_path()
        if cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                cached = data.get("host")
                if cached:
                    return cached
            except (OSError, ValueError):
                pass
        return self.host

    def remember_host(self, host: str) -> None:
        if host == self.host:
            # Back to the configured address; drop the override.
            resolved_host_path().unlink(missing_ok=True)
            return
        resolved_host_path().write_text(
            json.dumps({"host": host}, indent=2), encoding="utf-8"
        )


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg_path = Path(path) if path else Path(
        os.environ.get("SAMSUNG_INPUT_CONFIG", DEFAULT_CONFIG_PATH)
    )
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return Config.from_dict(data, cfg_path)
