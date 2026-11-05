"""Transient brush footprints, axis spokes, and completed-pipe recognition."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from ..core.blender import batch_for_shader, bpy, gpu
from ..core.state import Vec3, get_state
from ..viewport.navigation import AXIS_COLORS
from .canvas import Hit
from .geometry import add, basis, mul, profile


class BrushPreview:
    def __init__(self) -> None:
        self._handler: Any = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), "WINDOW", "POST_VIEW")
        self._area: Any = None
        self._region: Any = None
        self._hit: Hit | None = None
        self._radius: float = 0.02
        self._line_shader: Any = None
        self._fill_shader: Any = None
        self._entered: str = ""
        self._recognized: str = ""
        self._expires: float = 0.0
        self._recognition_batch: Any = None
        self._timer: Callable[[], float | None] = self._tick
        self._last_error: str = ""

    def update(self, area: Any, region: Any, hit: Hit | None, radius: float, recognition: str = "") -> None:
        self._area, self._region, self._hit, self._radius = area, region, hit, radius
        if recognition != self._entered:
            self._entered = recognition
            if recognition:
                self._recognized = recognition
                self._expires = time.monotonic() + 1.0
                self._recognition_batch = None
                if not bpy.app.timers.is_registered(self._timer):
                    bpy.app.timers.register(self._timer, first_interval=0.1)
        area.tag_redraw()

    def clear(self) -> None:
        self._hit = None
        self._entered = ""
        if self._area is not None:
            self._area.tag_redraw()

    def _tick(self) -> float | None:
        if self._area is not None:
            self._area.tag_redraw()
        if time.monotonic() >= self._expires:
            self._recognized = ""
            self._recognition_batch = None
            return None
        return 0.1

    def _line(self, points: list[Vec3], color: tuple[float, float, float, float], width: float) -> None:
        if self._line_shader is None:
            self._line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        batch: Any = batch_for_shader(self._line_shader, "LINES", {"pos": points})
        self._line_shader.bind()
        self._line_shader.uniform_float("viewportSize", (float(self._region.width), float(self._region.height)))
        self._line_shader.uniform_float("lineWidth", width)
        self._line_shader.uniform_float("color", color)
        batch.draw(self._line_shader)

    def _draw(self) -> None:
        if bpy.context.area != self._area or bpy.context.region != self._region:
            return
        if get_state().tool not in ("paint", "pipe", "grease") and not get_state().cursor_placing:
            return
        depth: str = str(gpu.state.depth_test_get())
        mask: bool = bool(gpu.state.depth_mask_get())
        blend: str = str(gpu.state.blend_get())
        error: RuntimeError | TypeError | ValueError | AttributeError
        try:
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("ALPHA")
            self._draw_recognition()
            if self._hit is None:
                return
            right: Vec3
            up: Vec3
            right, up = basis(self._hit.normal)
            center: Vec3 = add(self._hit.position, mul(self._hit.normal, self._radius * 0.005))
            outline: list[tuple[float, float]] = profile(get_state().brush.shape)
            if get_state().brush.shape == "circle":
                outline = []
                index: int
                for index in range(64):
                    angle: float = index * math.tau / 64.0
                    outline.append((math.cos(angle), math.sin(angle)))
            points: list[Vec3] = []
            index: int
            first: tuple[float, float]
            for index, first in enumerate(outline):
                second: tuple[float, float] = outline[(index + 1) % len(outline)]
                points.extend(
                    (
                        add(center, mul(add(mul(right, first[0]), mul(up, first[1])), self._radius)),
                        add(center, mul(add(mul(right, second[0]), mul(up, second[1])), self._radius)),
                    )
                )
            self._line(points, (1.0, 0.85, 0.2, 1.0), 1.5)
            self._line(
                [
                    add(center, mul(right, -self._radius * 0.18)),
                    add(center, mul(right, self._radius * 0.18)),
                    add(center, mul(up, -self._radius * 0.18)),
                    add(center, mul(up, self._radius * 0.18)),
                ],
                (1.0, 0.85, 0.2, 0.9),
                1.0,
            )
            if get_state().axes_spokes:
                axis: int
                for axis in range(3):
                    vector: Vec3 = (float(axis == 0), float(axis == 1), float(axis == 2))
                    self._line(
                        [add(center, mul(vector, -self._radius * 4.0)), add(center, mul(vector, self._radius * 4.0))],
                        (*AXIS_COLORS[axis], 0.9),
                        1.2,
                    )
        except (RuntimeError, TypeError, ValueError, AttributeError) as error:
            if str(error) != self._last_error:
                self._last_error = str(error)
                print(f"Drawing brush preview: {error}")
        finally:
            gpu.state.depth_test_set(depth)
            gpu.state.depth_mask_set(mask)
            gpu.state.blend_set(blend)

    def _draw_recognition(self) -> None:
        if not self._recognized or time.monotonic() >= self._expires:
            return
        obj: Any = bpy.data.objects.get(self._recognized)
        if obj is None or obj.type != "MESH" or not obj.visible_get():
            return
        if self._fill_shader is None:
            self._fill_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        if self._recognition_batch is None:
            obj.data.calc_loop_triangles()
            points: list[Vec3] = []
            vertex: Any
            for vertex in obj.data.vertices:
                points.append((float(vertex.co.x), float(vertex.co.y), float(vertex.co.z)))
            indices: list[tuple[int, int, int]] = []
            triangle: Any
            for triangle in obj.data.loop_triangles:
                indices.append((int(triangle.vertices[0]), int(triangle.vertices[1]), int(triangle.vertices[2])))
            self._recognition_batch = batch_for_shader(self._fill_shader, "TRIS", {"pos": points}, indices=indices)
        self._fill_shader.bind()
        self._fill_shader.uniform_float("color", (1.0, 0.38, 0.03, 0.8))
        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(obj.matrix_world)
            self._recognition_batch.draw(self._fill_shader)

    def destroy(self) -> None:
        if bpy.app.timers.is_registered(self._timer):
            bpy.app.timers.unregister(self._timer)
        if self._handler is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handler, "WINDOW")
            self._handler = None
        self._hit = None
        self._area = self._region = None
        self._recognition_batch = None
