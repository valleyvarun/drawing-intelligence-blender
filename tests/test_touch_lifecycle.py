"""Window-hook lifetime regressions; run with Python without starting Blender."""

from __future__ import annotations

import ctypes
import importlib.machinery
import importlib.util
import sys
import unittest
from _ctypes import CFuncPtr
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from drawing_app.viewport.touch import TouchNavigation

ROOT: Path = Path(__file__).resolve().parents[1]


def callback_result() -> int:
    return 0


def load_touch() -> ModuleType:
    """Load the real touch adapter with inert Blender and camera boundaries."""
    modules: dict[str, ModuleType] = {}
    name: str
    for name in (
        "drawing_app",
        "drawing_app.core",
        "drawing_app.core.blender",
        "drawing_app.viewport",
        "drawing_app.viewport.navigation",
    ):
        modules[name] = ModuleType(name)
    modules["drawing_app.core.blender"].__dict__["bpy"] = MagicMock()
    name = "drawing_app.viewport.navigation"
    function: str
    for function in ("orbit_view", "pan_view", "zoom_view"):
        modules[name].__dict__[function] = MagicMock()
    spec: importlib.machinery.ModuleSpec | None = importlib.util.spec_from_file_location(
        "drawing_app.viewport.touch", ROOT / "app/drawing_app/viewport/touch.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class TouchLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module: ModuleType = load_touch()
        navigation_type: type[TouchNavigation] = cast("type[TouchNavigation]", self.module.TouchNavigation)
        self.navigation: TouchNavigation = navigation_type()
        self.comctl32: MagicMock = MagicMock()
        self.user32: MagicMock = MagicMock()
        self.callback: CFuncPtr = ctypes.CFUNCTYPE(ctypes.c_ssize_t)(callback_result)
        self.navigation._hwnd = 123
        self.navigation._comctl32 = cast(ctypes.WinDLL, self.comctl32)
        self.navigation._user32 = cast(ctypes.WinDLL, self.user32)
        self.navigation._callback = self.callback
        self.comctl32.RemoveWindowSubclass.return_value = True
        self.comctl32.DefSubclassProc.return_value = 42
        self.navigation._contacts[1] = (10.0, 20.0)

    def retained(self) -> dict[int, TouchNavigation]:
        return cast("dict[int, TouchNavigation]", self.module._RETAINED)

    def test_successful_detach_releases_callback(self) -> None:
        self.navigation.destroy()
        self.comctl32.RemoveWindowSubclass.assert_called_once_with(123, self.callback, id(self.navigation))
        self.assertIsNone(self.navigation._hwnd)
        self.assertIsNone(self.navigation._callback)
        self.assertFalse(self.navigation.active)
        self.assertFalse(self.retained())
        self.navigation.destroy()
        self.comctl32.RemoveWindowSubclass.assert_called_once()

    def test_failed_detach_retains_callback_and_stops_touch_input(self) -> None:
        self.comctl32.RemoveWindowSubclass.return_value = False
        self.navigation.destroy()
        self.assertIs(self.retained()[id(self.navigation)], self.navigation)
        self.assertIs(self.navigation._callback, self.callback)
        self.assertFalse(self.navigation.active)
        result: int = self.navigation._message(123, self.module.WM_POINTERDOWN, 1, 0, 0, 0)
        self.assertEqual(result, 42)
        self.user32.GetPointerType.assert_not_called()
        self.navigation._install()
        self.navigation.tick(MagicMock())
        self.comctl32.SetWindowSubclass.assert_not_called()

    def test_failed_detach_can_be_retried(self) -> None:
        self.comctl32.RemoveWindowSubclass.return_value = False
        self.navigation.destroy()
        self.comctl32.RemoveWindowSubclass.return_value = True
        self.navigation.destroy()
        self.assertFalse(self.retained())
        self.assertIsNone(self.navigation._callback)
        self.assertEqual(self.comctl32.RemoveWindowSubclass.call_count, 2)

    def test_window_destruction_invalidates_handle_before_later_cleanup(self) -> None:
        self.comctl32.RemoveWindowSubclass.return_value = False
        self.navigation.destroy()
        result: int = self.navigation._message(123, self.module.WM_NCDESTROY, 0, 0, 0, 0)
        self.assertEqual(result, 42)
        self.assertIsNone(self.navigation._hwnd)
        # Keep the callback alive until execution has returned out of the native callback.
        self.assertIs(self.navigation._callback, self.callback)
        self.assertIs(self.retained()[id(self.navigation)], self.navigation)
        self.navigation.destroy()
        self.assertIsNone(self.navigation._callback)
        self.assertFalse(self.retained())
        self.assertEqual(self.comctl32.RemoveWindowSubclass.call_count, 2)


if __name__ == "__main__":
    unittest.main()
