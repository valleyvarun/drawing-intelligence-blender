"""Exercise native hook cleanup with mocks; no Blender or desktop window is started."""

from __future__ import annotations

import ctypes
import importlib.util
import sys
import unittest
from _ctypes import CFuncPtr
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, patch, sentinel

if TYPE_CHECKING:
    from drawing_app.core.desktop import DesktopIdentity

ROOT: Path = Path(__file__).resolve().parents[1]


def _launcher_path() -> Path:
    return ROOT / "Drawing Intelligence.exe"


def _load_desktop() -> type[DesktopIdentity]:
    package: ModuleType = ModuleType("_desktop_lifecycle_test")
    search_paths: list[str] = []
    setattr(package, "__path__", search_paths)
    identity: ModuleType = ModuleType("_desktop_lifecycle_test.identity")
    setattr(identity, "APP_NAME", "Drawing Intelligence")
    setattr(identity, "APP_USER_MODEL_ID", "DrawIntel.1")
    setattr(identity, "launcher_path", _launcher_path)
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_desktop_lifecycle_test.desktop", ROOT / "app/drawing_app/core/desktop.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {package.__name__: package, identity.__name__: identity}):
        spec.loader.exec_module(module)
    return cast("type[DesktopIdentity]", getattr(module, "DesktopIdentity"))


DESKTOP_IDENTITY: type[DesktopIdentity] = _load_desktop()


class DesktopLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.desktop: DesktopIdentity = DESKTOP_IDENTITY()
        self.user32: Mock = Mock()
        self.comctl32: Mock = Mock()
        self.desktop._loaded = True
        self.desktop._user32 = cast(ctypes.WinDLL, self.user32)
        self.desktop._comctl32 = cast(ctypes.WinDLL, self.comctl32)
        self.desktop._callback = cast(CFuncPtr, sentinel.callback)
        self.desktop._windows = {101: True}

    def test_failed_detachment_is_retained_and_retried(self) -> None:
        self.user32.IsWindow.return_value = True
        self.comctl32.RemoveWindowSubclass.side_effect = (False, True)
        with patch.object(self.desktop, "_set_properties"):
            self.desktop.uninstall()
            self.assertEqual(self.desktop._windows, {101: True})
            self.assertIs(self.desktop._callback, sentinel.callback)
            self.desktop.uninstall()
        self.assertEqual(self.desktop._windows, {})
        self.assertEqual(self.comctl32.RemoveWindowSubclass.call_count, 2)

    def test_destroyed_window_does_not_receive_native_calls(self) -> None:
        self.user32.IsWindow.return_value = False
        properties: Mock
        with patch.object(self.desktop, "_set_properties") as properties:
            self.desktop.uninstall()
            properties.assert_not_called()
        self.assertEqual(self.desktop._windows, {})
        self.user32.RemovePropW.assert_not_called()
        self.comctl32.RemoveWindowSubclass.assert_not_called()

    def test_successful_cleanup_is_idempotent(self) -> None:
        self.user32.IsWindow.return_value = True
        self.comctl32.RemoveWindowSubclass.return_value = True
        with patch.object(self.desktop, "_set_properties"):
            self.desktop.uninstall()
            self.desktop.uninstall()
        self.assertEqual(self.desktop._windows, {})
        self.comctl32.RemoveWindowSubclass.assert_called_once_with(101, sentinel.callback, id(self.desktop))


if __name__ == "__main__":
    unittest.main()
