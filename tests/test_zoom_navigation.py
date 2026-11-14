"""Camera-to-surface zoom regressions without Blender or a GPU."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT: Path = Path(__file__).resolve().parents[1]


@dataclass
class Vector:
    x: float
    y: float
    z: float

    def copy(self) -> Vector:
        return Vector(self.x, self.y, self.z)

    def __add__(self, other: Vector) -> Vector:
        return Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vector) -> Vector:
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, factor: float) -> Vector:
        return Vector(self.x * factor, self.y * factor, self.z * factor)

    def dot(self, other: Vector) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y, self.z)[index]


def vector(values: tuple[float, float, float]) -> Vector:
    return Vector(*values)


def identity(value: Vector) -> Vector:
    return value


def anchor(_region: object, _view: object, _position: tuple[float, float], pivot: Vector) -> Vector:
    return pivot.copy()


class ZoomNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        blender: ModuleType = ModuleType("_zoom_test.core.blender")
        name: str
        for name in ("Matrix", "Quaternion", "bpy", "view3d_utils"):
            setattr(blender, name, MagicMock())
        setattr(blender, "Vector", vector)
        self.utils: MagicMock = blender.view3d_utils
        self.utils.region_2d_to_location_3d.side_effect = anchor
        state: ModuleType = ModuleType("_zoom_test.core.state")
        setattr(state, "get_state", MagicMock())
        setattr(state, "save_preferences", MagicMock())
        spec: ModuleSpec | None = importlib.util.spec_from_file_location(
            "_zoom_test.viewport.navigation", ROOT / "app/drawing_app/viewport/navigation.py"
        )
        assert spec is not None and spec.loader is not None
        self.navigation: ModuleType = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {blender.__name__: blender, state.__name__: state, spec.name: self.navigation}):
            spec.loader.exec_module(self.navigation)
        rotation: MagicMock = MagicMock()
        rotation.__matmul__.side_effect = identity
        self.view: SimpleNamespace = SimpleNamespace(
            view_perspective="PERSP",
            view_location=Vector(0, 0, 0),
            view_distance=12.0,
            view_rotation=rotation,
            update=MagicMock(),
        )
        self.context: MagicMock = MagicMock()
        self.context.scene.ray_cast.return_value = (True, Vector(0, 0, 8))

    def test_zoom_approaches_surface_in_front_of_old_pivot_without_passing_it(self) -> None:
        self.navigation.zoom_view(None, self.view, (0, 0), 0.5, self.context)
        self.assertEqual(self.view.view_location.z + self.view.view_distance, 10.0)
        step: int
        for step in range(20):
            self.navigation.zoom_view(None, self.view, (0, 0), 0.5, self.context)
            self.assertGreaterEqual(self.view.view_location.z + self.view.view_distance, 8.02, step)

    def test_empty_space_and_orthographic_zoom_keep_original_anchor(self) -> None:
        self.context.scene.ray_cast.return_value = (False, None)
        self.navigation.zoom_view(None, self.view, (0, 0), 0.5, self.context)
        self.assertEqual(self.view.view_location, Vector(0, 0, 0))
        self.assertEqual(self.view.view_distance, 6.0)
        self.context.scene.ray_cast.reset_mock()
        self.view.view_perspective = "ORTHO"
        self.navigation.zoom_view(None, self.view, (0, 0), 0.5, self.context)
        self.assertEqual(self.view.view_distance, 3.0)
        self.context.scene.ray_cast.assert_not_called()

    def test_extreme_zoom_stays_outside_near_clip_and_invalid_factor_is_ignored(self) -> None:
        self.navigation.zoom_view(None, self.view, (0, 0), 1e-12, self.context)
        self.assertEqual(self.view.view_distance, 0.02)
        self.view.update.reset_mock()
        self.navigation.zoom_view(None, self.view, (0, 0), float("nan"), self.context)
        self.view.update.assert_not_called()

    def align(self, view: SimpleNamespace, axis: int, sign: int) -> None:
        view.view_rotation.__matmul__.side_effect = None
        view.view_rotation.__matmul__.return_value = Vector(
            float(axis == 0) * sign, float(axis == 1) * sign, float(axis == 2) * sign
        )
        view.view_location = Vector(0, 0, 0)

    def test_axis_shortcuts_toggle_sides_and_keep_target(self) -> None:
        self.view.view_location = Vector(2, 3, 4)
        axis: str
        with patch.object(self.navigation, "align_axis", side_effect=self.align):
            for axis in ("X", "Y", "Z"):
                sign: int
                for sign in (1, -1, 1):
                    self.navigation.set_view(self.view, axis)
                    self.assertEqual(self.view.view_perspective, "ORTHO")
                    self.assertEqual(self.view.view_location, Vector(2, 3, 4))
                    self.assertEqual(self.view.view_rotation.__matmul__.return_value["XYZ".index(axis)], sign)
                    self.assertEqual(self.view.view_distance, 12.0)
        with patch.object(self.navigation, "Vector", MagicMock()):
            self.navigation.set_view(self.view, "PERSPECTIVE")
        self.assertEqual(self.view.view_perspective, "PERSP")

    def test_an_axis_view_turns_the_3d_cursors_plane_to_face_it(self) -> None:
        # Loaded with stand-in vectors, as only the cursor's plane is checked here.
        blender: ModuleType = ModuleType("_axis_test.core.blender")
        name: str
        for name in ("Matrix", "Quaternion", "Vector", "bpy", "view3d_utils"):
            setattr(blender, name, MagicMock())
        cursor: SimpleNamespace = SimpleNamespace(cursor_axis=1)
        state: ModuleType = ModuleType("_axis_test.core.state")
        setattr(state, "get_state", MagicMock(return_value=cursor))
        setattr(state, "save_preferences", MagicMock())
        spec: ModuleSpec | None = importlib.util.spec_from_file_location(
            "_axis_test.viewport.navigation", ROOT / "app/drawing_app/viewport/navigation.py"
        )
        assert spec is not None and spec.loader is not None
        navigation: ModuleType = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {blender.__name__: blender, state.__name__: state, spec.name: navigation}):
            spec.loader.exec_module(navigation)
        view: MagicMock = MagicMock()
        view.view_perspective = "PERSP"
        # Every control that chooses an axis view goes through align_axis: the 1 2 3 keys, the menus, the
        # Tool Box's X Y Z buttons, the orientation gizmo, and scripts.
        axis: int
        sign: int
        for axis, sign in ((0, 1), (2, -1), (1, 1), (0, -1)):
            navigation.align_axis(view, axis, sign)
            self.assertEqual(cursor.cursor_axis, axis)
        # The named views do too, and Perspective keeps the last plane.
        view.view_rotation.__matmul__.return_value = [0.0, 0.0, 0.0]
        navigation.set_view(view, "Z")
        self.assertEqual((cursor.cursor_axis, view.view_perspective), (2, "ORTHO"))
        navigation.set_view(view, "PERSPECTIVE")
        self.assertEqual((cursor.cursor_axis, view.view_perspective), (2, "PERSP"))


if __name__ == "__main__":
    unittest.main()
