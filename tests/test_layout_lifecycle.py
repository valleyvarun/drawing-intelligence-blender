"""Startup layout regressions without starting Blender or touching user preferences."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from drawing_app.core.state import AppState

ROOT: Path = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module}):
        spec.loader.exec_module(module)
    return module


def area(kind: str, x: int, width: int, pointer: int) -> MagicMock:
    result: MagicMock = MagicMock()
    result.type = kind
    result.x = x
    result.width = width
    result.as_pointer.return_value = pointer
    return result


class LayoutLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_module: ModuleType = load_module("_layout_test.core.state", "app/drawing_app/core/state.py")
        blender: ModuleType = ModuleType("_layout_test.core.blender")
        self.bpy: MagicMock = MagicMock()
        self.bpy.app.background = False
        self.bpy.app.timers.is_registered.return_value = False
        setattr(blender, "bpy", self.bpy)
        with patch.dict(sys.modules, {"_layout_test.core.state": self.state_module, blender.__name__: blender}):
            self.layout: ModuleType = load_module("_layout_test.ui.layout", "app/drawing_app/ui/layout.py")
        self.state: AppState = cast("AppState", self.state_module.get_state())

    def test_old_startup_widths_are_not_adopted(self) -> None:
        screen: SimpleNamespace = SimpleNamespace(
            areas=[area("IMAGE_EDITOR", 0, 350, 1), area("VIEW_3D", 354, 700, 2), area("IMAGE_EDITOR", 1058, 540, 3)]
        )
        self.assertFalse(self.layout._adopt(screen, 1600.0, 1.0))
        self.assertEqual(self.layout._ROLES, {})
        self.assertEqual((self.state.left_width, self.state.right_width), (220, 280))

    def test_correct_layout_keeps_widths_and_remembers_user_drag(self) -> None:
        left: MagicMock = area("IMAGE_EDITOR", 0, 218, 1)
        right: MagicMock = area("IMAGE_EDITOR", 1320, 278, 3)
        screen: SimpleNamespace = SimpleNamespace(areas=[left, area("VIEW_3D", 222, 1094, 2), right])
        self.assertTrue(self.layout._adopt(screen, 1600.0, 1.0))
        self.layout.remember_width(left, 1.0)
        self.layout.remember_width(right, 1.0)
        self.assertEqual((self.state.left_width, self.state.right_width), (220, 280))
        left.width = 258
        self.layout.remember_width(left, 1.0)
        self.assertEqual(self.state.left_width, 260)
        self.bpy.ops.screen.area_move.assert_not_called()

    def test_layout_sizes_scale_and_leave_room_for_viewport(self) -> None:
        self.assertEqual(self.layout.sidebar_widths(1600.0, 1.0), (220, 280))
        self.assertEqual(self.layout.sidebar_widths(3200.0, 2.0), (440, 560))
        self.assertEqual(self.layout.sidebar_widths(800.0, 1.0), (220, 280))
        self.state.left_width = 700
        self.state.right_width = 700
        self.assertEqual(self.layout.sidebar_widths(1600.0, 1.0), (700, 700))
        self.assertLessEqual(sum(self.layout.sidebar_widths(800.0, 1.0)), 772)

    def test_panel_cursor_resets_after_leaving_either_divider(self) -> None:
        left: MagicMock = area("IMAGE_EDITOR", 0, 218, 1)
        view: MagicMock = area("VIEW_3D", 222, 1094, 2)
        right: MagicMock = area("IMAGE_EDITOR", 1320, 278, 3)
        screen: SimpleNamespace = SimpleNamespace(areas=[left, view, right])
        self.assertTrue(self.layout._adopt(screen, 1600.0, 1.0))
        item: MagicMock
        for item in screen.areas:
            item.y, item.height = 20, 900
        context: MagicMock = MagicMock()
        context.window.screen = screen
        context.preferences.system.ui_scale = 1.0
        context.preferences.view.border_width = 1
        x: int
        for x in (218, 1320):
            self.layout.reset_panel_cursor(context, x, 400)
        context.window.cursor_set.assert_not_called()
        for x in (100, 1400, 100):
            self.layout.reset_panel_cursor(context, x, 400)
        self.assertEqual(context.window.cursor_set.call_count, 3)
        context.window.cursor_set.assert_called_with("DEFAULT")
        context.window.cursor_set.reset_mock()
        self.layout.reset_panel_cursor(context, 700, 400)
        self.layout.reset_panel_cursor(context, 100, 950)
        self.layout.reset_panel_cursor(context, 100, 10)
        context.window.cursor_set.assert_not_called()
        context.preferences.system.ui_scale = 2.0
        self.layout.reset_panel_cursor(context, 212, 400)
        context.window.cursor_set.assert_not_called()
        left.width = 300
        self.layout.reset_panel_cursor(context, 218, 400)
        context.window.cursor_set.assert_called_once_with("DEFAULT")

    def test_invalid_saved_widths_recover_without_replacing_custom_widths(self) -> None:
        folder: str
        with tempfile.TemporaryDirectory(dir=ROOT / ".checks") as folder:
            config: Path = Path(folder)
            path: Path = config / "drawing-preferences.json"
            with patch.object(self.state_module, "CONFIG", config):
                path.write_text(json.dumps({"left_width": -80, "right_width": 0}), encoding="utf-8")
                self.state_module.load_preferences()
                self.assertEqual((self.state.left_width, self.state.right_width), (220, 280))
                path.write_text(json.dumps({"left_width": 80, "right_width": 700}), encoding="utf-8")
                self.state_module.load_preferences()
                self.assertEqual((self.state.left_width, self.state.right_width), (80, 700))

    def test_resize_saves_after_settling_and_restores_next_session(self) -> None:
        left: MagicMock = area("IMAGE_EDITOR", 0, 218, 1)
        right: MagicMock = area("IMAGE_EDITOR", 1320, 278, 3)
        screen: SimpleNamespace = SimpleNamespace(areas=[left, area("VIEW_3D", 222, 1094, 2), right])
        self.assertTrue(self.layout._adopt(screen, 1600.0, 1.0))
        folder: str
        with tempfile.TemporaryDirectory(dir=ROOT / ".checks") as folder:
            config: Path = Path(folder)
            path: Path = config / "drawing-preferences.json"
            with (
                patch.object(self.state_module, "CONFIG", config),
                patch.object(self.layout.time, "monotonic", return_value=10.0),
            ):
                left.width = 158
                self.layout.remember_width(left, 1.0)
                self.bpy.app.timers.register.assert_called_once_with(self.layout._save_widths, first_interval=0.4)
                self.bpy.app.timers.is_registered.return_value = True
                right.width = 698
                self.layout.remember_width(right, 1.0)
                self.assertFalse(path.exists())
                self.assertAlmostEqual(self.layout._save_widths(), 0.4)
                self.bpy.app.timers.register.assert_called_once()
            with (
                patch.object(self.state_module, "CONFIG", config),
                patch.object(self.layout.time, "monotonic", return_value=10.5),
            ):
                self.assertIsNone(self.layout._save_widths())
                self.assertTrue(path.exists())
                self.state.left_width = 220
                self.state.right_width = 280
                self.state_module.load_preferences()
                self.assertEqual(self.layout.sidebar_widths(1600.0, 1.0), (160, 700))
                self.bpy.app.timers.register.reset_mock()
                self.bpy.app.timers.is_registered.return_value = False
                self.layout.remember_width(left, 1.0)
                self.layout.remember_width(right, 1.0)
                self.bpy.app.timers.register.assert_not_called()

    def test_shutdown_captures_resize_without_final_redraw(self) -> None:
        left: MagicMock = area("IMAGE_EDITOR", 0, 218, 1)
        right: MagicMock = area("IMAGE_EDITOR", 1320, 278, 3)
        screen: SimpleNamespace = SimpleNamespace(areas=[left, area("VIEW_3D", 222, 1094, 2), right])
        self.assertTrue(self.layout._adopt(screen, 1600.0, 1.0))
        context: MagicMock = MagicMock()
        context.window.screen = screen
        context.preferences.system.ui_scale = 1.0
        left.width = 308
        right.width = 398
        self.bpy.app.timers.is_registered.return_value = True
        self.layout.capture_widths(context)
        self.assertEqual((self.state.left_width, self.state.right_width), (310, 400))
        self.bpy.app.timers.unregister.assert_called_once_with(self.layout._save_widths)
        self.bpy.app.timers.register.assert_not_called()

    def test_config_override_stays_inside_application_folder(self) -> None:
        with patch.dict(os.environ, {"DRAWING_APP_CONFIG": str(ROOT.parent / "external-config")}):
            external: ModuleType = load_module("_layout_test.core.external_state", "app/drawing_app/core/state.py")
        self.assertEqual(external.CONFIG, ROOT / "runtime/config")
        with patch.dict(os.environ, {"DRAWING_APP_CONFIG": str(ROOT / ".checks/profiles/layout/config")}):
            internal: ModuleType = load_module("_layout_test.core.internal_state", "app/drawing_app/core/state.py")
        self.assertEqual(internal.CONFIG, ROOT / ".checks/profiles/layout/config")


if __name__ == "__main__":
    unittest.main()
