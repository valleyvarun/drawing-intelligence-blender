"""Viewport rays and the shared ground, plane, object, and cursor canvases."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..core.blender import Vector, bpy, view3d_utils
from ..core.state import AppState, Vec3, get_state
from .layers import plane_equation, selected_plane


@dataclass(frozen=True)
class Hit:
    position: Vec3
    normal: Vec3
    source: str


def vector_tuple(value: Any) -> Vec3:
    return float(value[0]), float(value[1]), float(value[2])


def ray(event: Any, area: Any, region: Any, *, pointer: tuple[float, float] | None = None) -> tuple[Any, Any]:
    pixel: tuple[float, float] = pointer if pointer is not None else (float(event.mouse_x), float(event.mouse_y))
    coordinate: tuple[float, float] = (pixel[0] - region.x, pixel[1] - region.y)
    view: Any = area.spaces.active.region_3d
    return (
        view3d_utils.region_2d_to_origin_3d(region, view, coordinate),
        view3d_utils.region_2d_to_vector_3d(region, view, coordinate).normalized(),
    )


def intersect(origin: Any, direction: Any, point: Any, normal: Any, source: str) -> Hit | None:
    denominator: float = float(direction.dot(normal))
    if abs(denominator) < 1e-9:
        return None
    distance: float = float((point - origin).dot(normal)) / denominator
    if distance < 0.0:
        return None
    return Hit(vector_tuple(origin + direction * distance), vector_tuple(normal.normalized()), source)


def object_hit(context: Any, origin: Any, direction: Any) -> Hit | None:
    depsgraph: Any = context.evaluated_depsgraph_get()
    start: Any = origin.copy()
    _attempt: int
    for _attempt in range(32):
        result: Any = context.scene.ray_cast(depsgraph, start, direction)
        if not result[0]:
            return None
        obj: Any = result[4]
        if not obj.get("drawing_plane") and not obj.get("drawing_preview") and not obj.get("drawing_helper"):
            return Hit(vector_tuple(result[1]), vector_tuple(result[2]), obj.name)
        start = result[1] + direction * 1e-5
    return None


def hit(context: Any, event: Any, area: Any, region: Any, placing: bool = False) -> Hit | None:
    state: AppState = get_state()
    origin: Any
    direction: Any
    origin, direction = ray(event, area, region)
    if state.cursor_active and state.cursor_placed and not placing:
        normal: Any = Vector(
            (float(state.cursor_axis == 0), float(state.cursor_axis == 1), float(state.cursor_axis == 2))
        )
        return intersect(origin, direction, context.scene.cursor.location, normal, "cursor")
    plane_hit: Hit | None = None
    if state.drawing_plane:
        point: Any
        normal: Any
        point, normal = plane_equation(context)
        plane_hit = intersect(
            origin, direction, point, normal, "plane" if selected_plane(context) is not None else "ground"
        )
    surface: Hit | None = object_hit(context, origin, direction) if state.on_object else None
    if plane_hit is not None and surface is not None:

        def distance(value: Hit) -> float:
            return float((Vector(value.position) - origin).length_squared)

        return min((plane_hit, surface), key=distance)
    if surface is not None:
        return surface
    if state.on_object and plane_hit is None and not placing:
        return None
    if plane_hit is not None:
        return plane_hit
    return intersect(origin, direction, Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 1.0)), "ground")


def place_cursor(context: Any, event: Any, area: Any, region: Any) -> bool:
    location: Hit | None = hit(context, event, area, region, placing=True)
    if location is None:
        get_state().status = "No drawing surface under the pointer"
        return False
    position: list[float] = list(location.position)
    if location.source == "ground" and get_state().ruler:
        from ..viewport import grid_spacing

        spacing: float = grid_spacing(context, area, region)
        if spacing > 0.0:
            position[0] = round(position[0] / spacing) * spacing
            position[1] = round(position[1] / spacing) * spacing
    context.scene.cursor.location = position
    get_state().cursor_active = True
    get_state().cursor_placed = True
    get_state().cursor_placing = False
    get_state().status = "3D cursor placed"
    return True


def axis_directions(start: Vec3, view: Any, region: Any) -> list[tuple[int, tuple[float, float]]]:
    """Screen directions of the world axes at a visible point, independent of depth."""
    matrix: Any = view.perspective_matrix
    clip: Any = matrix @ Vector((*start, 1.0))
    w: float = float(clip.w)
    if w <= 1e-8 or not math.isfinite(w):
        return []
    x: float = float(clip.x) / w
    y: float = float(clip.y) / w
    directions: list[tuple[int, tuple[float, float]]] = []
    axis: int
    for axis in range(3):
        dx: float = (float(matrix[0][axis]) - x * float(matrix[3][axis])) * float(region.width)
        dy: float = (float(matrix[1][axis]) - y * float(matrix[3][axis])) * float(region.height)
        length: float = math.hypot(dx, dy)
        if length > 1e-8 and math.isfinite(length):
            directions.append((axis, (dx / length, dy / length)))
    return directions


def choose_axis(
    start: Vec3,
    event: Any,
    area: Any,
    region: Any,
    *,
    current: int | None = None,
    motion: tuple[float, float] | None = None,
    minimum: float = 4.0,
) -> int | None:
    """Choose from recent pen motion, retaining the current axis through small or ambiguous turns."""
    view: Any = area.spaces.active.region_3d
    center: Any = view3d_utils.location_3d_to_region_2d(region, view, Vector(start))
    if center is None:
        return current
    delta: Any = Vector(
        motion if motion is not None else (event.mouse_x - region.x - center.x, event.mouse_y - region.y - center.y)
    )
    if delta.length < minimum:
        return current
    best: float = -1.0
    previous: float = -1.0
    chosen: int | None = None
    axis: int
    direction: tuple[float, float]
    motion_direction: Any = delta.normalized()
    for axis, direction in axis_directions(start, view, region):
        score: float = abs(float(motion_direction.x) * direction[0] + float(motion_direction.y) * direction[1])
        if axis == current:
            previous = score
        if score > best:
            best, chosen = score, axis
    if current is not None and (best < 0.85 or best < previous + 0.15):
        return current
    return chosen


def axis_position(
    start: Vec3, axis: int, event: Any, area: Any, region: Any, *, pointer: tuple[float, float] | None = None
) -> Vec3 | None:
    origin: Any
    direction: Any
    origin, direction = ray(event, area, region, pointer=pointer)
    unit: Any = Vector((float(axis == 0), float(axis == 1), float(axis == 2)))
    delta: Any = Vector(start) - origin
    dot: float = float(direction.dot(unit))
    denominator: float = 1.0 - dot * dot
    if denominator < 1e-8:
        return None
    distance: float = (dot * float(direction.dot(delta)) - float(unit.dot(delta))) / denominator
    return vector_tuple(Vector(start) + unit * distance)


def pipe_hit(context: Any, event: Any, area: Any, region: Any, radius: float) -> Hit | None:
    """Recognize a visible completed pipe inside the projected brush footprint."""
    origin: Any
    direction: Any
    origin, direction = ray(event, area, region)
    direct: Hit | None = object_hit(context, origin, direction)
    if direct is not None:
        obj: Any = bpy.data.objects.get(direct.source)
        if obj is not None and obj.get("drawing_kind") == "pipe":
            return direct
    view: Any = area.spaces.active.region_3d
    pointer: Any = Vector((event.mouse_x - region.x, event.mouse_y - region.y))
    right: Any = view.view_rotation @ Vector((1.0, 0.0, 0.0))
    closest: float = float("inf")
    target: Any = None
    obj: Any
    for obj in context.visible_objects:
        if obj.get("drawing_kind") != "pipe" or obj.get("drawing_preview"):
            continue
        bounds: list[Any] = []
        corner: Any
        for corner in obj.bound_box:
            projected_corner: Any = view3d_utils.location_3d_to_region_2d(
                region, view, obj.matrix_world @ Vector(corner)
            )
            if projected_corner is not None:
                bounds.append(projected_corner)
        center_world: Any = obj.matrix_world @ Vector(obj.bound_box[0])
        center_pixel: Any = view3d_utils.location_3d_to_region_2d(region, view, center_world)
        radius_pixel: Any = view3d_utils.location_3d_to_region_2d(region, view, center_world + right * radius)
        margin: float = (
            max(4.0, float((radius_pixel - center_pixel).length))
            if radius_pixel is not None and center_pixel is not None
            else 8.0
        )
        if len(bounds) == 8:
            xs: list[float] = []
            ys: list[float] = []
            bound: Any
            for bound in bounds:
                xs.append(float(bound.x))
                ys.append(float(bound.y))
            if (
                pointer.x < min(xs) - margin
                or pointer.x > max(xs) + margin
                or pointer.y < min(ys) - margin
                or pointer.y > max(ys) + margin
            ):
                continue
        values: Any = obj.get("drawing_samples", [])
        count: int = len(values) // 9
        stride: int = max(1, count // 192)
        index: int
        for index in range(0, count, stride):
            position: Any = obj.matrix_world @ Vector(values[index * 9 : index * 9 + 3])
            projected: Any = view3d_utils.location_3d_to_region_2d(region, view, position)
            edge: Any = view3d_utils.location_3d_to_region_2d(
                region, view, position + right * (radius + float(values[index * 9 + 3]))
            )
            if projected is None or edge is None:
                continue
            distance: float = float((projected - pointer).length)
            if distance <= max(4.0, float((edge - projected).length)) and distance < closest:
                closest, target = distance, projected
    if target is None:
        return None
    origin = view3d_utils.region_2d_to_origin_3d(region, view, target)
    direction = view3d_utils.region_2d_to_vector_3d(region, view, target).normalized()
    result: Hit | None = object_hit(context, origin, direction)
    obj = bpy.data.objects.get(result.source) if result is not None else None
    return result if obj is not None and obj.get("drawing_kind") == "pipe" else None
