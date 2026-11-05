"""Incremental paint unions clipped to the source mesh's native triangles."""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from ..core.blender import Vector
from ..core.state import Vec3
from .canvas import vector_tuple
from .geometry import Face, Sample, add, basis, cross, dot, mul, unit

Point: TypeAlias = tuple[float, float]
Cell: TypeAlias = tuple[int, int]


def _side(a: Point, b: Point, point: Point) -> float:
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])


def _clip(points: list[Point], triangle: tuple[Point, Point, Point]) -> list[Point]:
    edge: int
    for edge in range(3):
        if not points:
            break
        first: Point = triangle[edge]
        last: Point = triangle[(edge + 1) % 3]
        clipped: list[Point] = []
        previous: Point = points[-1]
        before: float = _side(first, last, previous)
        point: Point
        for point in points:
            after: float = _side(first, last, point)
            if (before >= 0.0) != (after >= 0.0):
                t: float = before / (before - after)
                clipped.append((previous[0] + (point[0] - previous[0]) * t, previous[1] + (point[1] - previous[1]) * t))
            if after >= 0.0:
                clipped.append(point)
            previous, before = point, after
        points = clipped
    return points


@dataclass
class SurfacePatch:
    origin: Vec3
    right: Vec3
    up: Vec3
    normal: Vec3
    triangle: tuple[Point, Point, Point]
    cells: dict[Cell, Face] = field(default_factory=dict)
    active: set[Cell] = field(default_factory=set)
    indices: dict[tuple[int, int], int] = field(default_factory=dict)
    points: list[Vec3] = field(default_factory=list)
    alphas: list[float] = field(default_factory=list)


class PaintSurface:
    def __init__(self, context: Any, obj: Any, radius: float, cell: float) -> None:
        evaluated: Any = obj.evaluated_get(context.evaluated_depsgraph_get())
        mesh: Any = evaluated.to_mesh()
        vertices: list[Vec3] = []
        faces: list[tuple[int, int, int]] = []
        try:
            mesh.calc_loop_triangles()
            vertex: Any
            for vertex in mesh.vertices:
                vertices.append(vector_tuple(evaluated.matrix_world @ vertex.co))
            triangle: Any
            for triangle in mesh.loop_triangles:
                faces.append((int(triangle.vertices[0]), int(triangle.vertices[1]), int(triangle.vertices[2])))
            module: Any = importlib.import_module("mathutils.bvhtree")
            self._tree: Any = module.BVHTree.FromPolygons(vertices, faces, all_triangles=True)
        finally:
            evaluated.to_mesh_clear()
        self._vertices: list[Vec3] = vertices
        self._triangles: list[tuple[int, int, int]] = faces
        self._patches: dict[int, SurfacePatch] = {}
        self._cell: float = cell
        self._offset: float = max(cell * 0.01, 1e-7)
        self._processed: int = 0

    @property
    def patch_count(self) -> int:
        return len(self._patches)

    def _patch(self, index: int) -> SurfacePatch:
        existing: SurfacePatch | None = self._patches.get(index)
        if existing is not None:
            return existing
        indices: tuple[int, int, int] = self._triangles[index]
        origin: Vec3 = self._vertices[indices[0]]
        first: Vec3 = add(self._vertices[indices[1]], mul(origin, -1.0))
        last: Vec3 = add(self._vertices[indices[2]], mul(origin, -1.0))
        normal: Vec3 = unit(cross(first, last))
        right: Vec3 = unit(first)
        up: Vec3 = unit(cross(normal, right))
        patch: SurfacePatch = SurfacePatch(
            origin, right, up, normal, ((0.0, 0.0), (dot(first, right), 0.0), (dot(last, right), dot(last, up)))
        )
        self._patches[index] = patch
        return patch

    def _cell_face(self, patch: SurfacePatch, key: Cell) -> Face:
        if key in patch.cells:
            return patch.cells[key]
        x: float = key[0] * self._cell
        y: float = key[1] * self._cell
        clipped: list[Point] = _clip(
            [(x, y), (x + self._cell, y), (x + self._cell, y + self._cell), (x, y + self._cell)], patch.triangle
        )
        face: list[int] = []
        point: Point
        for point in clipped:
            lookup: tuple[int, int] = (round(point[0] / self._cell * 1e7), round(point[1] / self._cell * 1e7))
            index: int | None = patch.indices.get(lookup)
            if index is None:
                index = len(patch.points)
                patch.indices[lookup] = index
                patch.points.append(add(patch.origin, add(mul(patch.right, point[0]), mul(patch.up, point[1]))))
                patch.alphas.append(0.0)
            if index not in face:
                face.append(index)
        result: Face = tuple(face) if len(face) >= 3 else ()
        patch.cells[key] = result
        return result

    def _stamp(self, patch: SurfacePatch, sample: Sample, shape: str) -> None:
        radius: float = max(sample.radius, self._cell * 0.25)
        reach: float = radius * (1.0 if shape == "circle" else math.sqrt(3.0))
        offset: Vec3 = add(sample.position, mul(patch.origin, -1.0))
        u: float = dot(offset, patch.right)
        v: float = dot(offset, patch.up)
        lo_x: int = math.floor((u - reach) / self._cell)
        hi_x: int = math.floor((u + reach) / self._cell)
        lo_y: int = math.floor((v - reach) / self._cell)
        hi_y: int = math.floor((v + reach) / self._cell)
        lo_x = max(lo_x, math.floor(min(patch.triangle[0][0], patch.triangle[1][0], patch.triangle[2][0]) / self._cell))
        hi_x = min(hi_x, math.floor(max(patch.triangle[0][0], patch.triangle[1][0], patch.triangle[2][0]) / self._cell))
        lo_y = max(lo_y, math.floor(min(patch.triangle[0][1], patch.triangle[1][1], patch.triangle[2][1]) / self._cell))
        hi_y = min(hi_y, math.floor(max(patch.triangle[0][1], patch.triangle[1][1], patch.triangle[2][1]) / self._cell))
        touched: set[int] = set()
        cells: list[Cell] = []
        x: int
        y: int
        for y in range(lo_y, hi_y + 1):
            for x in range(lo_x, hi_x + 1):
                key: Cell = (x, y)
                face: Face = self._cell_face(patch, key)
                if face:
                    cells.append(key)
                    touched.update(face)
        right: Vec3
        up: Vec3
        right, up = basis(sample.normal)
        feather: float = max(1.0 - sample.hardness, self._cell / radius)
        index: int
        for index in touched:
            delta: Vec3 = mul(add(patch.points[index], mul(sample.position, -1.0)), 1.0 / radius)
            a: float = dot(delta, right)
            b: float = dot(delta, up)
            depth: float = dot(delta, sample.normal)
            metric: float = (
                max(abs(a), abs(b), abs(depth))
                if shape == "square"
                else (
                    max(-2.0 * b, math.sqrt(3.0) * a + b, -math.sqrt(3.0) * a + b, abs(depth))
                    if shape == "triangle"
                    else math.sqrt(dot(delta, delta))
                )
            )
            t: float = min(1.0, max(0.0, (1.0 - metric) / feather))
            alpha: float = t * t * (3.0 - 2.0 * t) * sample.opacity
            patch.alphas[index] = max(patch.alphas[index], alpha)
        for key in cells:
            for index in patch.cells[key]:
                if patch.alphas[index] > 0.0:
                    patch.active.add(key)
                    break

    def paint(self, samples: list[Sample], shape: str) -> tuple[list[Vec3], list[Face], list[float]]:
        sample: Sample
        for sample in samples[self._processed :]:
            reach: float = max(sample.radius, self._cell) * (1.0 if shape == "circle" else math.sqrt(3.0))
            hits: Any = self._tree.find_nearest_range(Vector(sample.position), reach)
            visited: set[int] = set()
            hit: Any
            for hit in hits:
                index: int = int(hit[2])
                if index in visited:
                    continue
                visited.add(index)
                patch: SurfacePatch = self._patch(index)
                if dot(patch.normal, sample.normal) >= -0.01:
                    self._stamp(patch, sample, shape)
        self._processed = len(samples)
        points: list[Vec3] = []
        faces: list[Face] = []
        alphas: list[float] = []
        for patch in self._patches.values():
            offset: int = len(points)
            point: Vec3
            for point in patch.points:
                points.append(add(point, mul(patch.normal, self._offset)))
            alphas.extend(patch.alphas)
            key: Cell
            for key in sorted(patch.active):
                face: list[int] = []
                for index in patch.cells[key]:
                    face.append(offset + index)
                faces.append(tuple(face))
        return points, faces, alphas
