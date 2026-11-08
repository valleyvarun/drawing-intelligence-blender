"""Windows desktop identity: taskbar group, window titles, and the launcher's ready signal.

The launcher opens the engine window in its saved place behind a splash screen. The window
gets the application's taskbar identity and titles here; once the workspace has drawn, the
ready property tells the launcher to close its splash.
"""

from __future__ import annotations

import ctypes
import re
import struct
import sys
import uuid
from _ctypes import CFuncPtr
from ctypes import wintypes

from .identity import APP_NAME, APP_USER_MODEL_ID, launcher_path

WM_DESTROY: int = 0x0002
WM_SETTEXT: int = 0x000C
WM_NCDESTROY: int = 0x0082
GW_OWNER: int = 4
SW_SHOWNORMAL: int = 1
VT_EMPTY: int = 0
VT_LPWSTR: int = 31
WINDOW_CLASS: str = "GHOST_WindowClass"
# Read by launcher/Launcher.cs.
READY_PROPERTY: str = "DrawingIntelligence.Ready"
IID_PROPERTY_STORE: bytes = uuid.UUID("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99").bytes_le
APP_USER_MODEL: bytes = uuid.UUID("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3").bytes_le
KEY_ID: int = 5
KEY_RELAUNCH_COMMAND: int = 2
KEY_RELAUNCH_ICON: int = 3
KEY_RELAUNCH_NAME: int = 4
_ENGINE_SUFFIX: re.Pattern[str] = re.compile(r" - Blender \d.*$")


def brand_title(text: str, main: bool) -> str:
    """Replace the engine's product name in a window title; repeated calls are harmless."""
    suffix: str = " - " + APP_NAME
    if text == APP_NAME or text.endswith(suffix):
        return text
    if main:
        base: str = _ENGINE_SUFFIX.sub("", text)
        if base == text and text.startswith("Blender"):
            base = ""
        base = base.replace("(Unsaved)", "Untitled").strip()
        return base + suffix if base else APP_NAME
    if text in ("", "Blender"):
        return APP_NAME
    return text.replace("Blender", APP_NAME) if "Blender" in text else text + suffix


def _property_key(pid: int) -> ctypes.Array[ctypes.c_char]:
    return ctypes.create_string_buffer(APP_USER_MODEL + struct.pack("<I", pid), 20)


class DesktopIdentity:
    def __init__(self) -> None:
        self._user32: ctypes.WinDLL
        self._shell32: ctypes.WinDLL
        self._comctl32: ctypes.WinDLL
        self._kernel32: ctypes.WinDLL
        self._callback: CFuncPtr
        self._enum_type: type[CFuncPtr]
        self._loaded: bool = False
        self._windows: dict[int, bool] = {}
        self._failed: bool = False

    @property
    def available(self) -> bool:
        return sys.platform == "win32" and not self._failed

    def _load(self) -> bool:
        if self._loaded:
            return True
        if not self.available:
            return False
        error: OSError | AttributeError
        try:
            user32: ctypes.WinDLL = ctypes.WinDLL("user32", use_last_error=True)
            shell32: ctypes.WinDLL = ctypes.WinDLL("shell32", use_last_error=True)
            comctl32: ctypes.WinDLL = ctypes.WinDLL("comctl32", use_last_error=True)
            kernel32: ctypes.WinDLL = ctypes.WinDLL("kernel32", use_last_error=True)
            enum_type: type[CFuncPtr] = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumThreadWindows.argtypes = [wintypes.DWORD, enum_type, wintypes.LPARAM]
            user32.EnumThreadWindows.restype = wintypes.BOOL
            user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetClassNameW.restype = ctypes.c_int
            user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetWindow.restype = wintypes.HWND
            user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetWindowTextW.restype = ctypes.c_int
            user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
            user32.SetWindowTextW.restype = wintypes.BOOL
            user32.IsWindow.argtypes = [wintypes.HWND]
            user32.IsWindow.restype = wintypes.BOOL
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.IsZoomed.argtypes = [wintypes.HWND]
            user32.IsZoomed.restype = wintypes.BOOL
            user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
            user32.SetPropW.restype = wintypes.BOOL
            user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
            user32.RemovePropW.restype = wintypes.HANDLE
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
            shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
            shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p]
            shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
            subclass_type: type[CFuncPtr] = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                wintypes.HWND,
                wintypes.UINT,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
            )
            comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, subclass_type, ctypes.c_size_t, ctypes.c_size_t]
            comctl32.SetWindowSubclass.restype = wintypes.BOOL
            comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, subclass_type, ctypes.c_size_t]
            comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
            comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
            comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
            self._callback = subclass_type(self._message)
            self._enum_type = enum_type
            self._user32, self._shell32, self._comctl32, self._kernel32 = user32, shell32, comctl32, kernel32
            self._loaded = True
            return True
        except (OSError, AttributeError) as error:
            self._failed = True
            print(f"Desktop identity unavailable: {error}")
            return False

    def install(self) -> None:
        """Group future windows and jump lists under the application, then brand open windows."""
        if not self._load():
            return
        self._shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        self.refresh()

    def windows(self) -> list[int]:
        """Top-level engine windows created by this thread."""
        if not self._load():
            return []
        found: list[int] = []
        name: ctypes.Array[ctypes.c_wchar] = ctypes.create_unicode_buffer(64)

        def visit(hwnd: int | None, _param: int) -> bool:
            if hwnd:
                self._user32.GetClassNameW(hwnd, name, 64)
                if name.value == WINDOW_CLASS:
                    found.append(int(hwnd))
            return True

        callback: CFuncPtr = self._enum_type(visit)
        self._user32.EnumThreadWindows(self._kernel32.GetCurrentThreadId(), callback, 0)
        return found

    def main_window(self) -> int:
        hwnd: int
        for hwnd in self.windows():
            if not self._user32.GetWindow(hwnd, GW_OWNER):
                return hwnd
        return 0

    def refresh(self) -> None:
        """Brand windows Blender created since the last call; forget destroyed ones."""
        if not self._load():
            return
        hwnd: int
        for hwnd in list(self._windows):
            if not self._user32.IsWindow(hwnd):
                del self._windows[hwnd]
        for hwnd in self.windows():
            if hwnd in self._windows:
                continue
            main: bool = not self._user32.GetWindow(hwnd, GW_OWNER)
            if not self._comctl32.SetWindowSubclass(hwnd, self._callback, id(self), 0):
                continue
            self._windows[hwnd] = main
            self._set_properties(hwnd, True)
            title: ctypes.Array[ctypes.c_wchar] = ctypes.create_unicode_buffer(1024)
            self._user32.GetWindowTextW(hwnd, title, 1024)
            # The subclass rewrites the text on its way through.
            self._user32.SetWindowTextW(hwnd, title.value)

    def _set_properties(self, hwnd: int, enabled: bool) -> None:
        store: ctypes.c_void_p = ctypes.c_void_p()
        iid: ctypes.Array[ctypes.c_char] = ctypes.create_string_buffer(IID_PROPERTY_STORE, 16)
        if self._shell32.SHGetPropertyStoreForWindow(hwnd, iid, ctypes.byref(store)) < 0 or not store.value:
            return
        vtable: ctypes._Pointer[ctypes.c_void_p] = ctypes.cast(
            store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        set_value: CFuncPtr = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            vtable[6]
        )
        release: CFuncPtr = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        launcher: str = str(launcher_path())
        values: list[tuple[int, str]] = []
        if launcher_path().is_file():
            # Pinned buttons and jump lists start the application, not the engine.
            values.extend(
                (
                    (KEY_RELAUNCH_COMMAND, f'"{launcher}"'),
                    (KEY_RELAUNCH_ICON, f"{launcher},0"),
                    (KEY_RELAUNCH_NAME, APP_NAME),
                )
            )
        values.append((KEY_ID, APP_USER_MODEL_ID))
        try:
            key: int
            text: str
            for key, text in values:
                buffer: ctypes.Array[ctypes.c_wchar] = ctypes.create_unicode_buffer(text)
                pointer: int = ctypes.addressof(buffer) if enabled else 0
                variant: ctypes.Array[ctypes.c_char] = ctypes.create_string_buffer(
                    struct.pack("@HHHHPP", VT_LPWSTR if enabled else VT_EMPTY, 0, 0, 0, pointer, 0)
                )
                set_value(store, _property_key(key), variant)
        finally:
            release(store)

    def _message(self, hwnd: int | None, message: int, wparam: int, lparam: int, _subclass: int, _data: int) -> int:
        handle: int = int(hwnd or 0)
        error: OSError | ValueError | TypeError
        try:
            if message == WM_SETTEXT and lparam and handle in self._windows:
                text: str = ctypes.wstring_at(lparam)
                branded: str = brand_title(text, self._windows[handle])
                if branded != text:
                    replacement: ctypes.Array[ctypes.c_wchar] = ctypes.create_unicode_buffer(branded)
                    return int(self._comctl32.DefSubclassProc(hwnd, message, wparam, ctypes.addressof(replacement)))
            elif message == WM_DESTROY and handle in self._windows:
                # Properties left on a closing window are leaked by the shell.
                self._set_properties(handle, False)
            elif message == WM_NCDESTROY:
                self._comctl32.RemoveWindowSubclass(hwnd, self._callback, id(self))
                self._windows.pop(handle, None)
        except (OSError, ValueError, TypeError) as error:
            print(f"Desktop identity: {error}")
        return int(self._comctl32.DefSubclassProc(hwnd, message, wparam, lparam))

    def is_visible(self) -> bool:
        hwnd: int = self.main_window()
        return bool(hwnd) and bool(self._user32.IsWindowVisible(hwnd))

    def is_maximized(self) -> bool:
        hwnd: int = self.main_window()
        return bool(hwnd) and bool(self._user32.IsZoomed(hwnd))

    def reveal(self) -> None:
        """Bring the drawn workspace forward and tell the launcher to close its splash."""
        hwnd: int = self.main_window()
        if not hwnd:
            return
        if not self._user32.IsWindowVisible(hwnd):
            self._user32.ShowWindow(hwnd, SW_SHOWNORMAL)
        self._user32.SetForegroundWindow(hwnd)
        self._user32.SetPropW(hwnd, READY_PROPERTY, 1)

    def uninstall(self) -> None:
        if not self._loaded:
            return
        hwnd: int
        for hwnd in list(self._windows):
            if not self._user32.IsWindow(hwnd):
                self._windows.pop(hwnd, None)
                continue
            self._user32.RemovePropW(hwnd, READY_PROPERTY)
            self._set_properties(hwnd, False)
            if self._comctl32.RemoveWindowSubclass(hwnd, self._callback, id(self)):
                self._windows.pop(hwnd, None)
            # Keep failed detachments tracked until a retry or WM_NCDESTROY.


_IDENTITY: DesktopIdentity = DesktopIdentity()


def identity() -> DesktopIdentity:
    return _IDENTITY
