"""Mouse and trackpad navigation in the native Blender 3D View."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from ..core.blender import Matrix, Quaternion, Vector, bpy, view3d_utils
from ..core.state import get_state, save_preferences

Point: TypeAlias = tuple[float, float]
Drag: TypeAlias = Literal["", "orbit", "pan", "fly", "gizmo"]
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


def zoom_view(region: Any, rv3d: Any, position: Point, factor: float) -> None:
    if not math.isfinite(factor) or factor <= 0.0:
        return
    if rv3d.view_perspective == "CAMERA":
        rv3d.view_perspective = "PERSP"
    pivot: Any = rv3d.view_location.copy()
    anchor: Any = view3d_utils.region_2d_to_location_3d(region, rv3d, position, pivot)
    if anchor is None:
        return
    old_distance: float = float(rv3d.view_distance)
    distance: float = max(1e-5, min(1e9, old_distance * factor))
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
    def __init__(self) -> None:
        from .touch import TouchNavigation

        self._drag: Drag = ""
        self._area: Any = None
        self._region: Any = None
        self._previous: Point = (0.0, 0.0)
        self._press: Point = (0.0, 0.0)
        self._handle: AxisHandle | None = None
        self._moved: bool = False
        self._keys: set[str] = set()
        self._shift: bool = False
        self._control: bool = False
        self._fly_speed: float = 1.0
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
        self._keys.clear()
        self._handle = None
        self._area = None
        self._region = None

    def handle(self, context: Any, event: Any, area: Any, region: Any) -> bool:
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
        self._shift = bool(event.shift)
        self._control = bool(event.ctrl)
        if self._drag == "fly" and kind in {"W", "A", "S", "D", "Q", "E"}:
            if value == "RELEASE":
                self._keys.discard(kind)
            elif value == "PRESS":
                self._keys.add(kind)
            return True
        if kind in {"RIGHTMOUSE", "MIDDLEMOUSE"}:
            if value == "PRESS":
                self._drag = "pan" if kind == "MIDDLEMOUSE" else ("fly" if get_state().navigation == "fly" else "orbit")
                self._area, self._region = area, region
                self._previous = self._press = position
                self._keys.clear()
                return True
            if value == "RELEASE" and self._drag:
                self.cancel()
                return True
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
        if kind in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"} and self._drag:
            dx: float = position[0] - self._previous[0]
            dy: float = position[1] - self._previous[1]
            if self._drag == "pan":
                pan_view(target_region, rv3d, self._previous, position)
            elif self._drag == "gizmo":
                self._moved = self._moved or math.dist(position, self._press) >= 4.0
                if self._moved:
                    orbit_view(rv3d, dx, dy, 136.0, 136.0)
            else:
                eye: Any = rv3d.view_location + rv3d.view_rotation @ Vector((0.0, 0.0, rv3d.view_distance))
                orbit_view(rv3d, dx, dy, float(target_region.width), float(target_region.height))
                if self._drag == "fly":
                    rv3d.view_location = eye - rv3d.view_rotation @ Vector((0.0, 0.0, rv3d.view_distance))
                    rv3d.update()
            self._previous = position
            target_area.tag_redraw()
            return True
        if kind in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and value == "PRESS":
            amount: float = 1.0 if kind == "WHEELUPMOUSE" else -1.0
            if self._drag == "fly":
                self._fly_speed = max(0.05, min(50.0, self._fly_speed * math.pow(1.25, amount)))
                get_state().status = f"Fly speed: {self._fly_speed:.3g}x"
            elif self._shift and get_state().navigation == "orbit":
                get_state().zoom_speed = max(0.1, min(10.0, get_state().zoom_speed * math.pow(1.25, amount)))
                save_preferences()
                get_state().status = f"Orbit zoom speed: {get_state().zoom_speed:.3g}x"
            else:
                zoom_view(target_region, rv3d, position, math.exp(-amount * -math.log(0.88) * get_state().zoom_speed))
            target_area.tag_redraw()
            return True
        if kind in {"TRACKPADPAN", "MOUSEROTATE", "TRACKPADZOOM"}:
            delta_x: float = float(event.mouse_x - event.mouse_prev_x)
            delta_y: float = float(event.mouse_y - event.mouse_prev_y)
            if kind == "TRACKPADZOOM":
                zoom_view(target_region, rv3d, position, math.exp(max(-2.0, min(2.0, -(delta_x + delta_y) * 0.01))))
            elif self._shift:
                pan_view(target_region, rv3d, (position[0] - delta_x, position[1] - delta_y), position)
            else:
                orbit_view(rv3d, delta_x, delta_y, float(target_region.width), float(target_region.height))
            target_area.tag_redraw()
            return True
        if kind in {"F", "NUMPAD_PERIOD"} and value == "PRESS" and not self._drag:
            frame_selected(context, area, region)
            return True
        return False

    def tick(self, context: Any, dt: float) -> None:
        self._touch.tick(context)
        if self._drag != "fly" or not self._keys or self._area is None:
            return
        rv3d: Any = self._area.spaces.active.region_3d
        local: Any = Vector(
            (
                float("D" in self._keys) - float("A" in self._keys),
                0.0,
                float("S" in self._keys) - float("W" in self._keys),
            )
        )
        direction: Any = rv3d.view_rotation @ local
        direction.z += float("E" in self._keys) - float("Q" in self._keys)
        if direction.length_squared < 1e-10:
            return
        speed: float = max(0.02, float(rv3d.view_distance) * 0.4)
        speed *= self._fly_speed
        speed *= 4.0 if self._shift else (0.25 if self._control else 1.0)
        rv3d.view_location += direction.normalized() * speed * max(0.0, min(dt, 0.1))
        rv3d.update()
        self._area.tag_redraw()

    @staticmethod
    def frame_selected(context: Any, area: Any, region: Any) -> None:
        frame_selected(context, area, region)

    def destroy(self) -> None:
        self.cancel()
        self._touch.destroy()
