"""Continuous axis turns using projected camera rays, without Blender or a GPU."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from dataclasses import dataclass, replace
from importlib.machinery import ModuleSpec
from itertools import product
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from drawing_app.core.state import AppState, Vec3
    from drawing_app.tools.drawing import DrawingController
    from drawing_app.tools.geometry import Sample
    from drawing_app.tools.preview import BrushPreview

ROOT: Path = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Vector:
    values: tuple[float, ...]

    def __getitem__(self, index: int) -> float:
        return self.values[index]

    @property
    def x(self) -> float:
        return self.values[0]

    @property
    def y(self) -> float:
        return self.values[1]

    @property
    def w(self) -> float:
        return self.values[3]

    @property
    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def __add__(self, other: Vector) -> Vector:
        values: list[float] = []
        index: int
        for index in range(len(self.values)):
            values.append(self[index] + other[index])
        return Vector(tuple(values))

    def __sub__(self, other: Vector) -> Vector:
        return self + other * -1.0

    def __mul__(self, amount: float) -> Vector:
        values: list[float] = []
        value: float
        for value in self.values:
            values.append(value * amount)
        return Vector(tuple(values))

    def dot(self, other: Vector) -> float:
        total: float = 0.0
        index: int
        for index in range(len(self.values)):
            total += self[index] * other[index]
        return total

    def normalized(self) -> Vector:
        return self * (1.0 / self.length)


RIGHT: Vector = Vector((1.0, 1.0, 0.0)).normalized()
UP: Vector = Vector((-1.0, 1.0, 2.0)).normalized()
BACK: Vector = Vector((1.0, -1.0, 1.0)).normalized()
EYE: Vector = BACK * 20.0


@dataclass(frozen=True)
class Matrix:
    rows: tuple[tuple[float, float, float, float], ...]

    def __getitem__(self, index: int) -> tuple[float, float, float, float]:
        return self.rows[index]

    def __matmul__(self, point: Vector) -> Vector:
        values: list[float] = []
        row: tuple[float, float, float, float]
        for row in self.rows:
            values.append(Vector(row).dot(point))
        return Vector(tuple(values))


@dataclass
class View:
    perspective: bool = False
    zoom: float = 1.0

    @property
    def perspective_matrix(self) -> Matrix:
        scale: float = (4.0 if self.perspective else 0.2) * self.zoom
        return Matrix(
            (
                (RIGHT[0] * scale, RIGHT[1] * scale, RIGHT[2] * scale, 0.0),
                (UP[0] * scale, UP[1] * scale, UP[2] * scale, 0.0),
                (0.0, 0.0, 0.0, 0.0),
                (-BACK[0], -BACK[1], -BACK[2], 20.0) if self.perspective else (0.0, 0.0, 0.0, 1.0),
            )
        )


def project(region: SimpleNamespace, view: View, point: Vector) -> Vector | None:
    depth: float = 20.0 - point.dot(BACK)
    if view.perspective and depth <= 0.0:
        return None
    scale: float = (4.0 / depth if view.perspective else 0.2) * view.zoom
    return Vector(
        (
            float(region.width) * 0.5 * (1.0 + point.dot(RIGHT) * scale),
            float(region.height) * 0.5 * (1.0 + point.dot(UP) * scale),
        )
    )


def ray_origin(_region: object, view: View, pixel: tuple[float, float]) -> Vector:
    return EYE if view.perspective else EYE + RIGHT * ((pixel[0] - 500.0) / 100.0) + UP * ((pixel[1] - 500.0) / 100.0)


def ray_direction(_region: object, view: View, pixel: tuple[float, float]) -> Vector:
    return (
        (RIGHT * ((pixel[0] - 500.0) / 100.0) + UP * ((pixel[1] - 500.0) / 100.0) - EYE).normalized()
        if view.perspective
        else BACK * -1.0
    )


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


class AxisSpokeTests(unittest.TestCase):
    def setUp(self) -> None:
        state: ModuleType = module("_spoke.core.state", "app/drawing_app/core/state.py")
        self.state: AppState = cast("AppState", state.get_state())
        self.state.tool = "pipe"
        self.state.axes_spokes = True
        self.bpy: MagicMock = MagicMock()
        self.bpy.ops.ed.undo_push.poll.return_value = True
        blender: ModuleType = ModuleType("_spoke.core.blender")
        setattr(blender, "Vector", Vector)
        setattr(blender, "bpy", self.bpy)
        setattr(
            blender,
            "view3d_utils",
            SimpleNamespace(
                location_3d_to_region_2d=project,
                region_2d_to_origin_3d=ray_origin,
                region_2d_to_vector_3d=ray_direction,
            ),
        )
        layers: ModuleType = ModuleType("_spoke.tools.layers")
        setattr(layers, "plane_equation", MagicMock())
        setattr(layers, "selected_plane", MagicMock())
        preview: ModuleType = ModuleType("_spoke.tools.preview")
        setattr(preview, "BrushPreview", MagicMock())
        surface: ModuleType = ModuleType("_spoke.tools.surface")
        setattr(surface, "PaintSurface", MagicMock())
        project_module: ModuleType = ModuleType("_spoke.core.project")
        setattr(project_module, "persist_scene", MagicMock())
        tools: ModuleType = ModuleType("_spoke.tools")
        setattr(tools, "layers", layers)
        modules: dict[str, ModuleType] = {}
        item: ModuleType
        for item in (state, blender, layers, preview, surface, tools, project_module):
            modules[item.__name__] = item
        self.modules: dict[str, ModuleType] = modules
        with patch.dict(sys.modules, modules):
            geometry: ModuleType = module("_spoke.tools.geometry", "app/drawing_app/tools/geometry.py")
            self.canvas: ModuleType = module("_spoke.tools.canvas", "app/drawing_app/tools/canvas.py")
            setattr(tools, "canvas", self.canvas)
            modules[geometry.__name__] = geometry
            modules[self.canvas.__name__] = self.canvas
            with patch.dict(sys.modules, modules):
                self.drawing: ModuleType = module("_spoke.tools.drawing", "app/drawing_app/tools/drawing.py")
        self.context: SimpleNamespace = SimpleNamespace(
            scene=SimpleNamespace(unit_settings=SimpleNamespace(scale_length=1.0)),
            preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1.0)),
            selected_objects=[],
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=None)),
        )
        self.view: View = View()
        self.area: MagicMock = MagicMock()
        self.area.spaces.active.region_3d = self.view
        self.region: SimpleNamespace = SimpleNamespace(x=0, y=0, width=1000, height=1000)
        self.controller: DrawingController = cast("DrawingController", self.drawing.DrawingController())
        self.stroke: MagicMock = MagicMock()
        self.controller._object = self.stroke
        self.controller._kind = "pipe"
        self.controller._scene = self.context.scene
        self.controller._area = self.area
        self.controller._region = self.region
        start: Sample = geometry.Sample((0.0, 0.0, 0.0), 0.02, 1.0, 1.0)
        self.controller._samples = [start]
        self.controller._axis_tip = start
        self.controller._axis_pointer = self.controller._axis_previous = (500.0, 500.0)

    def move(self, position: Vec3) -> None:
        pixel: Vector | None = project(self.region, self.view, Vector(position))
        assert pixel is not None
        event: SimpleNamespace = SimpleNamespace(
            type="MOUSEMOVE",
            value="NOTHING",
            mouse_x=pixel.x,
            mouse_y=pixel.y,
            is_tablet=False,
        )
        with patch.object(self.drawing, "write_mesh"):
            self.assertTrue(self.controller.handle(self.context, event, self.area, self.region))

    def assert_tip(self, position: Vec3) -> None:
        tip: Sample | None = self.controller._axis_tip
        assert tip is not None
        axis: int
        for axis in range(3):
            self.assertAlmostEqual(tip.position[axis], position[axis], places=8)

    def test_z_x_y_turns_and_reversal_stay_in_one_stroke(self) -> None:
        perspective: bool
        for perspective in (False, True):
            self.setUp()
            self.view.perspective = perspective
            self.move((0.0, 0.0, 1.0))
            self.assertEqual(self.controller._axis, 2)
            self.move((2.0, 0.0, 1.0))
            self.assertEqual(self.controller._axis, 0)
            self.assert_tip((2.0, 0.0, 1.0))
            self.move((2.0, 1.0, 1.0))
            self.assertEqual(self.controller._axis, 1)
            self.assert_tip((2.0, 1.0, 1.0))
            self.move((2.0, -1.0, 1.0))
            self.assertEqual(self.controller._axis, 1)
            self.assert_tip((2.0, -1.0, 1.0))
            self.assertIs(self.controller._object, self.stroke)
            self.bpy.data.objects.new.assert_not_called()
            self.bpy.ops.ed.undo_push.assert_not_called()
            before: Sample
            after: Sample
            for before, after in zip(self.controller._samples, self.controller._samples[1:]):
                axis: int
                changed: int = 0
                for axis in range(3):
                    changed += int(abs(after.position[axis] - before.position[axis]) > 1e-8)
                self.assertLessEqual(changed, 1)
            with patch.dict(sys.modules, self.modules), patch.object(self.drawing, "write_mesh"):
                self.controller.finish(self.context)
            self.bpy.ops.ed.undo_push.assert_called_once_with(message="3D pipe stroke")
            self.assertFalse(self.controller.is_drawing)

    def test_sub_spacing_corner_is_preserved(self) -> None:
        self.controller._brush.radius = 1.0
        self.controller._samples[0] = replace(self.controller._samples[0], radius=1.0)
        self.controller._axis_tip = self.controller._samples[0]
        self.move((0.0, 0.0, 0.1))
        self.assertEqual(len(self.controller._samples), 1)
        self.move((0.5, 0.0, 0.1))
        self.assertAlmostEqual(self.controller._samples[1].position[2], 0.1)
        self.assertAlmostEqual(self.controller._samples[1].position[0], 0.0)
        self.assert_tip((0.5, 0.0, 0.1))

    def test_small_pen_jitter_does_not_switch_axis(self) -> None:
        self.move((0.0, 0.0, 1.0))
        self.move((0.02, 0.0, 1.0))
        self.assertEqual(self.controller._axis, 2)
        self.move((0.0, 0.0, 1.5))
        self.assertEqual(self.controller._axis, 2)
        self.assert_tip((0.0, 0.0, 1.5))

    def test_small_motion_events_accumulate_into_a_deliberate_turn(self) -> None:
        step: int
        for step in range(1, 51):
            self.move((0.0, 0.0, step * 0.02))
        self.assertEqual(self.controller._axis, 2)
        for step in range(1, 51):
            self.move((step * 0.02, 0.0, 1.0))
        self.assertEqual(self.controller._axis, 0)
        self.assertIs(self.controller._object, self.stroke)
        tip: Sample | None = self.controller._axis_tip
        assert tip is not None
        self.assertGreater(tip.position[0], 0.9)
        self.assertLess(abs(tip.position[2] - 1.0), 0.1)
        self.assertAlmostEqual(tip.position[1], 0.0)

    def test_spoke_rendering_keeps_screen_size_across_depth_zoom_and_brush_size(self) -> None:
        blender: ModuleType = self.modules["_spoke.core.blender"]
        setattr(blender, "Matrix", MagicMock())
        setattr(blender, "gpu", MagicMock())
        setattr(blender, "batch_for_shader", MagicMock())
        viewport: ModuleType = ModuleType("_spoke.viewport")
        navigation: ModuleType = ModuleType("_spoke.viewport.navigation")
        setattr(navigation, "AXIS_COLORS", ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        self.modules[viewport.__name__] = viewport
        self.modules[navigation.__name__] = navigation
        with patch.dict(sys.modules, self.modules):
            preview_module: ModuleType = module("_spoke.tools.preview", "app/drawing_app/tools/preview.py")
        preview: BrushPreview = cast("BrushPreview", preview_module.BrushPreview())
        preview._area, preview._region = self.area, self.region
        self.region.width, self.region.height = 1600, 900
        drawn: list[list[Vec3]] = []

        def line(points: list[Vec3], _color: tuple[float, float, float, float], _width: float) -> None:
            drawn.append(points)

        perspective: bool
        depth: float
        zoom: float
        radius: float
        scale: float
        with patch.object(preview, "_line", side_effect=line):
            for perspective, depth, zoom, radius, scale in product(
                (False, True), (0.05, 20.0, 1000.0), (0.2, 5.0), (0.001, 100.0), (1.0, 2.0)
            ):
                self.view.perspective, self.view.zoom = perspective, zoom
                self.bpy.context.preferences.system.ui_scale = scale
                preview._radius = radius
                offset: float = depth * 0.1 if perspective else 1.0
                point: Vector = EYE - BACK * depth + RIGHT * offset + UP * (offset * 0.5)
                position: Vec3 = (point[0], point[1], point[2])
                center: Vector | None = project(self.region, self.view, point)
                assert center is not None
                drawn.clear()
                preview._draw_spokes(position)
                self.assertEqual(len(drawn), 3)
                segment: list[Vec3]
                for segment in drawn:
                    dx: float = (segment[1][0] - segment[0][0]) * self.region.width * 0.5
                    dy: float = (segment[1][1] - segment[0][1]) * self.region.height * 0.5
                    self.assertAlmostEqual(math.hypot(dx, dy), 64.0 * scale, places=7)
                    x: float = ((segment[0][0] + segment[1][0]) * 0.5 + 1.0) * self.region.width * 0.5
                    y: float = ((segment[0][1] + segment[1][1]) * 0.5 + 1.0) * self.region.height * 0.5
                    self.assertAlmostEqual(x, center.x, places=7)
                    self.assertAlmostEqual(y, center.y, places=7)
        preview.destroy()

    def test_near_camera_spoke_remains_selectable_when_world_endpoint_is_behind_eye(self) -> None:
        self.view.perspective = True
        point: Vector = EYE - BACK * 0.1
        position: Vec3 = (point[0], point[1], point[2])
        self.assertIsNone(project(self.region, self.view, point + Vector((1.0, 0.0, 0.0))))
        directions: dict[int, tuple[float, float]] = dict(self.canvas.axis_directions(position, self.view, self.region))
        direction: tuple[float, float] = directions[0]
        self.assertEqual(
            self.canvas.choose_axis(
                position,
                SimpleNamespace(),
                self.area,
                self.region,
                motion=(direction[0] * 20.0, direction[1] * 20.0),
            ),
            0,
        )

    def test_hidden_and_camera_aligned_axes_do_not_produce_spokes(self) -> None:
        self.view.perspective = True
        point: Vector = EYE + BACK
        self.assertEqual(self.canvas.axis_directions((point[0], point[1], point[2]), self.view, self.region), [])
        view: SimpleNamespace = SimpleNamespace(
            perspective_matrix=Matrix(
                ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
            )
        )
        self.assertEqual(
            self.canvas.axis_directions((0.0, 0.0, 0.0), view, self.region), [(0, (1.0, 0.0)), (1, (0.0, 1.0))]
        )


if __name__ == "__main__":
    unittest.main()
