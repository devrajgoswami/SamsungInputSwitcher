"""Fast, dependency-free check for whether a USB device is currently present.

Get-PnpDevice / a full CIM query costs roughly 300 ms, which is far too slow to
poll. CM_Get_Device_ID_List from cfgmgr32 returns the list of present device
instance IDs in well under a millisecond, so the watcher can poll several times
a second at no measurable cost and needs no device-change event subscription.

This is the Python counterpart of scripts/UsbPresence.ps1, which is still used
by the standalone tools.
"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from typing import List, Sequence, Tuple

_CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001
_CM_GETIDLIST_FILTER_PRESENT = 0x00000100
_CR_SUCCESS = 0
_CR_BUFFER_SMALL = 0x0000001A

_cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)

_cfgmgr32.CM_Get_Device_ID_List_SizeW.argtypes = [
    ctypes.POINTER(wintypes.ULONG),
    wintypes.LPCWSTR,
    wintypes.ULONG,
]
_cfgmgr32.CM_Get_Device_ID_List_SizeW.restype = wintypes.ULONG

_cfgmgr32.CM_Get_Device_ID_ListW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.ULONG,
    wintypes.ULONG,
]
_cfgmgr32.CM_Get_Device_ID_ListW.restype = wintypes.ULONG

# Reused between polls so a sub-second loop does not churn the allocator.
_buffer: ctypes.Array | None = None

_ROOT_NODE = re.compile(r"^VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4}$")


def present_ids(enumerator: str = "USB") -> List[str]:
    """Device instance IDs of all present devices under `enumerator`."""
    global _buffer

    flags = _CM_GETIDLIST_FILTER_PRESENT
    if enumerator:
        flags |= _CM_GETIDLIST_FILTER_ENUMERATOR

    # A device can arrive between the size call and the list call - which is
    # exactly what happens during the switch's enumeration burst - so the list
    # may be longer than the size just reported. Retry rather than truncate.
    for _ in range(3):
        length = wintypes.ULONG(0)
        if _cfgmgr32.CM_Get_Device_ID_List_SizeW(
            ctypes.byref(length), enumerator, flags
        ) != _CR_SUCCESS:
            return []
        if length.value == 0:
            return []

        if _buffer is None or len(_buffer) < length.value:
            _buffer = ctypes.create_unicode_buffer(length.value)

        result = _cfgmgr32.CM_Get_Device_ID_ListW(enumerator, _buffer, len(_buffer), flags)
        if result == _CR_BUFFER_SMALL:
            _buffer = None
            continue
        if result != _CR_SUCCESS:
            return []

        # The result is a MULTI_SZ: NUL-separated, double-NUL terminated. The
        # buffer is reused and may be longer than this call's data, so trust
        # the terminator rather than the size reported above.
        raw = _buffer[:]
        end = raw.find("\0\0")
        if end == -1:
            end = len(raw)
        return [part for part in raw[:end].split("\0") if part]

    return []


def matches_any(ids: Sequence[str], sentinels: Sequence[str]) -> bool:
    """True when any present ID starts with any sentinel (case-insensitive)."""
    upper = [s.upper() for s in sentinels if s]
    if not upper:
        return False
    for device_id in ids:
        candidate = device_id.upper()
        for sentinel in upper:
            if candidate.startswith(sentinel):
                return True
    return False


def any_present(sentinels: Sequence[str], enumerator: str = "USB") -> bool:
    return matches_any(present_ids(enumerator), sentinels)


def ambiguous_sentinels(sentinels: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """Sentinels that cannot tell the USB switch apart from identical hardware.

    Only root device nodes are counted. A composite device such as a wireless
    receiver legitimately produces several &MI_xx interface nodes that all
    belong to one physical device, whereas two identical hubs produce two
    roots. Counting every match would fire on the former.
    """
    ids = present_ids()
    problems: List[Tuple[str, List[str]]] = []

    for sentinel in sentinels:
        # A sentinel that already includes an instance path is specific enough.
        if len(sentinel.split("\\")) > 2:
            continue
        prefix = sentinel.upper()
        roots = [
            device_id
            for device_id in ids
            if device_id.upper().startswith(prefix)
            and _ROOT_NODE.match(device_id.split("\\")[1] if "\\" in device_id else "")
        ]
        if len(roots) > 1:
            problems.append((sentinel, roots))

    return problems
