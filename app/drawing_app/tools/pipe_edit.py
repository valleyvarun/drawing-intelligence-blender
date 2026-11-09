"""Pipe samples, mesh-confirmed junctions, rebuilding, and Fill dispatch for actual 3D pipe geometry."""

from __future__ import annotations

import math
from importlib import import_module
from typing import Any

from ..core.blender import Vector
from ..core.state import Brush, Vec3
from . import canvas, layers
from .drawing import write_mesh
from .geometry import Sample, length, profile
from .pipe_geometry import Junction, Network, Segment, build_network, sub


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
    """Pipe centerline junctions, confirmed against the actual transformed meshes."""
    paths: list[list[Sample]] = []
    obj: Any
    for obj in objects:
        paths.append(samples(obj))
    result: Network = build_network(paths)
    confirm(result, list(objects))
    return result


def confirm(result: Network, meshes: list[Any]) -> None:
    """Keep the junctions between two pipes that their actual meshes confirm.

    `meshes[i]` is path i's pipe object, or None for another stroke, whose junctions are kept as found.
    """
    pipes: dict[int, Any] = {}
    index: int
    item: Any
    for index, item in enumerate(meshes):
        if item is not None and item.get("drawing_kind") == "pipe":
            pipes[index] = item
    if not result.junctions or len(pipes) < 2:
        return
    paths: list[list[Sample]] = result.paths
    native: Any = import_module("mathutils.bvhtree")
    trees: dict[int, Any] = {}
    obj: Any
    for index, obj in pipes.items():
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
        sides: int = len(profile(str(pipes[path_index].get("drawing_shape", "circle"))))
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
        if first.path not in pipes or second.path not in pipes:
            confirmed.append(junction)
            continue
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


def fill(context: Any, event: Any, area: Any, region: Any) -> bool:
    from .pipe_fill import start

    return start(context, event, area, region)


def write_samples(obj: Any, values: list[Sample]) -> None:
    """Rebuild a pipe along world-space samples, keeping its transform, profile, colour, and materials."""
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
