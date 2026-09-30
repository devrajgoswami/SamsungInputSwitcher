"""Locate the Samsung monitor on the LAN when its DHCP address changes."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
import subprocess
from typing import Iterator, List, Optional

import requests

INFO_PORT = 8001
INFO_TIMEOUT = 2.0


def local_ipv4_networks() -> List[ipaddress.IPv4Network]:
    """Best-effort list of directly attached IPv4 /24-or-smaller networks."""
    nets: List[ipaddress.IPv4Network] = []
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object { $_.PrefixOrigin -ne 'WellKnown' } | "
                "ForEach-Object { \"$($_.IPAddress)/$($_.PrefixLength)\" }",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        lines = []

    for line in lines:
        try:
            iface = ipaddress.ip_interface(line)
        except ValueError:
            continue
        if not isinstance(iface.ip, ipaddress.IPv4Address):
            continue
        if iface.ip.is_loopback or iface.ip.is_link_local:
            continue
        # Only scan reasonably sized subnets.
        if iface.network.prefixlen < 22:
            continue
        nets.append(iface.network)
    return nets


def _port_open(host: str, port: int, timeout: float) -> Optional[str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return host
    except OSError:
        return None


def scan_for_open_port(
    network: ipaddress.IPv4Network, port: int = INFO_PORT, timeout: float = 1.0
) -> Iterator[str]:
    hosts = [str(h) for h in network.hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        for result in pool.map(lambda h: _port_open(h, port, timeout), hosts):
            if result:
                yield result


def device_info(host: str, timeout: float = INFO_TIMEOUT) -> Optional[dict]:
    try:
        resp = requests.get(f"http://{host}:{INFO_PORT}/api/v2/", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def matches(info: dict, device_id: str = "", wifi_mac: str = "") -> bool:
    """True when this device is the monitor we are looking for."""
    if not info:
        return False
    if device_id and info.get("id") == device_id:
        return True
    dev = info.get("device", {}) or {}
    if wifi_mac:
        for key in ("wifiMac", "mac", "ethernetMac"):
            value = (dev.get(key) or "").upper()
            if value and value == wifi_mac.upper():
                return True
    return False


def find_monitor(
    device_id: str = "",
    wifi_mac: str = "",
    timeout: float = 1.0,
) -> Optional[str]:
    """Scan attached subnets for the monitor. Returns its IP, or None."""
    for network in local_ipv4_networks():
        for host in scan_for_open_port(network, INFO_PORT, timeout):
            info = device_info(host)
            if info and matches(info, device_id, wifi_mac):
                return host
    return None
