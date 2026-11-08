"""Pipe junctions and planar regions computed in world space."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TypeAlias

from ..core.state import Vec3
from .geometry import Sample, add, basis, dot, length, mul

Vec2: TypeAlias = tuple[float, float]


def sub(a: Vec3, b: Vec3) -> Vec3:
    return add(a, mul(b, -1.0))


@dataclass(frozen=True)
class Segment:
    path: int
    index: int
    a: Sample
    b: Sample
    distance: float


@dataclass(frozen=True)
class Junction:
    first: int
    second: int
    t: float
    u: float
    point: Vec3
    gap: float
    radius: float


@dataclass
class Network:
    paths: list[list[Sample]]
    segments: list[Segment] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)


def closest(a: Vec3, b: Vec3, c: Vec3, d: Vec3) -> tuple[float, float, float]:
    """Clamped closest points on two finite 3D segments."""
    ab: Vec3 = sub(b, a)
    cd: Vec3 = sub(d, c)
    offset: Vec3 = sub(a, c)
    aa: float = dot(ab, ab)
    bb: float = dot(ab, cd)
    cc: float = dot(cd, cd)
    dd: float = dot(ab, offset)
    ee: float = dot(cd, offset)
    if cc <= 1e-20:
        t: float = max(0.0, min(1.0, -dd / aa)) if aa > 1e-20 else 0.0
        return t, 0.0, length(sub(add(a, mul(ab, t)), c))
    determinant: float = aa * cc - bb * bb
    t = max(0.0, min(1.0, (bb * ee - cc * dd) / determinant)) if determinant > aa * cc * 1e-12 else 0.0
    u: float = (bb * t + ee) / cc if cc > 1e-20 else 0.0
    if u < 0.0 or u > 1.0:
        u = max(0.0, min(1.0, u))
        t = max(0.0, min(1.0, (bb * u - dd) / aa)) if aa > 1e-20 else 0.0
    first: Vec3 = add(a, mul(ab, t))
    second: Vec3 = add(c, mul(cd, u))
    return t, u, length(sub(first, second))


def build_network(paths: list[list[Sample]]) -> Network:
    result: Network = Network(paths)
    path_index: int
    path: list[Sample]
    for path_index, path in enumerate(paths):
        distance: float = 0.0
        index: int
        for index in range(len(path) - 1):
            if length(sub(path[index + 1].position, path[index].position)) > 1e-10:
                result.segments.append(Segment(path_index, index, path[index], path[index + 1], distance))
            distance += length(sub(path[index + 1].position, path[index].position))
    bounds: list[tuple[float, float, float, float, float, float, int]] = []
    segment: Segment
    for index, segment in enumerate(result.segments):
        radius: float = max(segment.a.radius, segment.b.radius)
        a: Vec3 = segment.a.position
        b: Vec3 = segment.b.position
        bounds.append(
            (
                min(a[0], b[0]) - radius,
                max(a[0], b[0]) + radius,
                min(a[1], b[1]) - radius,
                max(a[1], b[1]) + radius,
                min(a[2], b[2]) - radius,
                max(a[2], b[2]) + radius,
                index,
            )
        )
    bounds.sort()
    active: list[tuple[float, float, float, float, float, float, int]] = []
    candidates: list[Junction] = []
    box: tuple[float, float, float, float, float, float, int]
    other: tuple[float, float, float, float, float, float, int]
    for box in bounds:
        active = [other for other in active if other[1] >= box[0]]
        first: Segment = result.segments[box[6]]
        for other in active:
            if box[2] > other[3] or other[2] > box[3] or box[4] > other[5] or other[4] > box[5]:
                continue
            second: Segment = result.segments[other[6]]
            radius = max(first.a.radius, first.b.radius) + max(second.a.radius, second.b.radius)
            if first.path == second.path and (
                abs(first.index - second.index) <= 1 or abs(first.distance - second.distance) <= radius * 4.0
            ):
                continue
            t: float
            u: float
            gap: float
            t, u, gap = closest(first.a.position, first.b.position, second.a.position, second.b.position)
            radius = first.a.blend(first.b, t).radius + second.a.blend(second.b, u).radius
            if gap > radius + 1e-8:
                continue
            point: Vec3 = mul(add(first.a.blend(first.b, t).position, second.a.blend(second.b, u).position), 0.5)
            candidates.append(Junction(box[6], other[6], t, u, point, gap, radius))
        active.append(box)
    candidates.sort(key=lambda junction: junction.gap)
    junction: Junction
    for junction in candidates:
        pair: set[int] = {result.segments[junction.first].path, result.segments[junction.second].path}
        duplicate: bool = False
        previous: Junction
        for previous in result.junctions:
            if pair == {result.segments[previous.first].path, result.segments[previous.second].path} and (
                length(sub(junction.point, previous.point)) <= 2.0 * max(junction.radius, previous.radius)
            ):
                duplicate = True
                break
        if not duplicate:
            result.junctions.append(junction)
    return result


def inside(point: Vec2, polygon: list[Vec2]) -> bool:
    result: bool = False
    index: int
    for index in range(len(polygon)):
        a: Vec2 = polygon[index - 1]
        b: Vec2 = polygon[index]
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (
            (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]
        ):
            result = not result
    return result


def _remove_bridges(neighbors: dict[int, set[int]]) -> None:
    """Ignore open tails and connectors that cannot bound a filled face."""
    discovered: dict[int, int] = {}
    low: dict[int, int] = {}
    parents: dict[int, int] = {}
    bridges: list[tuple[int, int]] = []
    root: int
    for root in neighbors:
        if root in discovered:
            continue
        discovered[root] = low[root] = len(discovered)
        stack: list[tuple[int, Iterator[int]]] = [(root, iter(neighbors[root]))]
        while stack:
            current: int
            edges: Iterator[int]
            current, edges = stack[-1]
            other: int | None = next(edges, None)
            parent: int | None = parents.get(current)
            if other is None:
                stack.pop()
                if parent is not None:
                    low[parent] = min(low[parent], low[current])
                    if low[current] > discovered[parent]:
                        bridges.append((parent, current))
            elif other not in discovered:
                parents[other] = current
                discovered[other] = low[other] = len(discovered)
                stack.append((other, iter(neighbors[other])))
            elif other != parent:
                low[current] = min(low[current], discovered[other])
    a: int
    b: int
    for a, b in bridges:
        neighbors[a].remove(b)
        neighbors[b].remove(a)


def _faces(network: Network, origin: Vec3, normal: Vec3, tolerance: float) -> list[list[Vec3]]:
    cuts: dict[int, list[tuple[float, Vec3]]] = {}
    index: int
    segment: Segment
    for index, segment in enumerate(network.segments):
        if (
            max(abs(dot(sub(segment.a.position, origin), normal)), abs(dot(sub(segment.b.position, origin), normal)))
            <= tolerance
        ):
            cuts[index] = [(0.0, segment.a.position), (1.0, segment.b.position)]
    junction: Junction
    for junction in network.junctions:
        if junction.first in cuts and junction.second in cuts:
            cuts[junction.first].append((junction.t, junction.point))
            cuts[junction.second].append((junction.u, junction.point))
    right: Vec3
    up: Vec3
    right, up = basis(normal)
    positions: list[Vec3] = []
    coordinates: list[Vec2] = []
    nodes: dict[tuple[int, int], int] = {}
    neighbors: dict[int, set[int]] = {}

    def node(point: Vec3) -> int:
        point = sub(point, mul(normal, dot(sub(point, origin), normal)))
        projected: Vec2 = (dot(sub(point, origin), right), dot(sub(point, origin), up))
        key: tuple[int, int] = (round(projected[0] / tolerance), round(projected[1] / tolerance))
        if key not in nodes:
            nodes[key] = len(positions)
            positions.append(point)
            coordinates.append(projected)
            neighbors[nodes[key]] = set()
        return nodes[key]

    values: list[tuple[float, Vec3]]
    for values in cuts.values():
        values.sort(key=lambda value: value[0])
        previous: int = node(values[0][1])
        _parameter: float
        point: Vec3
        for _parameter, point in values[1:]:
            current: int = node(point)
            if previous != current:
                neighbors[previous].add(current)
                neighbors[current].add(previous)
            previous = current
    _remove_bridges(neighbors)
    ordered: dict[int, list[int]] = {}
    for index in neighbors:

        def angle(other: int, center: int = index) -> float:
            return math.atan2(
                coordinates[other][1] - coordinates[center][1], coordinates[other][0] - coordinates[center][0]
            )

        ordered[index] = sorted(neighbors[index], key=angle)
    visited: set[tuple[int, int]] = set()
    faces: list[list[Vec3]] = []
    a: int
    b: int
    for a in neighbors:
        for b in neighbors[a]:
            if (a, b) in visited:
                continue
            edge: tuple[int, int] = (a, b)
            face: list[int] = []
            while edge not in visited:
                visited.add(edge)
                face.append(edge[0])
                options: list[int] = ordered[edge[1]]
                edge = (edge[1], options[(options.index(edge[0]) - 1) % len(options)])
            if edge != (a, b) or len(face) < 3:
                continue
            area: float = 0.0
            for index in range(len(face)):
                p: Vec2 = coordinates[face[index - 1]]
                q: Vec2 = coordinates[face[index]]
                area += p[0] * q[1] - q[0] * p[1]
            if area > tolerance * tolerance:
                faces.append([positions[index] for index in face])
    return faces


def trim_parts(network: Network, path_index: int, parameter: float) -> list[list[Sample]] | None:
    """Remove the picked interval bounded by actual 3D contacts and stroke endpoints."""
    path: list[Sample] = network.paths[path_index]
    cuts: list[float] = []
    junction: Junction
    for junction in network.junctions:
        for segment_index, amount in ((junction.first, junction.t), (junction.second, junction.u)):
            segment: Segment = network.segments[segment_index]
            if segment.path == path_index:
                cuts.append(segment.index + amount)
    if not cuts:
        return None
    cuts = sorted({0.0, float(len(path) - 1), *cuts})
    start: float = 0.0
    end: float = float(len(path) - 1)
    index: int
    for index in range(len(cuts) - 1):
        if cuts[index] <= parameter <= cuts[index + 1]:
            start, end = cuts[index], cuts[index + 1]
            break

    def portion(first: float, last: float) -> list[Sample]:
        if last - first < 1e-8:
            return []
        a: int = min(int(first), len(path) - 2)
        b: int = min(int(last), len(path) - 2)
        samples: list[Sample] = [path[a].blend(path[a + 1], first - a)]
        samples.extend(path[math.floor(first) + 1 : math.ceil(last)])
        samples.append(path[b].blend(path[b + 1], last - b))
        return samples

    return [part for part in (portion(0.0, start), portion(end, float(len(path) - 1))) if len(part) >= 2]
