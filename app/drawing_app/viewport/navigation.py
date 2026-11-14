"""Viewport navigation: Blender's native operators plus the application's gizmo, wheel speed, and touch.

Right and middle drags and Fly are Blender's own view3d.rotate, view3d.move and view3d.walk,
bound in keymap.py. This module adds the axis gizmo, adjustable wheel zoom, and finger gestures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from ..core.blender import Matrix, Quaternion, Vector, bpy, view3d_utils
from ..core.state import get_state, save_preferences

Point: TypeAlias = tuple[float, float]
Drag: TypeAlias = Literal["", "gizmo"]
# Blender's wheel step for view3d.zoom; other zoom speeds scale this step.
WHEEL_STEP: float = 1.2
GIZMO_CENTER: Point = (76.0, 76.0)
GIZMO_RADIUS: float = 43.0
AXIS_COLORS: tuple[tuple[float, float, float], ...] = (
    (170.0 / 255.0, 96.0 / 255.0, 96.0 / 255.0),
    (113.0 / 255.0, 163.0 / 255.0, 118.0 / 255.0),
    (79.0 / 255.0, 125.0 / 255.0, 160.0 / 255.0),
)


@dataclass(frozen=True)
class AxisHandle:
    axis: int
    sign: int
    x: float
    y: float
    depth: float


def axis_handles(rv3d: Any) -> list[AxisHandle]:
    rotation: Any = rv3d.view_rotation.inverted()
    handles: list[AxisHandle] = []
    axis: int
    sign: int
    for axis in range(3):
        for sign in (-1, 1):
            vector: Any = Vector((0.0, 0.0, 0.0))
            vector[axis] = float(sign)
            local: Any = rotation @ vector
            handles.append(
                AxisHandle(
                    axis,
                    sign,
                    GIZMO_CENTER[0] + local.x * GIZMO_RADIUS,
                    GIZMO_CENTER[1] + local.y * GIZMO_RADIUS,
                    float(local.z),
                )
            )
    return sorted(handles, key=_handle_depth)


def _handle_depth(handle: AxisHandle) -> float:
    return handle.depth


def gizmo_hit(rv3d: Any, position: Point) -> AxisHandle | None:
    handle: AxisHandle
    for handle in reversed(axis_handles(rv3d)):
        if math.hypot(position[0] - handle.x, position[1] - handle.y) <= 12.0:
            return handle
    return None


def align_axis(rv3d: Any, axis: int, sign: int) -> None:
    """Look along a world axis.

    The 3D cursor's drawing plane turns to face the view, so strokes drawn in an X, Y, or Z view land on
    the plane through the cursor that the view shows flat, whichever control chose the view.
    """
    get_state().cursor_axis = axis
    backward: Any = Vector((0.0, 0.0, 0.0))
    backward[axis] = float(sign)
    up: Any = Vector((0.0, 1.0, 0.0) if axis == 2 else (0.0, 0.0, 1.0))
    right: Any = up.cross(backward).normalized()
    up = backward.cross(right).normalized()
    rv3d.view_rotation = Matrix((right, up, backward)).transposed().to_quaternion()
    rv3d.view_location = Vector((0.0, 0.0, 0.0))
    if rv3d.view_perspective == "CAMERA":
        rv3d.view_perspective = "PERSP"
    rv3d.update()


def set_view(rv3d: Any, name: str) -> None:
    """Toggle an aligned axis side while preserving the current view target."""
    if name == "PERSPECTIVE":
        rv3d.view_perspective = "PERSP"
        rv3d.view_rotation = Vector((8.0, -10.0, 7.0)).to_track_quat("Z", "Y")
    elif name in ("X", "Y", "Z"):
        axis: int = "XYZ".index(name)
        backward: Any = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
        sign: int = -1 if rv3d.view_perspective == "ORTHO" and float(backward[axis]) > 0.9999 else 1
        pivot: Any = rv3d.view_location.copy()
        align_axis(rv3d, axis, sign)
        rv3d.view_location = pivot
        rv3d.view_perspective = "ORTHO"
    rv3d.update()


def orbit_view(rv3d: Any, dx: float, dy: float, width: float, height: float) -> None:
    if rv3d.view_perspective != "PERSP":
        rv3d.view_perspective = "PERSP"
    rotation: Any = rv3d.view_rotation.copy()
    forward: Any = rotation @ Vector((0.0, 0.0, -1.0))
    elevation: float = math.acos(max(-1.0, min(1.0, float(forward.z))))
    pitch: float = max(
        elevation - math.radians(179.0), min(elevation - math.radians(1.0), math.pi * dy / max(height, 1.0))
    )
    rotation = Quaternion((0.0, 0.0, 1.0), -math.pi * dx / max(width, 1.0)) @ rotation
    right: Any = rotation @ Vector((1.0, 0.0, 0.0))
    rv3d.view_rotation = Quaternion(right, pitch) @ rotation
    rv3d.update()


def zoom_view(region: Any, rv3d: Any, position: Point, factor: float, context: Any | None = None) -> None:
    if not math.isfinite(factor) or factor <= 0.0:
        return
    if rv3d.view_perspective == "CAMERA":
        rv3d.view_perspective = "PERSP"
    pivot: Any = rv3d.view_location.copy()
    old_distance: float = float(rv3d.view_distance)
    if context is not None and rv3d.view_perspective == "PERSP":
        origin: Any = view3d_utils.region_2d_to_origin_3d(region, rv3d, position)
        direction: Any = view3d_utils.region_2d_to_vector_3d(region, rv3d, position)
        hit: Any = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
        if hit[0]:
            backward: Any = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))
            eye: Any = pivot + backward * old_distance
            depth: float = float((eye - hit[1]).dot(backward))
            if math.isfinite(depth) and depth > 0.0:
                # Rebase on the pointed surface without moving the eye.
                old_distance = depth
                pivot = eye - backward * depth
    anchor: Any = view3d_utils.region_2d_to_location_3d(region, rv3d, position, pivot)
    if anchor is None:
        return
    distance: float = max(0.02, min(1e9, old_distance * factor))
    actual: float = distance / max(old_distance, 1e-12)
    rv3d.view_location = anchor + (pivot - anchor) * actual
    rv3d.view_distance = distance
    rv3d.update()


def pan_view(region: Any, rv3d: Any, previous: Point, position: Point) -> None:
    depth: Any = rv3d.view_location.copy()
    before: Any = view3d_utils.region_2d_to_location_3d(region, rv3d, previous, depth)
    after: Any = view3d_utils.region_2d_to_location_3d(region, rv3d, position, depth)
    if before is not None and after is not None:
        rv3d.view_location += before - after
        rv3d.update()


def frame_selected(context: Any, area: Any, region: Any) -> None:
    with context.temp_override(area=area, region=region):
        if context.selected_objects:
            bpy.ops.view3d.view_selected(use_all_regions=False)
        else:
            bpy.ops.view3d.view_all(center=False)
    area.tag_redraw()


class NavigationController:
    """Application navigation that Blender's keymap does not provide."""

    def __init__(self) -> None:
        from .touch import TouchNavigation

        self._drag: Drag = ""
        self._area: Any = None
        self._region: Any = None
        self._previous: Point = (0.0, 0.0)
        self._press: Point = (0.0, 0.0)
        self._handle: AxisHandle | None = None
        self._moved: bool = False
        self._touch: TouchNavigation = TouchNavigation()

    @property
    def active(self) -> bool:
        return bool(self._drag)

    @property
    def viewport_context(self) -> tuple[Any, Any] | None:
        return (
            (self._area, self._region) if self._drag and self._area is not None and self._region is not None else None
        )

    def cancel(self) -> None:
        self._drag = ""
        self._handle = None
        self._area = None
        self._region = None

    def handle(self, context: Any, event: Any, area: Any, region: Any) -> bool:
        """Return True when the event was used; native navigation events pass through."""
        kind: str = str(event.type)
        value: str = str(event.value)
        if kind == "WINDOW_DEACTIVATE" or (kind == "ESC" and self._drag):
            self.cancel()
            return kind == "ESC"
        if area is None or region is None or area.type != "VIEW_3D":
            return False
        target_area: Any = self._area if self._drag and self._area is not None else area
        target_region: Any = self._region if self._drag and self._region is not None else region
        rv3d: Any = target_area.spaces.active.region_3d
        position: Point = (float(event.mouse_x - target_region.x), float(event.mouse_y - target_region.y))
        if kind == "RIGHTMOUSE" and value == "PRESS" and get_state().navigation == "fly":
            from .guides import navigation_distance, update_clipping
            from .keymap import set_walk_speed

            # Cache scale before native Walk replaces orbit distance with zero.
            set_walk_speed(navigation_distance(target_area))
            update_clipping(target_area)
            return False
        if kind == "LEFTMOUSE":
            if value == "PRESS" and math.dist(position, GIZMO_CENTER) < 65.0:
                self._drag = "gizmo"
                self._area, self._region = area, region
                self._previous = self._press = position
                self._handle = gizmo_hit(rv3d, position)
                self._moved = False
                return True
            if value == "RELEASE" and self._drag == "gizmo":
                if not self._moved and self._handle is not None:
                    handle: AxisHandle = self._handle
                    if gizmo_hit(rv3d, position) == handle:
                        align_axis(rv3d, handle.axis, -handle.sign if handle.depth > 0.9999 else handle.sign)
                target_area.tag_redraw()
                self.cancel()
                return True
        if kind in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"} and self._drag == "gizmo":
            self._moved = self._moved or math.dist(position, self._press) >= 4.0
            if self._moved:
                orbit_view(rv3d, position[0] - self._previous[0], position[1] - self._previous[1], 136.0, 136.0)
            self._previous = position
            target_area.tag_redraw()
            return True
        if kind in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and value == "PRESS" and not self._drag:
            amount: float = 1.0 if kind == "WHEELUPMOUSE" else -1.0
            if event.shift:
                get_state().zoom_speed = max(0.1, min(10.0, get_state().zoom_speed * math.pow(1.25, amount)))
                save_preferences()
                get_state().status = f"Zoom speed: {get_state().zoom_speed:.3g}x"
                target_area.tag_redraw()
                return True
            if abs(get_state().zoom_speed - 1.0) < 1e-6:
                # Blender's view3d.zoom, toward the pointer (Zoom to Mouse Position).
                return False
            zoom_view(target_region, rv3d, position, math.pow(WHEEL_STEP, -amount * get_state().zoom_speed), context)
            target_area.tag_redraw()
            return True
        # Keyboard view keys (1/2/3/0, F, numpad) are in core/shortcuts.py.
        return False

    def tick(self, context: Any, dt: float) -> None:
        self._touch.tick(context)

    @staticmethod
    def frame_selected(context: Any, area: Any, region: Any) -> None:
        frame_selected(context, area, region)

    def destroy(self) -> None:
        self.cancel()
        self._touch.destroy()
