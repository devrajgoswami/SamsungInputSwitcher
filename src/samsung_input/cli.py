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
    if sys.stderr is None:
        # pythonw.exe has no streams at all; a StreamHandler would fail on
        # every record. The watch command installs its own file handler.
        logging.basicConfig(level=level, handlers=[logging.NullHandler()])
        return
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


def cmd_watch(cfg: Config, args: argparse.Namespace) -> int:
    from .watcher import InputWatcher, acquire_single_instance, setup_logging

    setup_logging(args.log, args.verbose)

    guard = acquire_single_instance()
    if guard is None:
        logging.error(
            "Another Samsung Input Switcher watcher is already running. "
            "Stop it first (Stop-ScheduledTask -TaskName 'Samsung Input Switcher')."
        )
        return 3

    try:
        watcher = InputWatcher(
            cfg,
            sentinels=args.sentinel or None,
            present_input=args.present_input,
            absent_input=args.absent_input,
            poll_interval=args.poll,
            settle_interval=args.settle,
            debounce_interval=args.debounce,
            what_if=args.what_if,
            sync_on_start=args.sync_on_start,
        )
    except ValueError as exc:
        logging.error("%s", exc)
        return 2

    use_tray = cfg.tray_enabled if args.tray is None else args.tray
    try:
        if use_tray:
            from .tray import run_tray

            return run_tray(cfg, watcher)
        return watcher.run()
    except KeyboardInterrupt:
        watcher.stop()
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

    s = sub.add_parser(
        "watch",
        help="Follow the USB switch and keep the monitor in sync (tray app)",
    )
    s.add_argument(
        "--tray",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show a system tray icon (default: tray.enabled in config.json)",
    )
    s.add_argument(
        "--what-if",
        action="store_true",
        help="Log every decision without sending anything to the monitor",
    )
    s.add_argument(
        "--sync-on-start",
        action="store_true",
        help="Also set the monitor to match the current state at startup",
    )
    s.add_argument("--sentinel", action="append", help="Override a sentinel instance ID (repeatable)")
    s.add_argument("--present-input", help="Input to select when the sentinels are present")
    s.add_argument("--absent-input", help="Input to select when the sentinels are absent")
    s.add_argument("--poll", type=float, help="Seconds between USB presence checks")
    s.add_argument("--settle", type=float, help="Seconds to wait before acting on a change")
    s.add_argument("--debounce", type=float, help="Minimum seconds between two monitor commands")
    s.add_argument("--log", help="Path to the watcher log")
    s.set_defaults(func=cmd_watch)

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
