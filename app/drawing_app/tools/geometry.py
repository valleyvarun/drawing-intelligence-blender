"""Pressure samples, distance resampling, pipe frames, and sparse paint fields."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TypeAlias

from ..core.state import Vec3

Face: TypeAlias = tuple[int, ...]


def add(a: Vec3, b: Vec3) -> Vec3:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def mul(a: Vec3, value: float) -> Vec3:
    return a[0] * value, a[1] * value, a[2] * value


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec3, fallback: Vec3 = (0.0, 0.0, 1.0)) -> Vec3:
    size: float = length(a)
    return mul(a, 1.0 / size) if size > 1e-12 else fallback


def basis(normal: Vec3) -> tuple[Vec3, Vec3]:
    right: Vec3 = unit(cross(normal, (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (0.0, 1.0, 0.0)))
    return right, unit(cross(normal, right))


@dataclass(frozen=True)
class Sample:
    position: Vec3
    radius: float
    hardness: float
    opacity: float
    normal: Vec3 = (0.0, 0.0, 1.0)

    def blend(self, other: Sample, t: float) -> Sample:
        return Sample(
            add(mul(self.position, 1.0 - t), mul(other.position, t)),
            self.radius + (other.radius - self.radius) * t,
            self.hardness + (other.hardness - self.hardness) * t,
            self.opacity + (other.opacity - self.opacity) * t,
            unit(add(mul(self.normal, 1.0 - t), mul(other.normal, t))),
        )


@dataclass
class PaintCache:
    cell: float
    processed: int = 0
    values: dict[tuple[int, int], tuple[float, float]] = field(default_factory=dict)
    cells: set[tuple[int, int]] = field(default_factory=set)
    meshed: set[tuple[int, int]] = field(default_factory=set)
    indices: dict[tuple[int, int], int] = field(default_factory=dict)
    points: list[Vec3] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    alphas: list[float] = field(default_factory=list)
    charts: list[PaintChart] = field(default_factory=list)
    normals: list[Vec3] = field(default_factory=list)


@dataclass
class PaintChart:
    samples: list[Sample]
    cache: PaintCache


def resample(previous: Sample, current: Sample) -> list[Sample]:
    distance: float = length(add(current.position, mul(previous.position, -1.0)))
    spacing: float = max(1e-6, min(previous.radius, current.radius) * 0.2)
    if distance < spacing:
        return []
    count: int = min(1024, max(1, math.ceil(distance / spacing)))
    result: list[Sample] = []
    index: int
    for index in range(1, count + 1):
        result.append(previous.blend(current, index / count))
    return result


def profile(shape: str) -> list[tuple[float, float]]:
    if shape == "square":
        return [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
    if shape == "triangle":
        return [(0.0, 1.0), (-math.sqrt(3.0) * 0.5, -0.5), (math.sqrt(3.0) * 0.5, -0.5)]
    points: list[tuple[float, float]] = []
    index: int
    for index in range(16):
        angle: float = index * math.tau / 16.0
        points.append((math.cos(angle), math.sin(angle)))
    return points


@dataclass
class PipeCache:
    shape: str = ""
    processed: int = 0
    points: list[Vec3] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    opacity: list[float] = field(default_factory=list)
    frames: list[tuple[Vec3, Vec3, Vec3]] = field(default_factory=list)


def pipe_mesh(
    samples: list[Sample], shape: str, cache: PipeCache | None = None
) -> tuple[list[Vec3], list[Face], list[float]]:
    """Extend the pipe body, revising only the previous endpoint's tangent."""
    body: PipeCache = cache if cache is not None else PipeCache()
    if body.shape != shape or len(samples) < body.processed:
        body.points.clear()
        body.faces.clear()
        body.opacity.clear()
        body.frames.clear()
        body.processed = 0
        body.shape = shape
    points: list[Vec3] = body.points
    faces: list[Face] = body.faces
    opacity: list[float] = body.opacity
    outline: list[tuple[float, float]] = profile(shape)
    count: int = len(outline)
    start: int = max(0, body.processed - 1)
    del points[start * count :]
    del opacity[start * count :]
    del faces[max(0, start - 1) * count :]
    frames: list[tuple[Vec3, Vec3, Vec3]] = body.frames
    del frames[start:]
    previous_right: Vec3 | None = frames[-1][0] if frames else None
    index: int
    sample: Sample
    for index in range(start, len(samples)):
        sample = samples[index]
        before: Vec3 = samples[max(0, index - 1)].position
        after: Vec3 = samples[min(len(samples) - 1, index + 1)].position
        tangent: Vec3 = unit(add(after, mul(before, -1.0)), sample.normal)
        right: Vec3 = (
            basis(tangent)[0]
            if previous_right is None
            else unit(add(previous_right, mul(tangent, -dot(previous_right, tangent))), basis(tangent)[0])
        )
        around: Vec3 = unit(cross(tangent, right))
        frames.append((right, around, tangent))
        previous_right = right
        x: float
        y: float
        for x, y in outline:
            points.append(add(sample.position, mul(add(mul(right, x), mul(around, y)), sample.radius)))
            opacity.append(sample.opacity)
        side: int
        if index:
            for side in range(count):
                a: int = (index - 1) * count + side
                b: int = (index - 1) * count + (side + 1) % count
                faces.append((a, b, b + count, a + count))
    body.processed = len(samples)
    # End caps are transient; keep the cached body ready for the next sample.
    points, faces, opacity = list(points), list(faces), list(opacity)
    if samples:
        if shape == "circle":
            endpoint: int
            sign: float
            for endpoint, sign in ((0, -1.0), (len(samples) - 1, 1.0)):
                sample = samples[endpoint]
                right, around, tangent = frames[endpoint]
                previous: int = endpoint * count
                ring: int
                for ring in range(1, 6):
                    angle: float = ring * math.pi / 12.0
                    center: Vec3 = add(sample.position, mul(tangent, sign * sample.radius * math.sin(angle)))
                    offset: int = len(points)
                    for x, y in outline:
                        points.append(
                            add(center, mul(add(mul(right, x), mul(around, y)), sample.radius * math.cos(angle)))
                        )
                        opacity.append(sample.opacity)
                    for side in range(count):
                        next_side: int = (side + 1) % count
                        face: Face = (previous + side, previous + next_side, offset + next_side, offset + side)
                        faces.append(tuple(reversed(face)) if sign < 0 else face)
                    previous = offset
                tip: int = len(points)
                points.append(add(sample.position, mul(tangent, sign * sample.radius)))
                opacity.append(sample.opacity)
                for side in range(count):
                    face = (previous + side, previous + (side + 1) % count, tip)
                    faces.append(tuple(reversed(face)) if sign < 0 else face)
        else:
            faces.append(tuple(reversed(range(count))))
            faces.append(tuple(range((len(samples) - 1) * count, len(samples) * count)))
    return points, faces, opacity


def paint_mesh(
    samples: list[Sample], shape: str, cache: PaintCache | None = None
) -> tuple[list[Vec3], list[Face], list[float]]:
    """Union stamps in stable tangent charts so sharp turns keep their surface area."""
    if not samples:
        return [], [], []
    sample: Sample
    result: PaintCache
    if cache is None:
        radii: list[float] = []
        for sample in samples:
            radii.append(sample.radius)
        radii.sort()
        result = PaintCache(max(1e-6, radii[len(radii) // 2] * 0.125, radii[-1] / 32.0))
    else:
        result = cache
    chart: PaintChart
    for sample in samples[result.processed :]:
        chosen: PaintChart | None = None
        best: float = 0.85
        for chart in result.charts:
            first: Sample = chart.samples[0]
            agreement: float = dot(first.normal, sample.normal)
            height: float = abs(dot(add(sample.position, mul(first.position, -1.0)), first.normal))
            if agreement > best and height <= max(first.radius, sample.radius) * 4.0:
                chosen, best = chart, agreement
        if chosen is None:
            chosen = PaintChart([], PaintCache(result.cell))
            result.charts.append(chosen)
        chosen.samples.append(sample)
    result.processed = len(samples)
    points: list[Vec3] = []
    faces: list[Face] = []
    alphas: list[float] = []
    normals: list[Vec3] = []
    for chart in result.charts:
        chart_points: list[Vec3]
        chart_faces: list[Face]
        chart_alphas: list[float]
        chart_points, chart_faces, chart_alphas = _paint_chart(chart.samples, shape, chart.cache)
        offset: int = len(points)
        if len(result.charts) == 1:
            points, faces, alphas = chart_points, chart_faces, chart_alphas
        else:
            points.extend(chart_points)
            alphas.extend(chart_alphas)
            face: Face
            index: int
            for face in chart_faces:
                indices: list[int] = []
                for index in face:
                    indices.append(index + offset)
                faces.append(tuple(indices))
        normals.extend([chart.samples[0].normal] * len(chart_points))
    result.normals = normals
    return points, faces, alphas


def _paint_chart(samples: list[Sample], shape: str, cache: PaintCache) -> tuple[list[Vec3], list[Face], list[float]]:
    """Union sparse signed-distance stamps; overlaps never multiply opacity."""
    if not samples:
        return [], [], []
    origin: Vec3 = samples[0].position
    normal: Vec3 = samples[0].normal
    right: Vec3
    up: Vec3
    right, up = basis(normal)
    sample: Sample
    cell: float = cache.cell
    field: dict[tuple[int, int], tuple[float, float]] = cache.values
    cells: set[tuple[int, int]] = cache.cells
    changed: set[tuple[int, int]] = set()
    start: int = cache.processed
    for sample in samples[start:]:
        offset: Vec3 = add(sample.position, mul(origin, -1.0))
        u: float = dot(offset, right)
        v: float = dot(offset, up)
        height: float = dot(offset, normal)
        radius: float = max(sample.radius, cell * 0.25)
        extent: float = radius + cell
        lo_x: int = math.floor((u - extent) / cell)
        hi_x: int = math.ceil((u + extent) / cell)
        lo_y: int = math.floor((v - extent) / cell)
        hi_y: int = math.ceil((v + extent) / cell)
        x: int
        y: int
        for y in range(lo_y, hi_y + 1):
            for x in range(lo_x, hi_x + 1):
                du: float = (x * cell - u) / radius
                dv: float = (y * cell - v) / radius
                metric: float = (
                    max(abs(du), abs(dv))
                    if shape == "square"
                    else (
                        max(-2.0 * dv, math.sqrt(3.0) * du + dv, -math.sqrt(3.0) * du + dv)
                        if shape == "triangle"
                        else math.hypot(du, dv)
                    )
                )
                feather: float = max(1.0 - sample.hardness, cell / radius)
                t: float = min(1.0, max(0.0, (1.0 - metric) / feather))
                alpha: float = t * t * (3.0 - 2.0 * t) * sample.opacity
                key: tuple[int, int] = (x, y)
                if alpha > field.get(key, (0.0, 0.0))[0]:
                    denominator: float = dot(sample.normal, normal)
                    tangent: Vec3 = add(mul(right, x * cell - u), mul(up, y * cell - v))
                    surface_height: float = (
                        height - dot(tangent, sample.normal) / denominator if abs(denominator) > 0.1 else height
                    )
                    field[key] = (alpha, surface_height)
                    changed.add(key)
                    cells.update(((x, y), (x - 1, y), (x, y - 1), (x - 1, y - 1)))
    cache.processed = len(samples)
    output: PaintCache = cache
    points: list[Vec3] = output.points
    faces: list[Face] = output.faces
    alphas: list[float] = output.alphas
    indices: dict[tuple[int, int], int] = output.indices
    for x, y in sorted(cells - output.meshed):
        corners: tuple[tuple[int, int], ...] = ((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1))
        heights: list[float] = []
        corner: tuple[int, int]
        for corner in corners:
            if corner in field:
                heights.append(field[corner][1])
        edge_height: float = sum(heights) / max(1, len(heights))
        face: list[int] = []
        for key in corners:
            if key not in indices:
                alpha, height = field.get(key, (0.0, edge_height))
                indices[key] = len(points)
                points.append(
                    add(
                        origin,
                        add(mul(right, key[0] * cell), add(mul(up, key[1] * cell), mul(normal, height + cell * 0.01))),
                    )
                )
                alphas.append(alpha)
            face.append(indices[key])
        faces.append(tuple(face))
    output.meshed.update(cells)
    for key in changed:
        if key in indices:
            index: int = indices[key]
            alpha, height = field[key]
            points[index] = add(
                origin, add(mul(right, key[0] * cell), add(mul(up, key[1] * cell), mul(normal, height + cell * 0.01)))
            )
            alphas[index] = alpha
    return points, faces, alphas
