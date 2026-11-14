"""Sketch Mesh: a surface through a skeleton of drawn lines.

numpy and Python, without Blender, so it can be tested on its own; only the triangulation of points in a plane is
handed in (Blender's constrained Delaunay triangulation in the app). `build` makes the surface in the way that
suits how the lines lie:

- Loft: lines that do not touch each other and are not all in one plane (ribs, or rings) are sampled at matching
  places and joined rib to rib. A closed loft caps both ends; open ribs are first closed with a straight side.
- Patch: lines that can be seen whole from one side are a height field over the plane they fit best. They are
  triangulated in that plane, the triangles outside the outermost lines are dropped, and heights come from the
  lines. Closed, the patch becomes a solid with walls down to a flat base; a flat sketch is inflated instead.
- Shell: lines that fold around a shape (a cage) are a radius in every direction from their centre. Open, the
  widest opening the lines surround is left open.

Curved surfaces add points between the lines and bend smoothly through them (a thin-plate spline, or a
Catmull-Rom spline across ribs). Straight surfaces only join the lines' own points with flat faces.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, TypeAlias

# numpy ships with Blender's Python; editors without that interpreter see it as a dynamic module.
np: Any = import_module("numpy")

Edge: TypeAlias = tuple[int, int]
Triangle: TypeAlias = tuple[int, int, int]
Face: TypeAlias = tuple[int, ...]

# The mesh is about this many faces across; the Tool Box slider's range and default.
DETAIL_LIMITS: tuple[int, int] = (8, 64)
DETAIL: int = 24
# A line turning by more than this within one face length has a corner there, which the mesh keeps.
CORNER: float = math.radians(35.0)
# Line points and rib points, at most; the spacing grows to stay under them.
POINT_LIMIT: int = 2400
LOFT_LIMIT: int = 160
# Smoothing of the spline through the lines: enough to calm hand jitter, far below what is visible.
SMOOTHING: float = 1e-5
SPLINE_LIMIT: int = 700
# Lines with more than this share of their points folded over each other, seen from every side, are not a
# sheet over a plane: they surround a shape. A sheet's lines hardly fold at all; a cage's fold by a third
# or more from every side.
FOLD_LIMIT: float = 0.15
# A line rising this steeply from the plane (as the sine of its angle) is a wall there, not a fold.
STEEP: float = 0.9


class SketchError(ValueError):
    """The lines cannot make a surface; the message tells the user why."""


@dataclass
class Line:
    """One drawn line; a closed line's last point joins its first."""

    points: Any
    closed: bool = False

    def path(self) -> tuple[Any, Any]:
        """The polyline, with a loop's closing segment, and the distance along it to each of its points."""
        points: Any = np.concatenate((self.points, self.points[:1])) if self.closed else self.points
        return points, _lengths(points)

    @property
    def length(self) -> float:
        return float(self.path()[1][-1])


@dataclass
class Triangulation:
    """Triangles of points in a plane, as the triangulation handed to `build` returns them.

    Triangles are counter-clockwise. `sources` lists, for every output point, the input points it stands for
    (none for a point added where two constraint edges cross). `constraints` maps each output edge that lies on
    input edges to those edges.
    """

    points: Any
    triangles: list[Triangle]
    sources: list[list[int]]
    constraints: dict[Edge, list[int]] = field(default_factory=dict)


Triangulate: TypeAlias = Callable[[Any, list[Edge]], Triangulation]


@dataclass
class Surface:
    """The mesh: points, faces wound the same way, how it was made, and whether it is shaded smooth."""

    points: Any
    faces: list[Face]
    method: str
    smooth: bool


def blender_triangulate(points: Any, edges: list[Edge]) -> Triangulation:
    """Blender's constrained Delaunay triangulation of the points' whole convex hull."""
    mathutils: Any = import_module("mathutils")
    geometry: Any = import_module("mathutils.geometry")
    vectors: list[Any] = [mathutils.Vector((float(point[0]), float(point[1]))) for point in points]
    result: Any = geometry.delaunay_2d_cdt(vectors, edges, [], 0, 1e-9, True)
    coordinates: Any = np.array([(float(v[0]), float(v[1])) for v in result[0]], dtype=np.float64).reshape(-1, 2)
    triangles: list[Triangle] = []
    face: Any
    for face in result[2]:
        if len(face) == 3:
            triangles.append((int(face[0]), int(face[1]), int(face[2])))
    constraints: dict[Edge, list[int]] = {}
    index: int
    pair: Any
    for index, pair in enumerate(result[1]):
        origins: list[int] = [int(origin) for origin in result[4][index]]
        if origins:
            constraints[_edge(int(pair[0]), int(pair[1]))] = origins
    sources: list[list[int]] = [[int(origin) for origin in origins] for origins in result[3]]
    return Triangulation(coordinates, triangles, sources, constraints)


def _edge(a: int, b: int) -> Edge:
    return (a, b) if a < b else (b, a)


def _lengths(points: Any) -> Any:
    """Distance along a polyline to each of its points."""
    steps: Any = np.linalg.norm(np.diff(points, axis=0), axis=1)
    return np.concatenate(([0.0], np.cumsum(steps)))


def _along(points: Any, cumulative: Any, at: Any) -> Any:
    """Points at the given distances along a polyline."""
    columns: list[Any] = [np.interp(at, cumulative, points[:, axis]) for axis in range(points.shape[1])]
    return np.stack(columns, axis=-1)


def _loop(points: Any, cumulative: Any, radius: float) -> tuple[Any, bool]:
    """A line whose two ends meet, or cross near each other, is a loop: its tails beyond the meeting are cut."""
    total: float = float(cumulative[-1])
    tolerance: float = min(max(4.0 * radius, 0.08 * total), 0.25 * total)
    head: Any = np.nonzero(cumulative <= 0.15 * total)[0]
    tail: Any = np.nonzero(cumulative >= 0.85 * total)[0]
    head = head[:: max(1, len(head) // 300)]
    tail = tail[:: max(1, len(tail) // 300)]
    gaps: Any = np.linalg.norm(points[head][:, None, :] - points[tail][None, :, :], axis=2)
    first: int
    last: int
    first, last = (int(value) for value in np.unravel_index(int(np.argmin(gaps)), gaps.shape))
    if float(gaps[first, last]) > tolerance:
        return points, False
    loop: Any = points[int(head[first]) : int(tail[last]) + 1]
    if len(loop) > 3 and float(np.linalg.norm(loop[-1] - loop[0])) <= 1e-9 + 1e-6 * total:
        loop = loop[:-1]
    return (loop, True) if len(loop) >= 3 else (points, False)


def prepare(paths: list[Any], radius: float) -> list[Line]:
    """Usable lines from drawn paths: finite, longer than a dab, and closed where their ends meet."""
    lines: list[Line] = []
    path: Any
    for path in paths:
        points: Any = np.asarray(path, dtype=np.float64).reshape(-1, 3)
        points = points[np.isfinite(points).all(axis=1)]
        if len(points) > 20000:
            points = np.concatenate((points[:: math.ceil(len(points) / 20000)], points[-1:]))
        if len(points) >= 2:
            apart: Any = np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-12
            points = points[np.concatenate(([True], apart))]
        if len(points) < 2:
            continue
        cumulative: Any = _lengths(points)
        if float(cumulative[-1]) <= 2.0 * radius:
            continue
        closed: bool
        points, closed = _loop(points, cumulative, radius)
        lines.append(Line(points, closed))
    return lines


def _corners(line: Line, window: float) -> Any:
    """Distances along a line to its sharp corners, measured over `window` so hand jitter is not one."""
    path: Any
    cumulative: Any
    path, cumulative = line.path()
    total: float = float(cumulative[-1])
    if total < 2.0 * window:
        return np.zeros(0)
    at: Any = cumulative[:-1] if line.closed else cumulative[1:-1]
    if not line.closed:
        at = at[(at >= 0.5 * window) & (at <= total - 0.5 * window)]
    if len(at) == 0:
        return np.zeros(0)
    before: Any = at - window
    after: Any = at + window
    if line.closed:
        before, after = np.mod(before, total), np.mod(after, total)
    else:
        before, after = np.clip(before, 0.0, total), np.clip(after, 0.0, total)
    here: Any = _along(path, cumulative, at)
    back: Any = here - _along(path, cumulative, before)
    ahead: Any = _along(path, cumulative, after) - here
    sizes: Any = np.linalg.norm(back, axis=1) * np.linalg.norm(ahead, axis=1)
    cosine: Any = np.sum(back * ahead, axis=1) / np.maximum(sizes, 1e-300)
    angles: Any = np.arccos(np.clip(cosine, -1.0, 1.0))
    chosen: list[float] = []
    index: int
    # The sharpest first; a corner's neighbours within the window belong to it.
    for index in np.argsort(-angles):
        if float(angles[index]) <= CORNER:
            break
        position: float = float(at[index])
        taken: bool = False
        other: float
        for other in chosen:
            distance: float = abs(position - other)
            if line.closed:
                distance = min(distance, total - distance)
            if distance < window:
                taken = True
                break
        if not taken:
            chosen.append(position)
    return np.array(sorted(chosen), dtype=np.float64)


def _resample(line: Line, spacing: float) -> Line:
    """The line as points about `spacing` apart, with a point on each corner and on both ends."""
    path: Any
    cumulative: Any
    path, cumulative = line.path()
    total: float = float(cumulative[-1])
    corners: Any = _corners(line, spacing)
    pieces: list[Any] = []
    start: float
    end: float
    if line.closed and len(corners) == 0:
        count: int = max(3, round(total / spacing))
        pieces.append(np.arange(count) * (total / count))
    elif line.closed:
        breaks: list[float] = [float(corner) for corner in corners] + [float(corners[0]) + total]
        for start, end in zip(breaks[:-1], breaks[1:]):
            pieces.append(np.linspace(start, end, max(1, round((end - start) / spacing)), endpoint=False))
    else:
        breaks = [0.0] + [float(corner) for corner in corners] + [total]
        for start, end in zip(breaks[:-1], breaks[1:]):
            pieces.append(np.linspace(start, end, max(1, round((end - start) / spacing)), endpoint=False))
        pieces.append(np.array([total]))
    at: Any = np.concatenate(pieces)
    if line.closed:
        at = np.mod(at, total)
        if len(at) < 3:
            at = np.arange(3) * (total / 3.0)
    return Line(_along(path, cumulative, at), line.closed)


def _pair_gaps(clouds: list[Any]) -> Any:
    """Smallest distance between every two lines' points; zero for a line with no points of its own left."""
    count: int = len(clouds)
    gaps: Any = np.zeros((count, count))
    first: int
    second: int
    for first in range(count):
        for second in range(first + 1, count):
            if len(clouds[first]) and len(clouds[second]):
                apart: Any = clouds[first][:, None, :] - clouds[second][None, :, :]
                gaps[first, second] = gaps[second, first] = math.sqrt(float((apart**2).sum(axis=2).min()))
    return gaps


def _chain(lines: list[Line]) -> list[int]:
    """Ribs in the order they follow each other: from the one farthest out, always to the nearest one left."""
    samples: list[Any] = []
    line: Line
    for line in lines:
        path: Any
        cumulative: Any
        path, cumulative = line.path()
        samples.append(_along(path, cumulative, np.linspace(0.0, float(cumulative[-1]), 32)))
    count: int = len(lines)
    apart: Any = np.zeros((count, count))
    first: int
    second: int
    for first in range(count):
        for second in range(first + 1, count):
            distances: Any = np.linalg.norm(samples[first][:, None, :] - samples[second][None, :, :], axis=2)
            apart[first, second] = apart[second, first] = 0.5 * float(
                distances.min(axis=1).mean() + distances.min(axis=0).mean()
            )
    order: list[int] = [int(np.argmax(apart.sum(axis=1)))]
    while len(order) < count:
        nearest: Any = apart[order[-1]].copy()
        nearest[order] = np.inf
        order.append(int(np.argmin(nearest)))
    return order


def _snap(fractions: Any, corners: list[float], cyclic: bool) -> Any:
    """Move the nearest sample onto each corner, so every rib is sampled at the others' corners too."""
    result: Any = fractions.copy()
    count: int = len(result)
    step: float = 1.0 / count if cyclic else 1.0 / max(1, count - 1)
    moved: set[int] = set()
    corner: float
    for corner in sorted(corners):
        offsets: Any = np.abs(fractions - corner)
        if cyclic:
            offsets = np.minimum(offsets, 1.0 - offsets)
        index: int = int(np.argmin(offsets))
        if index in moved or float(offsets[index]) > 0.5 * step:
            continue
        if not cyclic and index in (0, count - 1):
            continue
        result[index] = corner
        moved.add(index)
    return result


def _spline_rows(grid: Any, spacing: float) -> Any:
    """Rows added between the ribs along a Catmull-Rom spline through them, about `spacing` apart."""
    count: int = len(grid)
    chords: Any = np.maximum(np.linalg.norm(np.diff(grid, axis=0), axis=2).mean(axis=1), 1e-12)
    slopes: Any = np.diff(grid, axis=0) / chords[:, None, None]
    tangents: Any = np.zeros_like(grid)
    tangents[0], tangents[-1] = slopes[0], slopes[-1]
    index: int
    for index in range(1, count - 1):
        before: float = float(chords[index - 1])
        after: float = float(chords[index])
        tangents[index] = (slopes[index - 1] * after + slopes[index] * before) / (before + after)
    rows: list[Any] = []
    room: int = max(1, (LOFT_LIMIT - count) // max(1, count - 1) + 1)
    for index in range(count - 1):
        chord: float = float(chords[index])
        steps: int = int(min(room, 16, max(1, round(chord / spacing))))
        step: int
        for step in range(steps):
            t: float = step / steps
            rows.append(
                (2 * t**3 - 3 * t**2 + 1) * grid[index]
                + (t**3 - 2 * t**2 + t) * chord * tangents[index]
                + (-2 * t**3 + 3 * t**2) * grid[index + 1]
                + (t**3 - t**2) * chord * tangents[index + 1]
            )
    rows.append(grid[-1])
    return np.stack(rows)


def _loft(lines: list[Line], spacing: float, curved: bool, caps: bool) -> Surface | None:
    """Ribs (or rings) joined in order; with caps, a closed solid whose open ribs get a straight closing side.

    None when the lines do not run alongside each other like ribs, so another way suits them better.
    """
    rings: bool = lines[0].closed
    ordered: list[Line] = [lines[index] for index in _chain(lines)]
    longest: float = max(line.length for line in ordered)
    count: int = int(min(LOFT_LIMIT, max(8 if rings else 2, round(longest / spacing) + (0 if rings else 1))))
    base: Any = np.arange(count) / count if rings else np.linspace(0.0, 1.0, count)
    # Each rib's start (a distance along it) and direction, so that it lines up with the rib before it.
    starts: list[float] = []
    directions: list[float] = []
    previous: Any = None
    line: Line
    for line in ordered:
        path: Any
        cumulative: Any
        path, cumulative = line.path()
        total: float = float(cumulative[-1])
        forward: Any = _along(path, cumulative, base * total)
        backward: Any = np.roll(forward[::-1], 1, axis=0) if rings else forward[::-1]
        start: float = 0.0
        direction: float = 1.0
        aligned: Any = forward
        if previous is not None:
            best: float = math.inf
            sign: float
            turned: Any
            for sign, turned in ((1.0, forward), (-1.0, backward)):
                shift: int
                for shift in range(count if rings else 1):
                    candidate: Any = np.roll(turned, -shift, axis=0)
                    cost: float = float(((previous - candidate) ** 2).sum())
                    if cost < best:
                        best, direction, aligned = cost, sign, candidate
                        if rings:
                            start = (shift if sign > 0.0 else (-shift) % count) * total / count
                        else:
                            start = 0.0 if sign > 0.0 else total
        if previous is not None and _agreement(previous, aligned, rings) < 0.5:
            return None
        starts.append(start)
        directions.append(direction)
        previous = aligned
    # Corners of every rib become samples of all of them.
    corners: list[float] = []
    window: float = max(spacing, longest / count)
    index: int
    for index, line in enumerate(ordered):
        total = line.length
        corner: float
        for corner in _corners(line, min(window, total / 4.0)):
            share: float = (float(corner) - starts[index]) * directions[index] / total
            corners.append(share % 1.0 if rings else share)
    fractions: Any = _snap(base, corners, rings)
    rows: list[Any] = []
    for index, line in enumerate(ordered):
        path, cumulative = line.path()
        total = float(cumulative[-1])
        at: Any = (
            np.mod(starts[index] + directions[index] * fractions * total, total)
            if rings
            else _open(fractions, total, directions[index])
        )
        rows.append(_along(path, cumulative, at))
    cyclic: bool = rings
    if caps and not rings:
        # A straight side from each rib's end back to its start makes it a ring.
        chord: float = float(np.mean([np.linalg.norm(row[-1] - row[0]) for row in rows]))
        extra: int = int(min(60, max(0, round(chord / spacing) - 1)))
        shares: Any = (np.arange(extra) + 1.0)[:, None] / (extra + 1.0)
        rows = [np.concatenate((row, row[-1] + (row[0] - row[-1]) * shares)) for row in rows]
        cyclic = True
    grid: Any = np.stack(rows)
    if curved and len(grid) >= 3:
        grid = _spline_rows(grid, spacing)
    height: int = len(grid)
    width: int = grid.shape[1]
    points: Any = grid.reshape(-1, 3)
    faces: list[Face] = []
    row: int
    column: int
    for row in range(height - 1):
        for column in range(width if cyclic else width - 1):
            after: int = (column + 1) % width
            faces.append(
                (row * width + column, row * width + after, (row + 1) * width + after, (row + 1) * width + column)
            )
    if caps:
        centres: Any = np.stack((grid[0].mean(axis=0), grid[-1].mean(axis=0)))
        points = np.concatenate((points, centres))
        top: int = height * width
        for column in range(width):
            after = (column + 1) % width
            faces.append((top, after, column))
            faces.append((top + 1, (height - 1) * width + column, (height - 1) * width + after))
        if _volume(points, faces) < 0.0:
            faces = [tuple(reversed(face)) for face in faces]
    return Surface(points, faces, "loft", curved)


def _agreement(first: Any, second: Any, cyclic: bool) -> float:
    """How far two matched ribs run the same way: the mean cosine between their directions, point for point."""
    if cyclic:
        one: Any = np.roll(first, -1, axis=0) - np.roll(first, 1, axis=0)
        two: Any = np.roll(second, -1, axis=0) - np.roll(second, 1, axis=0)
    else:
        one, two = np.gradient(first, axis=0), np.gradient(second, axis=0)
    sizes: Any = np.linalg.norm(one, axis=1) * np.linalg.norm(two, axis=1)
    return float(((one * two).sum(axis=1) / np.maximum(sizes, 1e-300)).mean())


def _open(fractions: Any, total: float, direction: float) -> Any:
    """Distances along an open rib for shares of its length, from either end."""
    return fractions * total if direction > 0.0 else (1.0 - fractions) * total


def _volume(points: Any, faces: list[Face]) -> float:
    """Signed volume inside the faces: positive when they are wound counter-clockwise seen from outside."""
    centre: Any = points.mean(axis=0)
    total: float = 0.0
    face: Face
    for face in faces:
        corner: int
        for corner in range(1, len(face) - 1):
            a: Any = points[face[0]] - centre
            b: Any = points[face[corner]] - centre
            c: Any = points[face[corner + 1]] - centre
            total += float(np.dot(a, np.cross(b, c)))
    return total / 6.0


@dataclass
class _Network:
    """The lines' points as one list, the segments between them, and which line each belongs to."""

    points: Any
    line: Any
    # The direction each point's line runs there.
    tangent: Any
    edges: list[Edge]
    # The line each edge is part of; -1 for an edge joining a line's end to another line.
    edge_line: list[int]


def _network(lines: list[Line], tolerance: float, spacing: float) -> _Network:
    """Join the lines into a network: an end close to another line snaps onto it or gets a joining edge."""
    points: Any = np.concatenate([line.points for line in lines])
    owner: list[int] = []
    position: list[float] = []
    tangents: list[Any] = []
    edges: list[Edge] = []
    edge_line: list[int] = []
    ends: list[int] = []
    offset: int = 0
    index: int
    line: Line
    for index, line in enumerate(lines):
        count: int = len(line.points)
        owner.extend([index] * count)
        position.extend(float(value) for value in _lengths(line.points))
        if line.closed:
            run: Any = np.roll(line.points, -1, axis=0) - np.roll(line.points, 1, axis=0)
        else:
            run = np.gradient(line.points, axis=0)
        tangents.append(run / np.maximum(np.linalg.norm(run, axis=1), 1e-300)[:, None])
        step: int
        for step in range(count - 1):
            edges.append((offset + step, offset + step + 1))
            edge_line.append(index)
        if line.closed:
            edges.append((offset + count - 1, offset))
            edge_line.append(index)
        else:
            ends.extend((offset, offset + count - 1))
        offset += count
    lines_of: Any = np.array(owner)
    along: Any = np.array(position)
    everyone: Any = np.arange(len(points))
    alias: Any = everyone.copy()
    end: int
    for end in ends:
        # Another line, or a far part of this one, that has not itself been snapped away.
        allowed: Any = ((lines_of != lines_of[end]) | (np.abs(along - along[end]) > 3.0 * tolerance)) & (
            alias == everyone
        )
        allowed[end] = False
        if not allowed.any():
            continue
        candidates: Any = np.nonzero(allowed)[0]
        distances: Any = np.linalg.norm(points[candidates] - points[end], axis=1)
        nearest: int = int(np.argmin(distances))
        if float(distances[nearest]) > tolerance:
            continue
        if float(distances[nearest]) <= 0.5 * spacing:
            alias[end] = int(candidates[nearest])
        else:
            edges.append((end, int(candidates[nearest])))
            edge_line.append(-1)
    # Where several ends meet, each follows the chain to the one point that stays.
    while (alias[alias] != alias).any():
        alias = alias[alias]
    kept: Any = np.nonzero(alias == everyone)[0]
    renumber: Any = np.full(len(points), -1)
    renumber[kept] = np.arange(len(kept))
    joined: list[Edge] = []
    joined_line: list[int] = []
    seen: set[Edge] = set()
    edge: Edge
    for index, edge in enumerate(edges):
        a: int = int(renumber[alias[edge[0]]])
        b: int = int(renumber[alias[edge[1]]])
        if a == b or _edge(a, b) in seen:
            continue
        seen.add(_edge(a, b))
        joined.append((a, b))
        joined_line.append(edge_line[index])
    return _Network(points[kept], lines_of[kept], np.concatenate(tangents)[kept], joined, joined_line)


def _frames(points: Any) -> list[Any]:
    """Planes to try, as (right, up, normal) rows: the cloud's own three axes, then the world's."""
    centred: Any = points - points.mean(axis=0)
    axes: Any = np.linalg.svd(centred, full_matrices=False)[2]
    normals: list[Any] = [axes[2], axes[1], axes[0], np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0])]
    normals.append(np.array([1.0, 0.0, 0.0]))
    frames: list[Any] = []
    normal: Any
    for normal in normals:
        normal = normal / max(float(np.linalg.norm(normal)), 1e-300)
        # Lines drawn on the ground or an upright plane get exactly that plane, not one a hair off it.
        if float(np.abs(normal).max()) > 0.9998:
            normal = np.where(np.abs(normal) == np.abs(normal).max(), np.sign(normal), 0.0)
        if any(abs(float(np.dot(normal, frame[2]))) > 0.999 for frame in frames):
            continue
        # Face upward where there is a choice, so the surface's front is its top.
        if float(normal[2]) < -1e-9:
            normal = -normal
        helper: Any = np.array([0.0, 0.0, 1.0]) if abs(float(normal[2])) < 0.9 else np.array([1.0, 0.0, 0.0])
        right: Any = np.cross(helper, normal)
        right = right / float(np.linalg.norm(right))
        frames.append(np.stack((right, np.cross(normal, right), normal)))
    return frames


def _folds(flat: Any, height: Any, climb: Any, owner: Any, edges: list[Edge], reach: float) -> float:
    """Share of points that lie over or under another part of the lines: the sheet folds there.

    A point counts when a segment passes within `reach` of it, seen from above the plane, at a clearly different
    height. Where a line climbs steeply it is a wall, not a fold, and neither its points nor its segments count.
    """
    if not edges:
        return 0.0
    pairs: Any = np.array(edges, dtype=np.int64)
    pairs = pairs[(np.abs(climb[pairs[:, 0]]) < STEEP) & (np.abs(climb[pairs[:, 1]]) < STEEP)]
    across: Any = np.nonzero(np.abs(climb) < STEEP)[0]
    stride: int = max(1, len(across) // 1200)
    chosen: Any = across[::stride]
    if len(pairs) == 0 or len(chosen) == 0:
        return 0.0
    start: Any = flat[pairs[:, 0]][None, :, :]
    span: Any = flat[pairs[:, 1]][None, :, :] - start
    lengths: Any = np.maximum((span**2).sum(axis=2), 1e-300)
    low: Any = height[pairs[:, 0]][None, :]
    rise: Any = height[pairs[:, 1]][None, :] - low
    folded: int = 0
    first: int
    for first in range(0, len(chosen), 256):
        part: Any = chosen[first : first + 256]
        offset: Any = flat[part][:, None, :] - start
        along: Any = np.clip((offset * span).sum(axis=2) / lengths, 0.0, 1.0)
        gap: Any = ((offset - along[:, :, None] * span) ** 2).sum(axis=2)
        apart: Any = np.abs(height[part][:, None] - (low + along * rise)) > 3.0 * reach
        # A point's neighbours along its own line are never a fold.
        own: Any = (owner[part][:, None] == owner[pairs[:, 0]][None, :]) & (
            np.minimum(np.abs(part[:, None] - pairs[None, :, 0]), np.abs(part[:, None] - pairs[None, :, 1])) <= 3
        )
        folded += int(((gap < reach * reach) & apart & ~own).any(axis=1).sum())
    return min(1.0, folded * stride / len(flat))


def _overpasses(flat: Any, height: Any, edges: list[Edge], reach: float) -> int:
    """How many times one line passes over another, seen from above the plane, without touching it."""
    if not edges:
        return 0
    pairs: Any = np.array(edges, dtype=np.int64)
    start: Any = flat[pairs[:, 0]]
    end: Any = flat[pairs[:, 1]]
    low: Any = height[pairs[:, 0]]
    high: Any = height[pairs[:, 1]]

    def side(a: Any, b: Any, point: Any) -> Any:
        return (b[..., 0] - a[..., 0]) * (point[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (
            point[..., 0] - a[..., 0]
        )

    found: int = 0
    first: int
    for first in range(0, len(pairs), 256):
        own: Any = slice(first, first + 256)
        a: Any = start[own][:, None, :]
        b: Any = end[own][:, None, :]
        one: Any = side(start[None], end[None], a)
        two: Any = side(start[None], end[None], b)
        three: Any = side(a, b, start[None])
        four: Any = side(a, b, end[None])
        crossing: Any = (one * two < 0.0) & (three * four < 0.0)
        # Segments that share a point meet there; they do not cross.
        joined: Any = (pairs[own][:, None, :, None] == pairs[None, :, None, :]).any(axis=(2, 3))
        crossing &= ~joined
        if not crossing.any():
            continue
        along: Any = one / np.where(one - two == 0.0, 1.0, one - two)
        other: Any = three / np.where(three - four == 0.0, 1.0, three - four)
        here: Any = low[own][:, None] + along * (high[own] - low[own])[:, None]
        there: Any = low[None, :] + other * (high - low)[None, :]
        found += int((crossing & (np.abs(here - there) > 3.0 * reach)).sum())
    # Every crossing was seen from both of its segments.
    return found // 2


def _frame(network: _Network, extent: float) -> tuple[Any, float]:
    """The plane over which the lines are most nearly a height field, and widest; and how much they fold over it.

    The lines fold where they lie over each other, or pass over each other, at different heights. A cage of
    lines around a shape does so from every side.
    """
    centred: Any = network.points - network.points.mean(axis=0)
    reach: float = extent / 40.0
    best: Any = None
    best_folds: float = 1.0
    best_score: tuple[float, float] = (math.inf, 0.0)
    frame: Any
    for frame in _frames(network.points):
        local: Any = centred @ frame.T
        folds: float = _folds(local[:, :2], local[:, 2], network.tangent @ frame[2], network.line, network.edges, reach)
        if folds <= FOLD_LIMIT:
            # One or two overpasses can be lines that just miss each other; more make a fold.
            folds = max(folds, min(1.0, 0.06 * _overpasses(local[:, :2], local[:, 2], network.edges, reach)))
        spread: Any = local[:, :2].std(axis=0)
        # Folds in steps of 5%, so a few steep spots do not outweigh a much wider view.
        score: tuple[float, float] = (round(folds * 20.0), -float(spread[0] * spread[1]))
        if score < best_score:
            best, best_folds, best_score = frame, folds, score
    return best, best_folds


def _solve(system: Any, right: Any) -> Any:
    """Solve a square system; one that is singular (points all in a line or a plane) gets its smallest solution."""
    try:
        solved: Any = np.linalg.solve(system, right)
        if np.isfinite(solved).all() and float(np.abs(system @ solved - right).max()) <= 1e-6 * (
            1.0 + float(np.abs(right).max())
        ):
            return solved
    except np.linalg.LinAlgError:
        pass
    return np.linalg.lstsq(system, right, rcond=None)[0]


def _kernel(distances: Any) -> Any:
    """The thin-plate spline's r^2 log r."""
    return np.where(distances > 0.0, distances**2 * np.log(np.maximum(distances, 1e-300)), 0.0)


@dataclass
class _Spline:
    centres: Any
    weights: Any
    offset: Any
    scale: float


def _spline(centres: Any, values: Any) -> _Spline:
    """A thin-plate spline through values at points (in a plane, or directions on a sphere)."""
    offset: Any = centres.mean(axis=0)
    scale: float = max(float(np.abs(centres - offset).max()), 1e-12)
    local: Any = (centres - offset) / scale
    # Points that share a place are averaged; the solve needs distinct centres.
    cells: Any = np.round(local / 0.004).astype(np.int64)
    inverse: Any
    counts: Any
    _unique, inverse, counts = np.unique(cells, axis=0, return_inverse=True, return_counts=True)
    inverse = np.asarray(inverse).reshape(-1)
    merged: Any = np.zeros((len(counts), local.shape[1]))
    np.add.at(merged, inverse, local)
    merged /= counts[:, None]
    targets: Any = np.zeros(len(counts))
    np.add.at(targets, inverse, values)
    targets /= counts
    if len(merged) > SPLINE_LIMIT:
        chosen: Any = np.linspace(0, len(merged) - 1, SPLINE_LIMIT).astype(np.int64)
        merged, targets = merged[chosen], targets[chosen]
    size: int = len(merged)
    terms: int = merged.shape[1] + 1
    system: Any = np.zeros((size + terms, size + terms))
    system[:size, :size] = _kernel(np.linalg.norm(merged[:, None, :] - merged[None, :, :], axis=2))
    system[:size, :size] += SMOOTHING * np.eye(size)
    system[:size, size] = system[size, :size] = 1.0
    system[:size, size + 1 :] = merged
    system[size + 1 :, :size] = merged.T
    right: Any = np.concatenate((targets, np.zeros(terms)))
    weights: Any = _solve(system, right)
    return _Spline(merged, weights, offset, scale)


def _spline_values(spline: _Spline, queries: Any) -> Any:
    local: Any = (queries - spline.offset) / spline.scale
    size: int = len(spline.centres)
    values: Any = np.zeros(len(local))
    start: int
    for start in range(0, len(local), 512):
        part: Any = local[start : start + 512]
        basis: Any = _kernel(np.linalg.norm(part[:, None, :] - spline.centres[None, :, :], axis=2))
        values[start : start + 512] = (
            basis @ spline.weights[:size] + spline.weights[size] + part @ spline.weights[size + 1 :]
        )
    return values


def _positions(result: Triangulation, inputs: Any, flat: Any, edges: list[Edge]) -> Any:
    """Where each triangulated point is in space: its input point, or along the lines that cross there."""
    count: int = len(result.points)
    positions: Any = np.zeros((count, inputs.shape[1]))
    known: Any = np.zeros(count, dtype=bool)
    index: int
    origins: list[int]
    for index, origins in enumerate(result.sources):
        if origins:
            positions[index] = inputs[origins].mean(axis=0)
            known[index] = True
    sums: Any = np.zeros_like(positions)
    shares: Any = np.zeros(count)
    edge: Edge
    for edge, origins in result.constraints.items():
        vertex: int
        for vertex in edge:
            if known[vertex]:
                continue
            origin: int
            for origin in origins:
                start: int
                end: int
                start, end = edges[origin]
                span: Any = flat[end] - flat[start]
                t: float = float(np.dot(result.points[vertex] - flat[start], span) / max(float(span @ span), 1e-300))
                t = min(1.0, max(0.0, t))
                sums[vertex] += inputs[start] * (1.0 - t) + inputs[end] * t
                shares[vertex] += 1.0
    crossing: Any = ~known & (shares > 0.0)
    positions[crossing] = sums[crossing] / shares[crossing][:, None]
    known |= crossing
    # A point the triangulation moved or merged away from every input takes its nearest neighbour's place.
    for index in np.nonzero(~known)[0]:
        if known.any():
            placed: Any = np.nonzero(known)[0]
            nearest: int = int(placed[np.argmin(np.linalg.norm(result.points[placed] - result.points[index], axis=1))])
            positions[index] = positions[nearest]
    return positions


def _owners(result: Triangulation, network: _Network) -> list[frozenset[int]]:
    """The lines each triangulated point lies on."""
    owners: list[set[int]] = [set() for _point in result.points]
    index: int
    origins: list[int]
    for index, origins in enumerate(result.sources):
        # Points added between the lines come after the lines' own and lie on none.
        owners[index].update(int(network.line[origin]) for origin in origins if origin < len(network.line))
    edge: Edge
    for edge, origins in result.constraints.items():
        lines: set[int] = {network.edge_line[origin] for origin in origins} - {-1}
        owners[edge[0]].update(lines)
        owners[edge[1]].update(lines)
    return [frozenset(lines) for lines in owners]


def _sides(triangle: Triangle) -> tuple[Edge, Edge, Edge]:
    return _edge(triangle[0], triangle[1]), _edge(triangle[1], triangle[2]), _edge(triangle[2], triangle[0])


def _neighbours(triangles: list[Triangle]) -> dict[Edge, list[int]]:
    """The triangles on each edge."""
    result: dict[Edge, list[int]] = {}
    index: int
    triangle: Triangle
    for index, triangle in enumerate(triangles):
        side: Edge
        for side in _sides(triangle):
            result.setdefault(side, []).append(index)
    return result


def _region(result: Triangulation, owners: list[frozenset[int]], limit: float) -> list[bool]:
    """Which triangles are inside the outermost lines.

    Working inward from the hull, a triangle is dropped when it only fills a hollow of a single line, or when it
    was reached through an opening wider than `limit`. Lines are never crossed.
    """
    sides: dict[Edge, list[int]] = _neighbours(result.triangles)
    kept: list[bool] = [True] * len(result.triangles)
    pending: list[tuple[Edge, int]] = []
    side: Edge
    members: list[int]
    for side, members in sides.items():
        if len(members) == 1 and side not in result.constraints:
            pending.append((side, members[0]))
    while pending:
        index: int
        side, index = pending.pop()
        if not kept[index]:
            continue
        triangle: Triangle = result.triangles[index]
        hollow: bool = bool(owners[triangle[0]] & owners[triangle[1]] & owners[triangle[2]])
        width: float = float(np.linalg.norm(result.points[side[0]] - result.points[side[1]]))
        if not hollow and width <= limit:
            continue
        kept[index] = False
        other: Edge
        for other in _sides(triangle):
            if other == side or other in result.constraints:
                continue
            member: int
            for member in sides[other]:
                if member != index and kept[member]:
                    pending.append((other, member))
    return kept


def _bridge(
    points: Any, triangles: list[Triangle], owners: list[frozenset[int]], constraints: dict[Edge, list[int]]
) -> list[Triangle]:
    """Straight spans run from line to line where they can.

    A triangle between three points of one line lies flat along that line, like the corner of a tent's floor. When
    the triangle next to it reaches another line, the edge between them is turned the other way, so that both
    reach it and the corner rises to the ridge.
    """
    result: list[Triangle] = list(triangles)
    _round: int
    for _round in range(8):
        sides: dict[Edge, list[int]] = _neighbours(result)
        turned: set[int] = set()
        index: int
        for index in range(len(result)):
            triangle: Triangle = result[index]
            shared: frozenset[int] = owners[triangle[0]] & owners[triangle[1]] & owners[triangle[2]]
            if index in turned or not shared:
                continue
            corner: int
            for corner in range(3):
                a: int = triangle[corner]
                b: int = triangle[(corner + 1) % 3]
                c: int = triangle[(corner + 2) % 3]
                beyond: list[int] = [member for member in sides[_edge(a, b)] if member != index]
                if _edge(a, b) in constraints or len(beyond) != 1 or beyond[0] in turned:
                    continue
                across: list[int] = [vertex for vertex in result[beyond[0]] if vertex not in (a, b)]
                if len(across) != 1 or owners[across[0]] & shared:
                    continue
                # The four points must make a convex shape, or the new edge would leave it.
                tip: Any = points[across[0]] - points[c]
                one: Any = points[a] - points[c]
                two: Any = points[b] - points[c]
                left: float = float(tip[0] * one[1] - tip[1] * one[0])
                right: float = float(tip[0] * two[1] - tip[1] * two[0])
                if left * right >= 0.0 or min(abs(left), abs(right)) < 1e-12:
                    continue
                result[index] = (a, across[0], c)
                result[beyond[0]] = (across[0], b, c)
                turned.update((index, beyond[0]))
                break
        if not turned:
            break
    return result


def _inside(queries: Any, points: Any, triangles: Any) -> Any:
    """Whether each query point lies in any of the triangles."""
    a: Any = points[triangles[:, 0]][None, :, :]
    b: Any = points[triangles[:, 1]][None, :, :]
    c: Any = points[triangles[:, 2]][None, :, :]
    result: Any = np.zeros(len(queries), dtype=bool)

    def side(start: Any, end: Any, query: Any) -> Any:
        return (end[..., 0] - start[..., 0]) * (query[..., 1] - start[..., 1]) - (end[..., 1] - start[..., 1]) * (
            query[..., 0] - start[..., 0]
        )

    start: int
    for start in range(0, len(queries), 256):
        query: Any = queries[start : start + 256][:, None, :]
        first: Any = side(a, b, query)
        second: Any = side(b, c, query)
        third: Any = side(c, a, query)
        within: Any = ((first >= -1e-12) & (second >= -1e-12) & (third >= -1e-12)) | (
            (first <= 1e-12) & (second <= 1e-12) & (third <= 1e-12)
        )
        result[start : start + 256] = within.any(axis=1)
    return result


def _rim(triangles: list[Triangle]) -> list[Edge]:
    """The outline of counter-clockwise triangles as directed edges, which keep the triangles on their left."""
    directed: set[Edge] = set()
    triangle: Triangle
    for triangle in triangles:
        directed.update(((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])))
    return [side for side in directed if (side[1], side[0]) not in directed]


def _enclosed(triangles: list[Triangle], outline: set[Edge]) -> list[bool]:
    """Which counter-clockwise triangles an outline of directed edges surrounds.

    The triangles on the left of an outline edge are inside, and so is everything reached from them without
    crossing the outline. This needs no arithmetic, so points in a straight row cannot mislead it.
    """
    walls: set[Edge] = {_edge(side[0], side[1]) for side in outline}
    sides: dict[Edge, list[int]] = _neighbours(triangles)
    inside: list[bool] = [False] * len(triangles)
    pending: list[int] = []
    index: int
    triangle: Triangle
    for index, triangle in enumerate(triangles):
        runs: tuple[Edge, Edge, Edge] = (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        )
        if any(run in outline for run in runs):
            pending.append(index)
    while pending:
        index = pending.pop()
        if inside[index]:
            continue
        inside[index] = True
        side: Edge
        for side in _sides(triangles[index]):
            if side not in walls:
                pending.extend(member for member in sides[side] if not inside[member])
    return inside


def _boundary(triangles: list[Triangle]) -> list[Edge]:
    """Edges with a triangle on one side only."""
    return [side for side, members in _neighbours(triangles).items() if len(members) == 1]


@dataclass
class _Sheet:
    """A patch before it becomes a mesh.

    `local` holds each point as (right, up, height) over the patch's plane, whose axes are `frame`'s rows and whose
    origin is `origin`. Triangles are counter-clockwise seen from above the plane.
    """

    local: Any
    triangles: list[Triangle]
    frame: Any
    origin: Any

    @property
    def points(self) -> Any:
        return self.origin + self.local @ self.frame


def _tidy(points: Any, triangles: list[Triangle]) -> list[Triangle]:
    """Drop stray slivers: parts of a patch under half a percent of its area, as where two lines cross twice."""
    sides: dict[Edge, list[int]] = _neighbours(triangles)
    part: list[int] = [-1] * len(triangles)
    areas: list[float] = []
    index: int
    for index in range(len(triangles)):
        if part[index] >= 0:
            continue
        total: float = 0.0
        pending: list[int] = [index]
        part[index] = len(areas)
        while pending:
            member: int = pending.pop()
            a: Any = points[triangles[member][0]]
            b: Any = points[triangles[member][1]]
            c: Any = points[triangles[member][2]]
            total += 0.5 * abs(float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])))
            side: Edge
            for side in _sides(triangles[member]):
                other: int
                for other in sides[side]:
                    if part[other] < 0:
                        part[other] = len(areas)
                        pending.append(other)
        areas.append(total)
    least: float = 0.005 * sum(areas)
    return [triangle for triangle, group in zip(triangles, part) if areas[group] >= least]


def _root(groups: dict[int, int], item: int) -> int:
    """The group an item belongs to, in a table where each item points toward its group's first member."""
    while groups[item] != item:
        item = groups[item]
    return item


def _apart(points: Any, triangles: list[Triangle]) -> tuple[Any, list[Triangle]]:
    """Where two parts of a patch touch at one point only, each gets its own copy of the point.

    Walls and a base can then be built around each part without sharing an edge.
    """
    around: dict[int, list[int]] = {}
    index: int
    triangle: Triangle
    for index, triangle in enumerate(triangles):
        vertex: int
        for vertex in triangle:
            around.setdefault(vertex, []).append(index)
    result: list[Triangle] = list(triangles)
    copies: list[int] = []
    members: list[int]
    for vertex, members in around.items():
        # Triangles around the point that share an edge at it belong to one fan.
        shared: dict[int, list[int]] = {}
        for index in members:
            other: int
            for other in triangles[index]:
                if other != vertex:
                    shared.setdefault(other, []).append(index)
        fan: dict[int, int] = {index: index for index in members}
        joined: list[int]
        for joined in shared.values():
            for index in joined[1:]:
                fan[_root(fan, index)] = _root(fan, joined[0])
        first: int = _root(fan, members[0])
        renamed: dict[int, int] = {}
        for index in members:
            group: int = _root(fan, index)
            if group == first:
                continue
            if group not in renamed:
                renamed[group] = len(points) + len(copies)
                copies.append(vertex)
            current: Triangle = result[index]
            result[index] = (
                renamed[group] if current[0] == vertex else current[0],
                renamed[group] if current[1] == vertex else current[1],
                renamed[group] if current[2] == vertex else current[2],
            )
    if not copies:
        return points, result
    return np.concatenate((points, points[copies])), result


def _compact(points: Any, faces: list[Any]) -> tuple[Any, list[Any]]:
    """Drop points no face uses, and renumber the faces."""
    used: Any = np.unique(np.array([index for face in faces for index in face], dtype=np.int64))
    renumber: Any = np.full(len(points), -1)
    renumber[used] = np.arange(len(used))
    return points[used], [tuple(int(renumber[index]) for index in face) for face in faces]


def _sheet(network: _Network, frame: Any, spacing: float, extent: float, curved: bool, engine: Triangulate) -> _Sheet:
    """The patch over a plane: triangles inside the outermost lines, at the lines' heights."""
    origin: Any = network.points.mean(axis=0)
    local: Any = (network.points - origin) @ frame.T
    # The triangulation works in units of the sketch's size.
    flat: Any = local[:, :2] / extent
    first: Triangulation = engine(flat, network.edges)
    owners: list[frozenset[int]] = _owners(first, network)
    placed: Any = _positions(first, local, flat, network.edges)
    # Lines that share a point are joined; the others are as far apart as their nearest points.
    count: int = int(network.line.max()) + 1
    gaps: Any = _pair_gaps([flat[network.line == index] for index in range(count)])
    lines_here: frozenset[int]
    for lines_here in owners:
        a: int
        b: int
        for a in lines_here:
            for b in lines_here:
                gaps[a, b] = 0.0
    index: int
    edge: Edge
    for index, edge in enumerate(network.edges):
        if network.edge_line[index] < 0:
            a, b = int(network.line[edge[0]]), int(network.line[edge[1]])
            gaps[a, b] = gaps[b, a] = 0.0
    limit: float = 0.0
    if count > 1:
        np.fill_diagonal(gaps, np.inf)
        limit = 1.5 * float(gaps.min(axis=1).max())
    kept: list[bool] = _region(first, owners, limit)
    if not any(kept):
        # Lines that enclose nothing still span their hull.
        kept = _region(first, owners, math.inf)
    triangles: list[Triangle] = _tidy(first.points, [t for t, keep in zip(first.triangles, kept) if keep])
    if not triangles:
        raise SketchError("These lines do not span a surface. Draw a closed outline, or lines side by side.")
    if not curved:
        points: Any
        points, triangles = _apart(placed, _bridge(first.points, triangles, owners, first.constraints))
        points, triangles = _compact(points, triangles)
        return _Sheet(points, triangles, frame, origin)
    # Curved: long outline edges that are not lines are divided, and a grid of points fills the inside.
    step: float = spacing / extent
    region: Any = np.array(triangles, dtype=np.int64)
    inputs: list[Any] = [first.points]
    total: int = len(first.points)
    constraints: list[Edge] = [side for side in first.constraints]
    outline: list[Edge] = []
    for edge in _rim(triangles):
        if _edge(edge[0], edge[1]) in first.constraints:
            outline.append(edge)
            continue
        pieces: int = max(1, round(float(np.linalg.norm(first.points[edge[0]] - first.points[edge[1]])) / step))
        shares: Any = (np.arange(pieces - 1) + 1.0)[:, None] / pieces
        inputs.append(first.points[edge[0]] + (first.points[edge[1]] - first.points[edge[0]]) * shares)
        chain: list[int] = [edge[0], *range(total, total + pieces - 1), edge[1]]
        constraints.extend(zip(chain[:-1], chain[1:]))
        outline.extend(zip(chain[:-1], chain[1:]))
        total += pieces - 1
    fixed: Any = np.concatenate(inputs)
    low: Any = first.points[np.unique(region)].min(axis=0)
    high: Any = first.points[np.unique(region)].max(axis=0)
    across: Any = np.arange(float(low[0]) + 0.5 * step, float(high[0]), step)
    up: Any = np.arange(float(low[1]) + 0.5 * step, float(high[1]), step)
    grid: Any = np.stack(np.meshgrid(across, up), axis=-1).reshape(-1, 2)
    if len(grid):
        grid = grid[_inside(grid, first.points, region)]
    if len(grid):
        clear: Any = np.ones(len(grid), dtype=bool)
        start: int
        for start in range(0, len(grid), 512):
            part: Any = grid[start : start + 512]
            clear[start : start + 512] = (
                np.linalg.norm(part[:, None, :] - fixed[None, :, :], axis=2).min(axis=1) > 0.6 * step
            )
        grid = grid[clear]
    second: Triangulation = engine(np.concatenate((fixed, grid)), constraints)
    # The same outline, in the second triangulation's numbering, surrounds the patch.
    renumber: dict[int, int] = _outputs(second)
    surrounding: set[Edge] = {
        (renumber[a], renumber[b]) for a, b in outline if a in renumber and b in renumber and renumber[a] != renumber[b]
    }
    inside: list[bool] = _enclosed(second.triangles, surrounding)
    triangles = [triangle for triangle, keep in zip(second.triangles, inside) if keep]
    if not triangles:
        raise SketchError("These lines do not span a surface. Draw a closed outline, or lines side by side.")
    final: Any = np.zeros((len(second.points), 3))
    final[:, :2] = second.points * extent
    final[:, 2] = _spline_values(_spline(first.points, placed[:, 2]), second.points)
    origins: list[int]
    for index, origins in enumerate(second.sources):
        drawn: list[int] = [origin for origin in origins if origin < len(first.points)]
        if drawn:
            # A point of the lines keeps its own place.
            final[index] = placed[drawn[0]]
    final, triangles = _apart(final, triangles)
    final, triangles = _compact(final, triangles)
    return _Sheet(final, triangles, frame, origin)


def _outputs(result: Triangulation) -> dict[int, int]:
    """The output point that stands for each input point."""
    outputs: dict[int, int] = {}
    index: int
    origins: list[int]
    for index, origins in enumerate(result.sources):
        origin: int
        for origin in origins:
            outputs[origin] = index
    return outputs


def _bulge(flat: Any, triangles: list[Triangle]) -> tuple[Any, Any]:
    """How far each point of a flat patch rises when it is inflated, and which points are inside its outline.

    The rise is zero on the outline and round across: twice the square root of the solution of a Poisson equation
    on the patch, which makes a disc into a sphere and a strip into a rod.
    """
    count: int = len(flat)
    corners: Any = np.array(triangles, dtype=np.int64)
    stiffness: Any = np.zeros((count, count))
    load: Any = np.zeros(count)
    a: Any = flat[corners[:, 0]]
    b: Any = flat[corners[:, 1]]
    c: Any = flat[corners[:, 2]]
    areas: Any = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    corner: int
    for corner in range(3):
        at: Any = corners[:, corner]
        left: Any = corners[:, (corner + 1) % 3]
        right: Any = corners[:, (corner + 2) % 3]
        one: Any = flat[left] - flat[at]
        two: Any = flat[right] - flat[at]
        # The edge opposite this corner is weighted by the corner's cotangent.
        weight: Any = np.maximum(0.0, 0.5 * (one * two).sum(axis=1) / np.maximum(2.0 * areas, 1e-300))
        np.add.at(stiffness, (left, right), -weight)
        np.add.at(stiffness, (right, left), -weight)
        np.add.at(stiffness, (left, left), weight)
        np.add.at(stiffness, (right, right), weight)
        np.add.at(load, at, areas / 3.0)
    rim: Any = np.unique(np.array(_boundary(triangles), dtype=np.int64))
    free: Any = np.setdiff1d(np.arange(count), rim)
    rise: Any = np.zeros(count)
    if len(free):
        solved: Any = _solve(stiffness[np.ix_(free, free)], load[free])
        # Never quite zero inside, so the two sides stay apart.
        rise[free] = 2.0 * np.sqrt(np.maximum(solved, 1e-12 * float(areas.sum())))
    return rise, free


def _roomy(local: Any, triangles: list[Triangle]) -> tuple[Any, list[Triangle]]:
    """A patch ready to inflate: every triangle has a point inside the outline, which can rise.

    An inner edge between two outline points gets a point at its middle, and a triangle left with only outline
    points is dropped. Otherwise the two sides of the inflated patch would touch there.
    """
    rim: set[int] = {vertex for side in _boundary(triangles) for vertex in side}
    kept: list[Triangle] = list(triangles)
    points: list[Any] = [local]
    count: int = len(local)
    while True:
        divided: set[int] = set()
        side: Edge
        members: list[int]
        for side, members in _neighbours(kept).items():
            if len(members) != 2 or side[0] not in rim or side[1] not in rim:
                continue
            if members[0] in divided or members[1] in divided:
                # A triangle is divided along one edge at a time; the next round takes its other edges.
                continue
            points.append(0.5 * (local[side[0]] + local[side[1]])[None, :])
            member: int
            for member in members:
                triangle: Triangle = kept[member]
                # Turn the triangle so that the edge being divided comes first.
                while _edge(triangle[0], triangle[1]) != side:
                    triangle = (triangle[1], triangle[2], triangle[0])
                kept[member] = (triangle[0], count, triangle[2])
                kept.append((count, triangle[1], triangle[2]))
                divided.update((member, len(kept) - 1))
            count += 1
        if not divided:
            break
    kept = [t for t in kept if not (t[0] in rim and t[1] in rim and t[2] in rim)]
    return np.concatenate(points), kept


def _inflate(sheet: _Sheet) -> Surface:
    """A closed surface from a flat sketch: its patch, bulged on both sides and joined along the outline."""
    local: Any
    triangles: list[Triangle]
    local, triangles = _roomy(sheet.local, sheet.triangles)
    if not triangles:
        return Surface(sheet.points, list(sheet.triangles), "patch", True)
    local, triangles = _compact(local, triangles)
    rise: Any
    inner: Any
    rise, inner = _bulge(local[:, :2], triangles)
    count: int = len(local)
    below: Any = np.arange(count)
    below[inner] = count + np.arange(len(inner))
    upper: Any = local.copy()
    upper[:, 2] += rise
    lower: Any = local[inner].copy()
    lower[:, 2] -= rise[inner]
    faces: list[Face] = list(triangles)
    triangle: Triangle
    for triangle in triangles:
        faces.append((int(below[triangle[0]]), int(below[triangle[2]]), int(below[triangle[1]])))
    return Surface(sheet.origin + np.concatenate((upper, lower)) @ sheet.frame, faces, "inflate", True)


def _solid(sheet: _Sheet, extent: float, curved: bool, engine: Triangulate) -> Surface:
    """A closed solid under a patch.

    An outline that lies in one plane, as a dome's does, is closed by that plane. Any other outline gets walls
    down to a flat base at the patch's lowest point, or up to one at its highest when the patch hangs below its
    outline.
    """
    local: Any = sheet.local
    count: int = len(local)
    on_rim: Any = np.zeros(count, dtype=bool)
    on_rim[np.unique(np.array(_boundary(sheet.triangles), dtype=np.int64))] = True
    # The plane through the outline, as a height over the patch's own plane.
    design: Any = np.column_stack((local[:, :2], np.ones(count)))
    fit: Any = np.linalg.lstsq(design[on_rim], local[on_rim, 2], rcond=None)[0]
    slack: float = float(np.abs(design[on_rim] @ fit - local[on_rim, 2]).max())
    planar: bool = slack <= 0.004 * extent
    if planar:
        base: Any = design @ fit
        grounded: Any = np.abs(local[:, 2] - base) <= max(slack, 1e-6 * extent)
    else:
        inner: float = float(local[~on_rim, 2].mean()) if (~on_rim).any() else float(local[:, 2].mean())
        upward: bool = inner >= float(local[on_rim, 2].mean())
        base = np.full(count, float(local[:, 2].min()) if upward else float(local[:, 2].max()))
        grounded = np.abs(local[:, 2] - base) <= 1e-6 * extent
    # Parts of the patch that lie in the base have no thickness and are left out.
    triangles: list[Triangle] = [
        t for t in sheet.triangles if not (grounded[t[0]] and grounded[t[1]] and grounded[t[2]])
    ]
    if not triangles:
        return Surface(sheet.points, list(sheet.triangles), "patch", curved)
    outline: list[Edge] = _rim(triangles)
    # Each outline point's partner in the base; a point already in the base is its own.
    partner: Any = np.arange(count)
    members: list[int] = sorted({vertex for edge in outline for vertex in edge})
    added: list[tuple[float, float, float]] = []
    vertex: int
    for vertex in members:
        if not grounded[vertex]:
            partner[vertex] = count + len(added)
            added.append((float(local[vertex, 0]), float(local[vertex, 1]), float(base[vertex])))
    points: Any = np.concatenate((local, np.array(added, dtype=np.float64).reshape(-1, 3)))
    faces: list[Face] = list(triangles)
    # Walls: each outline edge, against the direction its triangle runs it, then down to the base and back.
    a: int
    b: int
    for a, b in outline:
        wall: list[int] = [b, a]
        if partner[a] != a:
            wall.append(int(partner[a]))
        if partner[b] != b:
            wall.append(int(partner[b]))
        if len(wall) >= 3:
            faces.append(tuple(wall))
    # The base: the outline's own points, triangulated inside it, facing the other way.
    place: dict[int, int] = {member: index for index, member in enumerate(members)}
    floor: Triangulation = engine(local[members, :2] / extent, [(place[a], place[b]) for a, b in outline])
    renumber: dict[int, int] = _outputs(floor)
    surrounding: set[Edge] = {(renumber[place[a]], renumber[place[b]]) for a, b in outline}
    keep: bool
    triangle: Triangle
    for triangle, keep in zip(floor.triangles, _enclosed(floor.triangles, surrounding)):
        corners: list[int] = [int(partner[members[floor.sources[v][0]]]) for v in triangle if floor.sources[v]]
        if keep and len(corners) == 3:
            faces.append((corners[0], corners[2], corners[1]))
    world: Any
    world, faces = _compact(sheet.origin + points @ sheet.frame, faces)
    if _volume(world, faces) < 0.0:
        faces = [tuple(reversed(face)) for face in faces]
    return Surface(world, faces, "solid", curved)


def _sphere(count: int) -> Any:
    """Evenly spread directions (a Fibonacci spiral)."""
    index: Any = np.arange(count) + 0.5
    height: Any = 1.0 - 2.0 * index / count
    turn: Any = index * (math.pi * (3.0 - math.sqrt(5.0)))
    ring: Any = np.sqrt(np.maximum(0.0, 1.0 - height**2))
    return np.stack((ring * np.cos(turn), ring * np.sin(turn), height), axis=1)


def _stereographic(directions: Any, pole: Any, sign: float) -> Any:
    """Directions flattened onto a plane, seen from `pole` (sign 1) or from the direction opposite it (-1)."""
    helper: Any = np.array([0.0, 0.0, 1.0]) if abs(float(pole[2])) < 0.9 else np.array([1.0, 0.0, 0.0])
    right: Any = np.cross(helper, pole)
    right = right / float(np.linalg.norm(right))
    up: Any = np.cross(pole, right)
    depth: Any = np.maximum(1.0 - sign * (directions @ pole), 1e-9)
    return np.stack((directions @ right, directions @ up), axis=1) / depth[:, None]


def _shell(
    network: _Network, spacing: float, extent: float, closed: bool, curved: bool, engine: Triangulate
) -> Surface:
    """A surface around the lines: their distance from the centre, carried to every direction.

    The sphere of directions is unrolled from its emptiest direction and triangulated flat. A closed shell fills
    the hole left there; an open one instead leaves open the widest opening the lines surround.
    """
    centre: Any = network.points.mean(axis=0)
    centred: Any = network.points - centre
    axes: Any = np.linalg.svd(centred, full_matrices=False)[2]
    scales: Any = np.maximum((centred @ axes.T).std(axis=0), 1e-12)
    # Stretch the cloud to a ball, so long shapes are seen evenly from the centre; never by more than four.
    scales[1] = max(float(scales[1]), 0.25 * float(scales[0]))
    scales[2] = max(float(scales[2]), 0.25 * float(scales[1]))
    balled: Any = (centred @ axes.T) / scales
    reach: Any = np.linalg.norm(balled, axis=1)
    if float(reach.min()) < 0.05 * float(np.median(reach)):
        raise SketchError("A line runs through the middle of this shape. Draw the lines around its middle instead.")
    directions: Any = balled / reach[:, None]
    candidates: Any = _sphere(600)
    emptiness: Any = np.zeros(len(candidates))
    start: int
    for start in range(0, len(candidates), 128):
        part: Any = candidates[start : start + 128]
        emptiness[start : start + 128] = (part @ directions.T).max(axis=1)
    pole: Any = candidates[int(np.argmin(emptiness))]
    added: Any = np.zeros((0, 3))
    if curved:
        angle: float = spacing / max(extent, 1e-12) * 3.5
        added = _sphere(int(min(1500, max(60, round(4.0 * math.pi / angle**2)))))
        clear: Any = np.ones(len(added), dtype=bool)
        for start in range(0, len(added), 256):
            part = added[start : start + 256]
            clear[start : start + 256] = np.arccos(np.clip((part @ directions.T).max(axis=1), -1.0, 1.0)) > 0.6 * angle
        added = added[clear & (np.arccos(np.clip(added @ pole, -1.0, 1.0)) > 0.8 * angle)]
    everything: Any = np.concatenate((directions, added))
    flat: Any = _stereographic(everything, pole, 1.0)
    flat = flat / max(float(np.abs(flat).max()), 1e-12)
    result: Triangulation = engine(flat, network.edges)
    radii: Any = np.concatenate((reach, np.ones(len(added))))
    if len(added):
        radii[len(reach) :] = np.clip(
            _spline_values(_spline(directions, reach), added), 0.3 * float(reach.min()), 2.0 * float(reach.max())
        )
    placed: Any = _positions(result, everything * radii[:, None], flat, network.edges)
    triangles: list[Triangle] = list(result.triangles)
    outline: list[Edge] = _rim(triangles)
    if not closed:
        # Everything reached from the unrolled sphere's edge without crossing a line is the opening.
        owners: list[frozenset[int]] = _owners(result, network)
        kept: list[bool] = _region(result, owners, 0.0)
        if not any(kept):
            # A cage with gaps surrounds no opening: only the hole at the pole stays open.
            kept = _region(result, owners, math.inf)
        triangles = [triangle for triangle, keep in zip(triangles, kept) if keep]
    elif len(outline) >= 3:
        # The hole around the pole is filled between the points on its rim. Seen from the other side the rim
        # runs the same way round, with the hole on its left.
        members: list[int] = sorted({vertex for edge in outline for vertex in edge})
        heading: Any = placed[members] / np.maximum(np.linalg.norm(placed[members], axis=1), 1e-300)[:, None]
        seen: Any = _stereographic(heading, pole, -1.0)
        place: dict[int, int] = {member: index for index, member in enumerate(members)}
        cap: Triangulation = engine(
            seen / max(float(np.abs(seen).max()), 1e-12), [(place[a], place[b]) for a, b in outline]
        )
        renumber: dict[int, int] = _outputs(cap)
        surrounding: set[Edge] = {(renumber[place[a]], renumber[place[b]]) for a, b in outline}
        triangle: Triangle
        keep: bool
        for triangle, keep in zip(cap.triangles, _enclosed(cap.triangles, surrounding)):
            corners: list[int] = [members[cap.sources[v][0]] for v in triangle if cap.sources[v]]
            if keep and len(corners) == 3:
                triangles.append((corners[0], corners[2], corners[1]))
    if not triangles:
        raise SketchError("These lines fold over each other without surrounding a shape. Choose Closed for a solid.")
    # The triangles all run the same way round; make that counter-clockwise seen from outside.
    corners_of: Any = placed[np.array(triangles, dtype=np.int64)]
    outward: float = float((corners_of[:, 0] * np.cross(corners_of[:, 1], corners_of[:, 2])).sum())
    faces: list[Face] = [
        triangle if outward >= 0.0 else (triangle[0], triangle[2], triangle[1]) for triangle in triangles
    ]
    points: Any
    points, faces = _compact(centre + (placed * scales) @ axes, faces)
    return Surface(points, faces, "shell", curved)


def build(
    paths: list[Any],
    radius: float,
    closed: bool = False,
    curved: bool = True,
    detail: int = DETAIL,
    triangulate: Triangulate | None = None,
) -> Surface:
    """A mesh through drawn lines.

    Args:
        paths: Each line's points in space, in drawing order.
        radius: The lines' brush radius; ends this close to another line are joined to it.
        closed: A watertight solid, instead of a sheet that ends at the outermost lines.
        curved: Bend smoothly through the lines, instead of joining them with flat faces.
        detail: About how many faces span the sketch.
        triangulate: Triangulates points in a plane; Blender's own by default.
    """
    drawn: list[Line] = prepare(paths, radius)
    if not drawn:
        raise SketchError("Draw the lines of the surface first.")
    cloud: Any = np.concatenate([line.points for line in drawn])
    extent: float = float((cloud.max(axis=0) - cloud.min(axis=0)).max())
    if extent <= 1e-9:
        raise SketchError("The lines are too small to make a surface.")
    spacing: float = extent / min(DETAIL_LIMITS[1], max(DETAIL_LIMITS[0], int(detail)))
    lines: list[Line] = [_resample(line, spacing) for line in drawn]
    total: int = sum(len(line.points) for line in lines)
    if total > POINT_LIMIT:
        spacing *= total / POINT_LIMIT
        lines = [_resample(line, spacing) for line in drawn]
    cloud = np.concatenate([line.points for line in lines])
    spread: Any = np.linalg.svd(cloud - cloud.mean(axis=0), compute_uv=False)
    if float(spread[1]) <= 0.02 * float(spread[0]):
        raise SketchError("The lines all run along one straight line. Draw across it too.")
    flat: bool = float(spread[2]) <= 0.02 * float(spread[1])
    if len(lines) == 1 and not lines[0].closed:
        # A single open line is closed by a straight side, as Fill does.
        lines = [Line(lines[0].points, True)]
    tolerance: float = max(4.0 * radius, 0.015 * extent)
    gaps: Any = _pair_gaps([line.points for line in lines])
    np.fill_diagonal(gaps, np.inf)
    apart: bool = len(lines) >= 2 and float(gaps.min()) > tolerance
    alike: bool = len({line.closed for line in lines}) == 1
    if apart and alike and not flat:
        lofted: Surface | None = _loft(drawn, spacing, curved, closed)
        if lofted is not None:
            return lofted
    engine: Triangulate = triangulate if triangulate is not None else blender_triangulate
    network: _Network = _network(lines, tolerance, spacing)
    frame: Any
    folds: float
    frame, folds = _frame(network, extent)
    if not flat and folds > FOLD_LIMIT:
        return _shell(network, spacing, extent, closed, curved, engine)
    sheet: _Sheet = _sheet(network, frame, spacing, extent, curved, engine)
    if closed and flat and curved:
        return _inflate(sheet)
    if closed and not flat:
        return _solid(sheet, extent, curved, engine)
    return Surface(sheet.points, list(sheet.triangles), "patch", curved)
