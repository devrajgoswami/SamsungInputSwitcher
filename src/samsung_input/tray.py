"""System-tray front end for the watcher.

Written directly against the Win32 shell API through ctypes so the watcher
gains no dependencies. The icon colour reflects where the keyboard and mouse
currently are, and the menu offers a manual override.

The process that hosts this must be a GUI-subsystem binary (pythonw.exe) -
powershell.exe and python.exe are console binaries, and under Windows 11 the
default terminal app ignores the hidden window style, which is what made a
terminal appear at logon.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

from .config import Config, log_path
from .watcher import InputWatcher

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_NULL = 0x0000
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_REFRESH = WM_APP + 2

WS_EX_TOOLWINDOW = 0x00000080
WS_OVERLAPPED = 0x00000000

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_ERROR = 0x01, 0x03

MF_STRING, MF_GRAYED, MF_SEPARATOR, MF_CHECKED = 0x0000, 0x0001, 0x0800, 0x0008
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

DIB_RGB_COLORS = 0
SM_CXSMICON = 49

ID_HEADER = 1
ID_MONITOR = 2
ID_PAUSE = 10
ID_RESYNC = 11
ID_LOG = 12
ID_EXIT = 99
ID_SWITCH_BASE = 100

# Icon colours, as (red, green, blue).
COLOR_PRESENT = (63, 185, 80)  # keyboard on this PC
COLOR_ABSENT = (88, 166, 255)  # keyboard on the laptop
COLOR_PAUSED = (139, 148, 158)
COLOR_ERROR = (248, 81, 73)

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_void_p, wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
user32.CreateIconIndirect.restype = wintypes.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]

# Handles are 64-bit; without explicit argtypes ctypes would pass them as int.
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.CreateBitmap.argtypes = [
    ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, wintypes.LPVOID
]


def _make_icon(size: int, color: Tuple[int, int, int]) -> wintypes.HICON:
    """Build a filled circle icon at runtime, so no .ico asset is needed."""
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = size
    header.biHeight = -size  # negative height => top-down rows
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0  # BI_RGB

    info = BITMAPINFO()
    info.bmiHeader = header

    bits = ctypes.c_void_p()
    color_bmp = gdi32.CreateDIBSection(
        None, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    if not color_bmp:
        raise ctypes.WinError(ctypes.get_last_error())

    red, green, blue = color
    centre = (size - 1) / 2.0
    radius = size / 2.0 - max(size / 16.0, 0.5)
    pixels = (ctypes.c_ubyte * (size * size * 4)).from_address(bits.value)

    # 3x3 supersampling gives a smooth edge without pulling in an imaging library.
    for y in range(size):
        for x in range(size):
            hits = 0
            for sy in range(3):
                for sx in range(3):
                    dx = x + (sx + 0.5) / 3.0 - 0.5 - centre
                    dy = y + (sy + 0.5) / 3.0 - 0.5 - centre
                    if dx * dx + dy * dy <= radius * radius:
                        hits += 1
            if not hits:
                continue
            alpha = int(255 * hits / 9)
            offset = (y * size + x) * 4
            # 32bpp icons expect premultiplied BGRA.
            pixels[offset + 0] = blue * alpha // 255
            pixels[offset + 1] = green * alpha // 255
            pixels[offset + 2] = red * alpha // 255
            pixels[offset + 3] = alpha

    mask_bmp = gdi32.CreateBitmap(size, size, 1, 1, None)

    icon_info = ICONINFO()
    icon_info.fIcon = True
    icon_info.hbmMask = mask_bmp
    icon_info.hbmColor = color_bmp
    handle = user32.CreateIconIndirect(ctypes.byref(icon_info))

    gdi32.DeleteObject(color_bmp)
    gdi32.DeleteObject(mask_bmp)

    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


class TrayApp:
    """Hidden top-level window that owns the notification icon."""

    CLASS_NAME = "SamsungInputSwitcherTray"

    def __init__(self, config: Config, watcher: InputWatcher):
        self.config = config
        self.watcher = watcher
        self.hwnd: Optional[int] = None
        self._icons: Dict[Tuple[int, int, int], wintypes.HICON] = {}
        self._icon_size = user32.GetSystemMetrics(SM_CXSMICON) or 16
        self._added = False
        self._pending_balloon: Optional[Tuple[str, str, int]] = None
        self._last_error_shown: Optional[str] = None
        self._switch_ids: List[Tuple[int, str, str]] = []
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        # ctypes must keep a reference or the callback is collected.
        self._wndproc = WNDPROC(self._on_message)

        watcher.on_change = self._request_refresh

    # ------------------------------------------------------------------ setup

    def _create_window(self) -> None:
        hinstance = kernel32.GetModuleHandleW(None)

        wnd_class = WNDCLASS()
        wnd_class.lpfnWndProc = self._wndproc
        wnd_class.hInstance = hinstance
        wnd_class.lpszClassName = self.CLASS_NAME
        if not user32.RegisterClassW(ctypes.byref(wnd_class)):
            raise ctypes.WinError(ctypes.get_last_error())

        # Never shown, so it stays out of the taskbar and Alt+Tab. It must be a
        # real top-level window rather than message-only, because the shell
        # broadcasts TaskbarCreated only to top-level windows.
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW, self.CLASS_NAME, "Samsung Input Switcher",
            WS_OVERLAPPED, 0, 0, 0, 0, None, None, hinstance, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _icon_for_state(self) -> wintypes.HICON:
        if self.watcher.last_error:
            color = COLOR_ERROR
        elif self.watcher.paused:
            color = COLOR_PAUSED
        elif self.watcher.present:
            color = COLOR_PRESENT
        else:
            color = COLOR_ABSENT

        if color not in self._icons:
            self._icons[color] = _make_icon(self._icon_size, color)
        return self._icons[color]

    def _tooltip(self) -> str:
        label = self._input_label(self.watcher.target_input)
        text = f"Samsung Input Switcher\nKeyboard/mouse: {self.watcher.location}\nMonitor: {label}"
        if self.watcher.paused:
            text += "\n(paused)"
        elif self.watcher.last_error:
            text += "\nLast switch failed"
        return text[:127]

    def _input_label(self, key: Optional[str]) -> str:
        if not key:
            return "unknown"
        spec = self.config.inputs.get(key)
        return spec.label if spec else key

    def _notify_icon(self, message: int, flags: int, **extra) -> bool:
        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAYICON
        if flags & NIF_ICON:
            data.hIcon = self._icon_for_state()
        if flags & NIF_TIP:
            data.szTip = self._tooltip()
        if flags & NIF_INFO:
            data.szInfoTitle = extra.get("title", "")[:63]
            data.szInfo = extra.get("text", "")[:255]
            data.dwInfoFlags = extra.get("info_flags", NIIF_INFO)
        return bool(shell32.Shell_NotifyIconW(message, ctypes.byref(data)))

    def _add_icon(self) -> None:
        self._added = self._notify_icon(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if not self._added:
            log.warning("Could not add the tray icon; the watcher keeps running without it.")

    def refresh(self) -> None:
        if not self._added:
            self._add_icon()
            return
        self._notify_icon(NIM_MODIFY, NIF_ICON | NIF_TIP)

        error = self.watcher.last_error
        if error and error != self._last_error_shown and self.config.tray_notify_errors:
            self._last_error_shown = error
            self._notify_icon(
                NIM_MODIFY, NIF_INFO,
                title="Samsung Input Switcher",
                text=f"Could not switch the monitor.\n{error}",
                info_flags=NIIF_ERROR,
            )
        elif not error:
            self._last_error_shown = None

    def _request_refresh(self, _watcher: InputWatcher) -> None:
        """Called from the watcher thread; the icon belongs to the UI thread."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_REFRESH, 0, 0)

    # ------------------------------------------------------------------- menu

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        if not menu:
            return

        append = user32.AppendMenuW
        append(menu, MF_STRING | MF_GRAYED, ID_HEADER, f"Keyboard/mouse: {self.watcher.location}")
        append(menu, MF_STRING | MF_GRAYED, ID_MONITOR,
               f"Monitor: {self._input_label(self.watcher.commanded_input or self.watcher.target_input)}")
        append(menu, MF_SEPARATOR, 0, None)

        self._switch_ids = []
        for offset, (key, spec) in enumerate(
            sorted(self.config.inputs.items(), key=lambda kv: kv[1].index)
        ):
            item_id = ID_SWITCH_BASE + offset
            self._switch_ids.append((item_id, key, spec.label))
            append(menu, MF_STRING, item_id, f"Switch to {spec.label}")

        append(menu, MF_SEPARATOR, 0, None)
        pause_flags = MF_STRING | (MF_CHECKED if self.watcher.paused else 0)
        append(menu, pause_flags, ID_PAUSE, "Pause following the USB switch")
        append(menu, MF_STRING, ID_RESYNC, "Resync monitor to keyboard")
        append(menu, MF_STRING, ID_LOG, "Open log")
        append(menu, MF_SEPARATOR, 0, None)
        append(menu, MF_STRING, ID_EXIT, "Exit")

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Required so the menu closes when the user clicks elsewhere.
        user32.SetForegroundWindow(self.hwnd)
        choice = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, self.hwnd, None
        )
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

        if choice:
            self._invoke(int(choice))

    def _invoke(self, command: int) -> None:
        if command == ID_EXIT:
            user32.DestroyWindow(self.hwnd)
            return
        if command == ID_PAUSE:
            self.watcher.request_pause(not self.watcher.paused)
            return
        if command == ID_RESYNC:
            self.watcher.request_resync()
            return
        if command == ID_LOG:
            try:
                os.startfile(str(log_path()))  # noqa: S606
            except OSError as exc:
                log.warning("Could not open the log: %s", exc)
            return
        for item_id, key, _label in self._switch_ids:
            if item_id == command:
                self.watcher.request_switch(key)
                return

    # --------------------------------------------------------------- messages

    def _on_message(self, hwnd, msg, wparam, lparam) -> int:
        if msg == WM_TRAYICON:
            if lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
                self._show_menu()
            return 0
        if msg == WM_REFRESH:
            self.refresh()
            return 0
        if msg == WM_COMMAND:
            self._invoke(wparam & 0xFFFF)
            return 0
        if msg == self._taskbar_created:
            # Explorer restarted and forgot every tray icon.
            self._added = False
            self._add_icon()
            return 0
        if msg == WM_QUERYENDSESSION:
            return 1
        if msg in (WM_CLOSE, WM_ENDSESSION):
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self._remove_icon()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _remove_icon(self) -> None:
        if self._added:
            self._notify_icon(NIM_DELETE, 0)
            self._added = False
        for handle in self._icons.values():
            user32.DestroyIcon(handle)
        self._icons.clear()

    # ------------------------------------------------------------------- run

    def run(self) -> int:
        self._create_window()
        # Seed the state before the icon appears, so the first tooltip does not
        # briefly claim the keyboard is somewhere it is not.
        try:
            self.watcher.present = self.watcher.read_present()
        except Exception:  # noqa: BLE001
            log.debug("Could not read initial USB state", exc_info=True)
        self._add_icon()

        worker = threading.Thread(target=self._run_watcher, daemon=True, name="watcher")
        worker.start()

        msg = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self.watcher.stop()
        worker.join(timeout=5)
        self._remove_icon()
        return 0

    def _run_watcher(self) -> None:
        try:
            self.watcher.run()
        except Exception:  # noqa: BLE001
            log.exception("Watcher stopped unexpectedly")
        finally:
            if self.hwnd:
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


def run_tray(config: Config, watcher: InputWatcher) -> int:
    return TrayApp(config, watcher).run()
