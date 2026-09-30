"""Command line interface for SamsungInputSwitcher."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from . import discovery
from .config import Config, load_config, resolved_host_path, token_path
from .monitor import MonitorError, SamsungMonitor


def _setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s  %(message)s")


def cmd_switch(cfg: Config, args: argparse.Namespace) -> int:
    try:
        with SamsungMonitor(cfg, host=args.host) as mon:
            mon.select_input(args.input)
    except MonitorError as exc:
        logging.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1
    return 0


def cmd_keys(cfg: Config, args: argparse.Namespace) -> int:
    try:
        with SamsungMonitor(cfg, host=args.host) as mon:
            mon.send_keys(args.keys, delay=args.delay)
    except Exception as exc:  # noqa: BLE001
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1
    return 0


def cmd_pair(cfg: Config, args: argparse.Namespace) -> int:
    print("Connecting to the monitor.")
    print("If a prompt appears on screen, choose Allow.")
    sys.stdout.flush()
    try:
        with SamsungMonitor(cfg, host=args.host) as mon:
            print(f"Connected to {mon.host}.")
    except Exception as exc:  # noqa: BLE001
        logging.error("Pairing failed: %s: %s", type(exc).__name__, exc)
        return 1

    tp = token_path()
    if tp.exists() and tp.read_text(encoding="utf-8").strip():
        print(f"Token stored at {tp}")
        return 0
    logging.error("No token was written. The prompt may have been declined.")
    return 1


def cmd_info(cfg: Config, args: argparse.Namespace) -> int:
    host = args.host or cfg.effective_host()
    info = discovery.device_info(host)
    if not info:
        logging.warning("No response from %s; scanning the network...", host)
        found = discovery.find_monitor(cfg.device_id, cfg.wifi_mac, timeout=1.0)
        if not found:
            logging.error("Monitor not found.")
            return 1
        host = found
        cfg.remember_host(found)
        info = discovery.device_info(host)

    dev = (info or {}).get("device", {})
    print(f"host          : {host}")
    print(f"name          : {dev.get('name')}")
    print(f"modelName     : {dev.get('modelName')}")
    print(f"powerState    : {dev.get('PowerState')}")
    print(f"networkType   : {dev.get('networkType')}")
    print(f"wifiMac       : {dev.get('wifiMac')}")
    print(f"id            : {(info or {}).get('id')}")
    print(f"token file    : {token_path()}  (exists={token_path().exists()})")
    override = resolved_host_path()
    print(f"host override : {override if override.exists() else '(none)'}")
    print()
    print("Configured inputs:")
    for key, spec in sorted(cfg.inputs.items(), key=lambda kv: kv[1].index):
        print(f"  {key:<8} index={spec.index}  {spec.label}")
    return 0


def cmd_discover(cfg: Config, args: argparse.Namespace) -> int:
    print("Scanning local subnets for Samsung devices on port 8001 ...")
    hits = 0
    for network in discovery.local_ipv4_networks():
        print(f"  network {network}")
        for host in discovery.scan_for_open_port(network, timeout=1.0):
            info = discovery.device_info(host)
            dev = (info or {}).get("device", {})
            name = dev.get("name", "?")
            model = dev.get("modelName", "?")
            mark = "  <== configured monitor" if discovery.matches(
                info or {}, cfg.device_id, cfg.wifi_mac
            ) else ""
            print(f"    {host:<16} {model:<20} {name}{mark}")
            hits += 1
    if not hits:
        print("  nothing found")
        return 1
    return 0


def cmd_sequence(cfg: Config, args: argparse.Namespace) -> int:
    mon = SamsungMonitor(cfg)
    print(json.dumps(mon.key_sequence_for(args.input), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="samsung-input",
        description="Switch the input source of a Samsung Smart Monitor over the network.",
    )
    p.add_argument("--config", help="Path to config.json")
    p.add_argument("--host", help="Override the monitor IP address")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")

    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("switch", help="Select a monitor input")
    s.add_argument("input", help="Input key, e.g. hdmi1 / usbc / tv")
    s.set_defaults(func=cmd_switch)

    s = sub.add_parser("pair", help="Pair with the monitor and store a token")
    s.set_defaults(func=cmd_pair)

    s = sub.add_parser("info", help="Show monitor and configuration details")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("discover", help="Scan the LAN for Samsung devices")
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("keys", help="Send raw remote key codes")
    s.add_argument("keys", nargs="+", help="e.g. KEY_SOURCE KEY_DOWN KEY_ENTER")
    s.add_argument("--delay", type=float, default=None)
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("sequence", help="Print the key sequence for an input (no I/O)")
    s.add_argument("input")
    s.set_defaults(func=cmd_sequence)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.quiet)
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        logging.error("%s", exc)
        return 2
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
