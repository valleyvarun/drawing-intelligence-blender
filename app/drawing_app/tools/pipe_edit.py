"""Fill and trim adapters for actual 3D pipe geometry."""

from __future__ import annotations

import math
from importlib import import_module
from typing import Any

from ..core.blender import Vector, bpy
from ..core.state import Brush, Vec3, get_state
from . import canvas, layers, pencil
from .drawing import write_mesh
from .geometry import Sample, length, profile
from .pipe_geometry import (
    Junction,
    Network,
    Segment,
    build_network,
    closest,
    sub,
    trim_parts,
)


def _radius_scale(obj: Any) -> float:
    factor: float = math.sqrt(2.0) if obj.get("drawing_shape") == "square" else 1.0
    return factor * max(abs(float(value)) for value in obj.matrix_world.to_scale())


def samples(obj: Any) -> list[Sample]:
    values: Any = obj.get("drawing_samples", ())
    result: list[Sample] = []
    if len(values) % 9:
        return result
    scale: float = _radius_scale(obj)
    normal_matrix: Any = obj.matrix_world.to_3x3().inverted_safe().transposed()
    index: int
    for index in range(0, len(values), 9):
        position: Vec3 = canvas.vector_tuple(obj.matrix_world @ Vector(values[index : index + 3]))
        normal: Vec3 = canvas.vector_tuple((normal_matrix @ Vector(values[index + 6 : index + 9])).normalized())
        result.append(
            Sample(
                position, float(values[index + 3]) * scale, float(values[index + 4]), float(values[index + 5]), normal
            )
        )
    return result


def pipe_objects(context: Any, *, editable: bool = False) -> list[Any]:
    result: list[Any] = []
    obj: Any
    for obj in layers.active_collection(context, strokes=True).objects:
        if obj.get("drawing_kind") == "pipe" and not obj.get("drawing_preview") and obj.visible_get():
            if not editable or not layers.object_locked(context, obj):
                result.append(obj)
    return result


def network(objects: list[Any]) -> Network:
    """Confirm centerline junction candidates against the actual transformed meshes."""
    paths: list[list[Sample]] = []
    obj: Any
    for obj in objects:
        paths.append(samples(obj))
    result: Network = build_network(paths)
    if not result.junctions:
        return result
    native: Any = import_module("mathutils.bvhtree")
    trees: dict[int, Any] = {}
    index: int
    for index, obj in enumerate(objects):
        trees[index] = native.BVHTree.FromPolygons(
            [obj.matrix_world @ vertex.co for vertex in obj.data.vertices],
            [tuple(polygon.vertices) for polygon in obj.data.polygons],
            epsilon=1e-7,
        )
    overlaps: dict[tuple[int, int], list[tuple[int, int]]] = {}
    distances: list[list[float]] = []
    path: list[Sample]
    for path in paths:
        cumulative: list[float] = [0.0]
        for index in range(1, len(path)):
            cumulative.append(cumulative[-1] + length(sub(path[index].position, path[index - 1].position)))
        distances.append(cumulative)

    def near(path_index: int, polygon: int, parameter: float, radius: float) -> bool:
        sides: int = len(profile(str(objects[path_index].get("drawing_shape", "circle"))))
        count: int = len(paths[path_index]) - 1
        cap_faces: int = 96 if sides == 16 else 1
        segment: int = (
            polygon // sides if polygon < count * sides else 0 if polygon < count * sides + cap_faces else count - 1
        )
        return (
            distances[path_index][segment] - radius * 3 <= parameter <= distances[path_index][segment + 1] + radius * 3
        )

    confirmed: list[Junction] = []
    junction: Junction
    for junction in result.junctions:
        first: Segment = result.segments[junction.first]
        second: Segment = result.segments[junction.second]
        pair: tuple[int, int] = (first.path, second.path)
        if pair not in overlaps:
            overlaps[pair] = trees[first.path].overlap(trees[second.path])
        a: float = first.distance + length(sub(first.b.position, first.a.position)) * junction.t
        b: float = second.distance + length(sub(second.b.position, second.a.position)) * junction.u
        first_face: int
        second_face: int
        for first_face, second_face in overlaps[pair]:
            if near(first.path, first_face, a, junction.radius) and near(second.path, second_face, b, junction.radius):
                confirmed.append(junction)
                break
    result.junctions = confirmed
    return result


def fill(context: Any, event: Any, area: Any, region: Any) -> bool:
    from .pipe_fill import start

    return start(context, event, area, region)


def _write_samples(obj: Any, values: list[Sample]) -> None:
    inverse: Any = obj.matrix_world.inverted_safe()
    scale: float = _radius_scale(obj)
    local: list[Sample] = []
    sample: Sample
    for sample in values:
        normal: Vec3 = canvas.vector_tuple(
            (obj.matrix_world.to_3x3().transposed() @ Vector(sample.normal)).normalized()
        )
        local.append(
            Sample(
                canvas.vector_tuple(inverse @ Vector(sample.position)),
                sample.radius / max(scale, 1e-12),
                sample.hardness,
                sample.opacity,
                normal,
            )
        )
    brush: Brush = Brush(
        shape=str(obj.get("drawing_shape", "circle")), color=canvas.vector_tuple(obj.color), opacity=float(obj.color[3])
    )
    write_mesh(obj, local, brush, "pipe")
    flattened: list[float] = []
    for sample in local:
        flattened.extend((*sample.position, sample.radius, sample.hardness, sample.opacity, *sample.normal))
    obj["drawing_samples"] = flattened


def trim(context: Any, obj: Any, position: Vec3) -> bool:
    if layers.object_locked(context, obj):
        return False
    objects: list[Any] = pipe_objects(context)
    if obj not in objects:
        return False
    paths: list[list[Sample]] = []
    item: Any
    for item in objects:
        paths.append(samples(item))
    topology: Network = network(objects)
    path_index: int = objects.index(obj)
    path: list[Sample] = paths[path_index]
    parameter: float = 0.0
    distance: float = math.inf
    index: int
    for index in range(len(path) - 1):
        t: float
        u: float
        gap: float
        t, u, gap = closest(path[index].position, path[index + 1].position, position, position)
        if gap < distance:
            parameter, distance = index + t, gap
    parts: list[list[Sample]] | None = trim_parts(topology, path_index, parameter)
    if parts is None:
        get_state().status = "No physical pipe junction bounds this stroke"
        return False
    if not parts:
        mesh: Any = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    else:
        if len(parts) > 1:
            split: Any = obj.copy()
            split.data = obj.data.copy()
            owner: Any
            for owner in obj.users_collection:
                owner.objects.link(split)
            _write_samples(split, parts[1])
        _write_samples(obj, parts[0])
    get_state().status = "Trimmed between physical pipe junctions"
    return True


class DRAWING_OT_pipe_trim(bpy.types.Operator):
    bl_idname: str = "drawing.pipe_trim"
    bl_label: str = "Trim pipe strokes"
    bl_options: set[str] = {"UNDO"}
    points: list[tuple[float, float]]
    area: Any
    region: Any
    handle: Any

    def invoke(self, context: Any, event: Any) -> set[str]:
        self.points = [(float(event.mouse_x), float(event.mouse_y))]
        self.area, self.region = context.area, context.region
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_gesture, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def draw_gesture(self) -> None:
        from ..ui import paint

        if bpy.context.area == self.area:
            point: tuple[float, float]
            points: list[tuple[float, float]] = []
            for point in self.points:
                points.append((point[0] - self.region.x, point[1] - self.region.y))
            paint.line(points)

    def cancel(self, context: Any) -> None:
        bpy.types.SpaceView3D.draw_handler_remove(self.handle, "WINDOW")
        self.area.tag_redraw()

    def modal(self, context: Any, event: Any) -> set[str]:
        if event.type in ("ESC", "RIGHTMOUSE", "WINDOW_DEACTIVATE"):
            self.cancel(context)
            return {"CANCELLED"}
        if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "LEFTMOUSE"):
            self.points.append((float(event.mouse_x), float(event.mouse_y)))
            self.area.tag_redraw()
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self.cancel(context)
            hits: dict[str, Vec3] = {}
            index: int
            for index in range(len(self.points) - 1):
                a: tuple[float, float] = self.points[index]
                b: tuple[float, float] = self.points[index + 1]
                steps: int = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / 3.0))
                step: int
                for step in range(steps + 1):
                    pointer: tuple[float, float] = (
                        a[0] + (b[0] - a[0]) * step / steps,
                        a[1] + (b[1] - a[1]) * step / steps,
                    )
                    origin: Any
                    direction: Any
                    origin, direction = canvas.ray(event, self.area, self.region, pointer=pointer)
                    hit: canvas.Hit | None = canvas.object_hit(context, origin, direction)
                    if hit is not None:
                        obj: Any = bpy.data.objects.get(hit.source)
                        if obj is not None and obj.get("drawing_kind") == "pipe":
                            hits.setdefault(obj.name, hit.position)
            changed: bool = False
            name: str
            position: Vec3
            for name, position in hits.items():
                obj = bpy.data.objects.get(name)
                if obj is not None:
                    changed = trim(context, obj, position) or changed
            if not hits and pencil.active(context):
                bpy.ops.grease_pencil.stroke_trim(
                    path=[{"loc": (point[0] - self.region.x, point[1] - self.region.y)} for point in self.points]
                )
            if changed:
                pencil._undo_push("Trim pipe strokes")
            return {"FINISHED"}
        return {"RUNNING_MODAL"}
