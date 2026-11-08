"""Fly-mode scale regressions with inert Blender/GPU boundaries."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT: Path = Path(__file__).resolve().parents[1]


@dataclass
class Point:
    x: float
    y: float
    z: float

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y, self.z + other.z)


def vector(values: tuple[float, float, float]) -> Point:
    return Point(*values)


def identity(point: Point) -> Point:
    return point


def persistent(callback: Callable[..., object]) -> Callable[..., object]:
    return callback


def load_guides() -> ModuleType:
    blender: ModuleType = ModuleType("_fly_test.core.blender")
    name: str
    for name in ("batch_for_shader", "blf", "bpy", "gpu", "view3d_utils"):
        setattr(blender, name, MagicMock())
    setattr(blender, "Vector", vector)
    blender.bpy.app.handlers.persistent.side_effect = persistent
    state: ModuleType = ModuleType("_fly_test.core.state")
    setattr(state, "Vec3", tuple[float, float, float])
    setattr(state, "get_state", MagicMock(return_value=SimpleNamespace(units="m")))
    navigation: ModuleType = ModuleType("_fly_test.viewport.navigation")
    for name in ("AXIS_COLORS", "GIZMO_CENTER", "AxisHandle", "axis_handles"):
        setattr(navigation, name, MagicMock())
    viewport: ModuleType = ModuleType("_fly_test.viewport")
    setattr(viewport, "fills", MagicMock())
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_fly_test.viewport.guides", ROOT / "app/drawing_app/viewport/guides.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    modules: dict[str, ModuleType] = {
        blender.__name__: blender,
        state.__name__: state,
        navigation.__name__: navigation,
        viewport.__name__: viewport,
        spec.name: module,
    }
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class FlyGuideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guides: ModuleType = load_guides()
        self.rotation: MagicMock = MagicMock()
        self.rotation.__matmul__.side_effect = identity
        self.view: SimpleNamespace = SimpleNamespace(
            view_distance=12.0,
            view_location=Point(0.0, 0.0, 0.0),
            view_rotation=self.rotation,
            is_perspective=True,
            window_matrix=((1.5, 0.0), (0.0, 2.0)),
        )
        self.space: SimpleNamespace = SimpleNamespace(region_3d=self.view, clip_start=0.001, clip_end=100000.0)
        self.area: MagicMock = MagicMock()
        self.area.type = "VIEW_3D"
        self.area.as_pointer.return_value = 1
        self.area.spaces.active = self.space
        self.context: SimpleNamespace = SimpleNamespace(
            scene=SimpleNamespace(unit_settings=SimpleNamespace(scale_length=1.0))
        )

    def begin_native_walk(self) -> None:
        # Native camera control moves the pivot to the eye, then sets distance to zero.
        self.view.view_location = self.guides.eye_position(self.view)
        self.view.view_distance = 0.0

    def test_clipping_does_not_collapse_during_walk(self) -> None:
        self.guides.update_clipping(self.area)
        before: tuple[float, float] = (self.space.clip_start, self.space.clip_end)
        eye: Point = self.guides.eye_position(self.view)
        self.begin_native_walk()
        self.guides.update_clipping(self.area)
        self.assertEqual((self.space.clip_start, self.space.clip_end), before)
        self.assertEqual(self.guides.eye_position(self.view), eye)

    def test_look_rotation_cannot_move_eye_or_shrink_ruler(self) -> None:
        self.guides.update_clipping(self.area)
        step: float = self.guides.grid_spacing(self.context, self.area, None)
        extent: float = self.guides.ruler_extent(self.area, step)
        self.begin_native_walk()
        # A zero offset remains zero under any native look rotation.
        self.rotation.__matmul__.return_value = Point(0.0, 0.0, 0.0)
        self.rotation.__matmul__.side_effect = None
        self.assertEqual(self.guides.eye_position(self.view), Point(0.0, 0.0, 12.0))
        self.assertEqual(self.guides.grid_spacing(self.context, self.area, None), step)
        self.assertEqual(self.guides.ruler_extent(self.area, step), extent)

    def test_normal_zoom_updates_scale_after_fly(self) -> None:
        self.assertEqual(self.guides.navigation_distance(self.area), 12.0)
        self.begin_native_walk()
        self.assertEqual(self.guides.navigation_distance(self.area), 12.0)
        self.view.view_distance = 3.0
        self.assertEqual(self.guides.navigation_distance(self.area), 3.0)
        self.view.view_distance = 0.0
        self.assertEqual(self.guides.navigation_distance(self.area), 3.0)

    def test_close_zoom_keeps_scene_and_depth_precision(self) -> None:
        distance: float
        for distance in (12.0, 1.0, 0.1, 0.001, 0.00001):
            self.view.view_distance = distance
            self.guides.update_clipping(self.area)
            self.assertEqual(self.space.clip_start, 0.01)
            self.assertGreaterEqual(self.space.clip_end, 1000.0)
            # Nearby paint remains distinguishable from its surface at ten units.
            depth_step: float = 10.0**2 / (self.space.clip_start * (2**24 - 1))
            self.assertLess(depth_step, 0.001)

    def test_zooming_back_in_preserves_extended_far_range(self) -> None:
        self.view.view_distance = 500.0
        self.guides.update_clipping(self.area)
        far: float = self.space.clip_end
        self.assertGreaterEqual(far, 6000.0)
        self.view.view_distance = 0.1
        self.guides.update_clipping(self.area)
        self.assertEqual(self.space.clip_end, far)

    def test_transient_singular_view_matrix_is_not_inverted(self) -> None:
        matrix: MagicMock = MagicMock()
        matrix.inverted.side_effect = ValueError("Matrix has no inverse")
        self.view.view_matrix = matrix
        self.guides.update_clipping(self.area)
        self.assertEqual(self.guides.eye_position(self.view), Point(0.0, 0.0, 12.0))
        matrix.inverted.assert_not_called()

    def test_viewports_keep_independent_scales_and_cleanup(self) -> None:
        self.view.view_distance = 50.0
        self.assertEqual(self.guides.navigation_distance(self.area), 50.0)
        self.area.as_pointer.return_value = 2
        self.view.view_distance = 0.0
        self.assertEqual(self.guides.navigation_distance(self.area), 12.0)
        self.area.as_pointer.return_value = 1
        self.assertEqual(self.guides.navigation_distance(self.area), 50.0)
        self.guides.remove_guides()
        self.assertEqual(self.guides.navigation_distance(self.area), 12.0)


if __name__ == "__main__":
    unittest.main()
