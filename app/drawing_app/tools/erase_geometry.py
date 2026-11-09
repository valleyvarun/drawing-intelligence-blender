"""Erase and Trim geometry in screen space, without Blender, so it can be tested on its own.

Strokes are cut along their points' parameters: parameter i + t lies t of the way from point i to point i + 1.
A kept piece of a stroke is a span (start, end) of parameters. On a closed stroke the parameters run on past the
last point to n, which is the first point again, and a span may wrap around: its end is then above n.

Which strokes the eraser may act on is decided from stacks of layers: at each sample under the eraser, every
stroke or object there, nearest first. A stroke covered by an erasable one waits until nothing erasable covers it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, TypeAlias

# numpy ships with Blender's Python; editors without that interpreter see it as a dynamic module.
np: Any = import_module("numpy")

Vec2: TypeAlias = tuple[float, float]
Polygon: TypeAlias = list[Vec2]
Span: TypeAlias = tuple[float, float]

# The eraser's circle, as a polygon for clipping faces.
SIDES: int = 24
TINY: float = 1e-6


def circle(center: Vec2, radius: float, sides: int = SIDES) -> Polygon:
    """A regular polygon around the circle, counter-clockwise; its edges touch the circle from outside."""
    reach: float = radius / math.cos(math.pi / sides)
    corners: Polygon = []
    index: int
    for index in range(sides):
        angle: float = math.tau * index / sides
        corners.append((center[0] + reach * math.cos(angle), center[1] + reach * math.sin(angle)))
    return corners


def area(polygon: Polygon) -> float:
    """Signed area: positive when counter-clockwise."""
    total: float = 0.0
    index: int
    for index in range(len(polygon)):
        a: Vec2 = polygon[index - 1]
        b: Vec2 = polygon[index]
        total += a[0] * b[1] - b[0] * a[1]
    return total * 0.5


def _clip(polygon: Polygon, a: Vec2, b: Vec2, left: bool) -> Polygon:
    """The part of a convex polygon to the left of the line a to b, or to its right."""
    edge: Vec2 = (b[0] - a[0], b[1] - a[1])

    def side(point: Vec2) -> float:
        cross: float = edge[0] * (point[1] - a[1]) - edge[1] * (point[0] - a[0])
        return cross if left else -cross

    result: Polygon = []
    index: int
    for index in range(len(polygon)):
        previous: Vec2 = polygon[index - 1]
        current: Vec2 = polygon[index]
        before: float = side(previous)
        now: float = side(current)
        if (before < 0.0) != (now < 0.0):
            t: float = before / (before - now)
            result.append((previous[0] + (current[0] - previous[0]) * t, previous[1] + (current[1] - previous[1]) * t))
        if now >= 0.0:
            result.append(current)
    return result


def subtract(polygon: Polygon, cutter: Polygon) -> list[Polygon] | None:
    """Convex pieces of a convex polygon that lie outside a convex, counter-clockwise cutter.

    Returns None when the cutter does not overlap the polygon, so it stays as it is.
    """
    xs: list[float] = [point[0] for point in polygon]
    ys: list[float] = [point[1] for point in polygon]
    cx: list[float] = [point[0] for point in cutter]
    cy: list[float] = [point[1] for point in cutter]
    if min(xs) >= max(cx) or max(xs) <= min(cx) or min(ys) >= max(cy) or max(ys) <= min(cy):
        return None
    pieces: list[Polygon] = []
    remaining: Polygon = polygon
    index: int
    for index in range(len(cutter)):
        a: Vec2 = cutter[index]
        b: Vec2 = cutter[(index + 1) % len(cutter)]
        outside: Polygon = _clip(remaining, a, b, False)
        if len(outside) >= 3 and abs(area(outside)) > TINY:
            pieces.append(outside)
        remaining = _clip(remaining, a, b, True)
        if len(remaining) < 3 or abs(area(remaining)) <= TINY:
            # Nothing of the polygon is inside the cutter.
            return None
    return pieces


def inside(points: Any, polygon: Polygon) -> Any:
    """Which points (N, 2) lie inside a polygon, by the even-odd rule."""
    x: Any = np.asarray(points, dtype=np.float64)[:, 0]
    y: Any = np.asarray(points, dtype=np.float64)[:, 1]
    result: Any = np.zeros(len(x), dtype=bool)
    index: int
    for index in range(len(polygon)):
        a: Vec2 = polygon[index - 1]
        b: Vec2 = polygon[index]
        if a[1] == b[1]:
            continue
        crosses: Any = (a[1] > y) != (b[1] > y)
        at: Any = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
        result ^= crosses & (x < at)
    return result


def outlines(points: list[Vec2], triangles: list[tuple[int, int, int]]) -> list[Polygon]:
    """The outlines of the area some triangles cover, counter-clockwise.

    A hole joins the outline around it through a cut of no width, so each piece of area is one polygon, as a
    Grease Pencil fill is.
    """
    edges: set[tuple[int, int]] = set()
    triangle: tuple[int, int, int]
    for triangle in triangles:
        a: int
        b: int
        c: int
        a, b, c = triangle
        if area([points[a], points[b], points[c]]) < 0.0:
            b, c = c, b
        edges.update(((a, b), (b, c), (c, a)))
    following: dict[int, list[int]] = {}
    start: int
    end: int
    for start, end in edges:
        if (end, start) not in edges:
            following.setdefault(start, []).append(end)
    loops: list[Polygon] = []
    while following:
        first: int = next(iter(following))
        loop: list[int] = [first]
        current: int = first
        while True:
            choices: list[int] | None = following.get(current)
            if not choices:
                break
            step: int = choices.pop()
            if not choices:
                del following[current]
            if step == first:
                break
            loop.append(step)
            current = step
        if len(loop) >= 3:
            loops.append([points[index] for index in loop])
    outer: list[Polygon] = [loop for loop in loops if area(loop) > 0.0]
    hole: Polygon
    for hole in [loop for loop in loops if area(loop) <= 0.0]:
        owner: int
        for owner in range(len(outer)):
            if inside(np.array([hole[0]]), outer[owner])[0]:
                outer[owner] = _join(outer[owner], hole)
                break
    return outer


def _join(outer: Polygon, hole: Polygon) -> Polygon:
    """An outline with a hole joined in at their closest points."""
    a: Any = np.asarray(outer, dtype=np.float64)
    b: Any = np.asarray(hole, dtype=np.float64)
    gaps: Any = ((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)
    i: int
    j: int
    i, j = (int(value) for value in np.unravel_index(int(gaps.argmin()), gaps.shape))
    return outer[: i + 1] + hole[j:] + hole[: j + 1] + outer[i:]


def chords(start: Any, end: Any, center: Vec2, radius: float) -> tuple[Any, Any]:
    """Parameters t0 <= t1 in [0, 1] of each segment's part inside the circle; t0 > t1 where it misses."""
    p: Any = np.asarray(start, dtype=np.float64)
    d: Any = np.asarray(end, dtype=np.float64) - p
    f: Any = p - np.asarray(center, dtype=np.float64)
    a: Any = (d * d).sum(axis=1)
    b: Any = 2.0 * (f * d).sum(axis=1)
    c: Any = (f * f).sum(axis=1) - radius * radius
    flat: Any = a < 1e-18
    discriminant: Any = b * b - 4.0 * a * c
    root: Any = np.sqrt(np.maximum(discriminant, 0.0))
    safe: Any = np.where(flat, 1.0, 2.0 * a)
    t0: Any = np.where(flat, np.where(c <= 0.0, 0.0, 1.0), (-b - root) / safe)
    t1: Any = np.where(flat, np.where(c <= 0.0, 1.0, 0.0), (-b + root) / safe)
    miss: Any = ~flat & (discriminant < 0.0)
    t0 = np.where(miss, 1.0, np.maximum(t0, 0.0))
    t1 = np.where(miss, 0.0, np.minimum(t1, 1.0))
    return t0, t1


def _merge(intervals: list[Span]) -> list[Span]:
    merged: list[Span] = []
    start: float
    end: float
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + TINY:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def kept(removed: list[Span], count: int, cyclic: bool) -> list[Span]:
    """The spans left after removing parameter intervals from a stroke of `count` points."""
    total: float = float(count if cyclic else count - 1)
    cuts: list[Span] = _merge([(max(0.0, a), min(total, b)) for a, b in removed if b >= a])
    spans: list[Span] = []
    position: float = 0.0
    start: float
    end: float
    for start, end in cuts:
        if start - position > TINY:
            spans.append((position, start))
        position = max(position, end)
    if total - position > TINY:
        spans.append((position, total))
    if cyclic and len(spans) > 1 and spans[0][0] <= TINY and spans[-1][1] >= total - TINY:
        # The pieces either side of the first point are one piece of the closed stroke.
        spans = [(spans[-1][0], total + spans[0][1])] + spans[1:-1]
    return spans


def erase_spans(points: Any, center: Vec2, radius: float, erasable: Any, cyclic: bool) -> list[Span] | None:
    """The spans a stroke keeps after the circle removes what lies inside it; None when nothing is removed.

    Only segments whose two points are `erasable` (visible and in front) are cut.
    """
    screen: Any = np.asarray(points, dtype=np.float64)
    count: int = len(screen)
    if count == 0:
        return None
    allowed: Any = np.asarray(erasable, dtype=bool)
    if count == 1:
        gap: float = math.hypot(screen[0, 0] - center[0], screen[0, 1] - center[1])
        return [] if allowed[0] and gap <= radius else None
    following: Any = np.arange(1, count + 1) % count if cyclic else np.arange(1, count)
    first: Any = np.arange(count) if cyclic else np.arange(count - 1)
    t0: Any
    t1: Any
    t0, t1 = chords(screen[first], screen[following], center, radius)
    hit: Any = (t1 - t0 > TINY) & allowed[first] & allowed[following]
    if not hit.any():
        return None
    removed: list[Span] = [(float(index + t0[index]), float(index + t1[index])) for index in np.flatnonzero(hit)]
    return kept(removed, count, cyclic)


def mask_spans(keep: Any, cyclic: bool) -> list[Span] | None:
    """Spans of the runs of points a mask keeps; None when it keeps them all. A lone point is a span of one."""
    flags: Any = np.asarray(keep, dtype=bool)
    count: int = len(flags)
    if flags.all():
        return None
    runs: list[Span] = []
    index: int = 0
    while index < count:
        if flags[index]:
            first: int = index
            while index + 1 < count and flags[index + 1]:
                index += 1
            runs.append((float(first), float(index)))
        index += 1
    if cyclic and len(runs) > 1 and runs[0][0] == 0.0 and runs[-1][1] == float(count - 1):
        # The runs either side of the first point join around it.
        runs = [(runs[-1][0], runs[0][1] + count)] + runs[1:-1]
    return runs


def span_points(span: Span, count: int, cyclic: bool) -> tuple[Any, Any, Any]:
    """Where a span's points come from: point k lies t[k] of the way from point a[k] to point b[k]."""
    start: float
    end: float
    start, end = span
    parameters: list[float] = [start]
    index: int
    for index in range(math.floor(start) + 1, math.ceil(end)):
        parameters.append(float(index))
    if end - start > TINY:
        parameters.append(end)
    a: list[int] = []
    b: list[int] = []
    t: list[float] = []
    parameter: float
    for parameter in parameters:
        whole: int = math.floor(parameter + 1e-9)
        share: float = max(0.0, parameter - whole)
        if cyclic:
            a.append(whole % count)
            b.append((whole + 1) % count)
        else:
            whole = min(whole, count - 1)
            a.append(whole)
            b.append(min(whole + 1, count - 1))
            if whole == count - 1:
                share = 0.0
        t.append(share)
    return np.asarray(a, dtype=np.int64), np.asarray(b, dtype=np.int64), np.asarray(t, dtype=np.float64)


def falloff(distance: Any, radius: float, hardness: float) -> Any:
    """How strongly a soft eraser acts at each distance: fully within its hard core, fading to nothing at the edge."""
    core: float = max(0.0, min(0.999, hardness)) * radius
    x: Any = np.clip((radius - np.asarray(distance, dtype=np.float64)) / max(radius - core, 1e-9), 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def trim_spans(count: int, cyclic: bool, cuts: list[float], touches: list[float]) -> list[Span]:
    """The spans a stroke keeps after Trim removes each piece between cuts that the gesture touched.

    Pieces between cuts are where the stroke runs between the places it meets other strokes. A stroke that meets
    none is one piece, so a touch removes all of it.
    """
    total: float = float(count if cyclic else count - 1)
    inner: list[float] = sorted({cut for cut in cuts if (0.0 <= cut < total if cyclic else 0.0 < cut < total)})
    pieces: list[Span]
    if cyclic:
        if not inner:
            return []
        pieces = [(inner[index], inner[index + 1]) for index in range(len(inner) - 1)]
        pieces.append((inner[-1], inner[0] + total))
    else:
        bounds: list[float] = [0.0, *inner, total]
        pieces = [(bounds[index], bounds[index + 1]) for index in range(len(bounds) - 1)]

    def touched(piece: Span) -> bool:
        touch: float
        for touch in touches:
            if piece[0] - TINY <= touch <= piece[1] + TINY:
                return True
            if cyclic and piece[0] - TINY <= touch + total <= piece[1] + TINY:
                return True
        return False

    flags: list[bool] = [touched(piece) for piece in pieces]
    spans: list[Span] = []
    piece: Span
    flag: bool
    for piece, flag in zip(pieces, flags, strict=True):
        if flag:
            continue
        if spans and abs(spans[-1][1] - piece[0]) <= TINY:
            spans[-1] = (spans[-1][0], piece[1])
        else:
            spans.append(piece)
    if cyclic and len(spans) > 1 and not flags[-1] and not flags[0]:
        # The last piece runs on into the first.
        spans = [(spans[-1][0], spans[0][1] + total)] + spans[1:-1]
    return spans


@dataclass(frozen=True)
class Layer:
    """One stroke or object at a sample under the eraser.

    `key` names an erasable target, or another object that only hides what is behind it (`erasable` False).
    At equal depth (within `slack`), lower ranks are in front: marks drawn on a surface before the surface.
    Strokes of one Grease Pencil object share a `group`: at equal depth the later one (`order`) is on top, and
    in an object that stacks its strokes as 2D layers (`flat`) the later one is on top whatever the depth.
    """

    key: str
    depth: float
    rank: int
    slack: float
    erasable: bool = True
    group: str = ""
    order: tuple[int, int] = (0, 0)
    flat: bool = False


def ahead(a: Layer, b: Layer) -> bool:
    """Whether `a` covers `b` where both are."""
    same: bool = bool(a.group) and a.group == b.group
    if same and a.flat:
        return a.order > b.order
    slack: float = max(a.slack, b.slack)
    if a.depth < b.depth - slack:
        return True
    if b.depth < a.depth - slack:
        return False
    if same:
        return a.order > b.order
    return a.rank < b.rank


def resolve(
    stacks: list[list[Layer]], blocks: Callable[[str], bool] | None = None
) -> tuple[set[str], list[set[str]], set[str]]:
    """The targets the eraser acts on, the erasable targets in front at each sample, and the covered ones.

    A target acts when it is in front somewhere and no other erasable target covers it anywhere under the
    eraser: what is behind waits until the front is gone. With `blocks`, only targets it accepts (such as
    those the eraser can still remove part of) hold back what they cover.
    """
    visible: set[str] = set()
    covered: set[str] = set()
    fronts: list[set[str]] = []
    stack: list[Layer]
    for stack in stacks:
        front: set[str] = set()
        layer: Layer
        for layer in stack:
            blockers: list[Layer] = [other for other in stack if other.key != layer.key and ahead(other, layer)]
            if not blockers:
                if layer.erasable:
                    front.add(layer.key)
            elif layer.erasable and any(other.erasable and (blocks is None or blocks(other.key)) for other in blockers):
                covered.add(layer.key)
        fronts.append(front)
        visible |= front
    return visible - covered, fronts, covered
