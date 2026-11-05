"""Windows finger navigation; pen input remains available to drawing tools."""

from __future__ import annotations

import ctypes
import math
import os
import sys
from ctypes import wintypes
from typing import Any, Literal, TypeAlias

from ..core.blender import bpy
from .navigation import orbit_view, pan_view, zoom_view

Point: TypeAlias = tuple[float, float]
Gesture: TypeAlias = Literal["", "pan", "orbit", "zoom", "ended"]
WM_POINTERUPDATE: int = 0x0245
WM_POINTERDOWN: int = 0x0246
WM_POINTERUP: int = 0x0247
WM_POINTERCAPTURECHANGED: int = 0x024C


def _center(points: list[Point]) -> Point:
    x: float = 0.0
    y: float = 0.0
    point: Point
    for point in points:
        x += point[0]
        y += point[1]
    return x / len(points), y / len(points)


class TouchNavigation:
    def __init__(self) -> None:
        self._hwnd: int | None = None
        self._user32: Any = None
        self._comctl32: Any = None
        self._callback: Any = None
        self._contacts: dict[int, Point] = {}
        self._previous: dict[int, Point] = {}
        self._start: dict[int, Point] = {}
        self._gesture: Gesture = ""
        self._rect: tuple[float, float, float, float] | None = None
        self._window_size: Point = (1.0, 1.0)
        self._area: Any = None
        self._region: Any = None
        self._failed: bool = False

    @property
    def active(self) -> bool:
        return bool(self._contacts)

    def _install(self) -> None:
        if sys.platform != "win32" or self._hwnd is not None or self._failed or bpy.app.background:
            return
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user32.GetClassNameW.restype = ctypes.c_int
        hwnd: int = int(self._user32.GetForegroundWindow() or 0)
        process: wintypes.DWORD = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        if int(process.value) != os.getpid():
            return
        name: Any = ctypes.create_unicode_buffer(128)
        self._user32.GetClassNameW(hwnd, name, 128)
        if "GHOST" not in name.value:
            return
        self._user32.GetPointerType.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT)]
        self._user32.GetPointerType.restype = wintypes.BOOL
        self._user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self._user32.ScreenToClient.restype = wintypes.BOOL
        self._user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._user32.GetClientRect.restype = wintypes.BOOL
        callback_type: Any = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )
        self._comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
        self._comctl32.SetWindowSubclass.restype = wintypes.BOOL
        self._comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t]
        self._comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
        self._comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        self._comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
        self._callback = callback_type(self._message)
        if self._comctl32.SetWindowSubclass(hwnd, self._callback, id(self), 0):
            self._hwnd = hwnd
        else:
            self._failed = True
            print("Touch navigation could not attach to the Blender window.")

    def _message(self, hwnd: int, message: int, wparam: int, lparam: int, subclass: int, data: int) -> int:
        pointer: int = wparam & 0xFFFF
        if message == WM_POINTERCAPTURECHANGED:
            self._contacts.pop(pointer, None)
        elif message in (WM_POINTERDOWN, WM_POINTERUPDATE, WM_POINTERUP):
            kind: wintypes.UINT = wintypes.UINT()
            if self._user32.GetPointerType(pointer, ctypes.byref(kind)) and kind.value == 2:
                point: wintypes.POINT = wintypes.POINT(ctypes.c_short(lparam).value, ctypes.c_short(lparam >> 16).value)
                self._user32.ScreenToClient(hwnd, ctypes.byref(point))
                client: wintypes.RECT = wintypes.RECT()
                self._user32.GetClientRect(hwnd, ctypes.byref(client))
                x: float = float(point.x) * self._window_size[0] / max(1, client.right - client.left)
                y: float = self._window_size[1] - float(point.y) * self._window_size[1] / max(
                    1, client.bottom - client.top
                )
                rect: tuple[float, float, float, float] | None = self._rect
                if (
                    message == WM_POINTERDOWN
                    and rect is not None
                    and rect[0] <= x < rect[0] + rect[2]
                    and rect[1] <= y < rect[1] + rect[3] - 36.0
                ):
                    self._contacts[pointer] = (x - rect[0], y - rect[1])
                if pointer in self._contacts:
                    if message == WM_POINTERUP:
                        self._contacts.pop(pointer, None)
                    elif rect is not None:
                        self._contacts[pointer] = (x - rect[0], y - rect[1])
                    return 0
        return int(self._comctl32.DefSubclassProc(hwnd, message, wparam, lparam))

    def tick(self, context: Any) -> None:
        if sys.platform != "win32" or bpy.app.background or context.window is None:
            return
        self._install()
        area: Any
        region: Any
        for area in context.window.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        self._area, self._region = area, region
                        self._rect = (float(region.x), float(region.y), float(region.width), float(region.height))
                        self._window_size = (float(context.window.width), float(context.window.height))
                        break
                break
        contacts: dict[int, Point] = dict(self._contacts)
        previous: dict[int, Point] = self._previous
        self._previous = contacts
        if contacts.keys() != previous.keys():
            self._start = contacts
            self._gesture = "" if len(contacts) > len(previous) else "ended"
            return
        if self._gesture == "ended" or len(contacts) not in (1, 2) or self._area is None:
            return
        keys: list[int] = sorted(contacts)
        now: list[Point] = []
        before: list[Point] = []
        start: list[Point] = []
        key: int
        for key in keys:
            now.append(contacts[key])
            before.append(previous[key])
            start.append(self._start[key])
        if len(now) == 1:
            self._gesture = "pan"
        elif not self._gesture:
            stretch: float = abs(math.dist(now[0], now[1]) - math.dist(start[0], start[1]))
            travel: float = math.dist(_center(now), _center(start))
            if max(stretch, travel) < 10.0:
                return
            self._gesture = "zoom" if stretch > travel else "orbit"
        if now == before:
            return
        center: Point = _center(now)
        last: Point = _center(before)
        rv3d: Any = self._area.spaces.active.region_3d
        if self._gesture == "zoom":
            zoom_view(self._region, rv3d, center, math.dist(before[0], before[1]) / max(math.dist(now[0], now[1]), 1.0))
        elif self._gesture == "orbit":
            orbit_view(rv3d, center[0] - last[0], center[1] - last[1], self._region.width, self._region.height)
        else:
            pan_view(self._region, rv3d, last, center)
        self._area.tag_redraw()

    def destroy(self) -> None:
        if self._hwnd is not None and self._callback is not None:
            self._comctl32.RemoveWindowSubclass(self._hwnd, self._callback, id(self))
        self._hwnd = None
        self._callback = None
        self._contacts.clear()
        self._previous.clear()
        self._start.clear()
