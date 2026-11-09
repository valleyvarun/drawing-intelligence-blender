"""The 3D Extruder's tube: where each stretch lands, how it coils, and its mesh.

Pure numpy and Python, without Blender, so it can be tested on its own. The tube is a chain of centre-line
points `spacing` apart. Each new point rests on the highest support under it: other objects (asked through
a callback) or the tube's own earlier stretches, which it touches when their centres are a tube width apart.
The stretch just laid does not hold it up, so a tube lies flat instead of climbing onto itself.

Like toothpaste, the tube is laid at the flow's speed. When the nozzle moves as fast as that or faster, the
tube trails straight behind it. Slower, the spare length coils around the point under the nozzle, so holding
still builds a coiled pile and a slow drag leaves loops.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable
from importlib import import_module
from typing import Any

# numpy ships with Blender's Python; editors without that interpreter see it as a dynamic module.
np: Any = import_module("numpy")

Point = tuple[float, float, float]
Frame = tuple[Point, Point]
# A coil's centre-line radius when the nozzle holds still, in tube radii.
COIL: float = 1.5
# Centre-line points are this many tube radii apart, or farther when the flow would lay more than RATE_LIMIT.
SPACING: float = 0.5
RATE_LIMIT: float = 2000.0  # centre-line points per second
# The stretch just laid, this many tube radii long, never holds up the next point.
CLEAR: float = 3.0
# Points laid in one call, at most; the rest are laid by the next calls.
STEP_LIMIT: int = 600
SIDES: int = 12
CAP_RINGS: int = 4
# The hanging strand between the nozzle and the tube has at most this many centre-line points.
STRAND_POINTS: int = 24
_ANGLES: Any = np.linspace(0.0, math.tau, SIDES, endpoint=False)
_COS: Any = np.cos(_ANGLES)[:, None]
_SIN: Any = np.sin(_ANGLES)[:, None]


def _unit(x: float, y: float, z: float, fallback: Point) -> Point:
    size: float = math.sqrt(x * x + y * y + z * z)
    return (x / size, y / size, z / size) if size > 1e-12 else fallback


def _cross(a: Point, b: Point) -> Point:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _across(tangent: Point) -> Point:
    """Any unit vector perpendicular to `tangent`."""
    helper: Point = (0.0, 0.0, 1.0) if abs(tangent[2]) < 0.9 else (1.0, 0.0, 0.0)
    side: Point = _cross(helper, tangent)
    return _unit(side[0], side[1], side[2], (1.0, 0.0, 0.0))


def _transport(previous: Point | None, tangent: Point) -> Frame:
    """A centre-line point's (right, up): the previous `right` turned onto the new tangent, so rings never twist."""
    if previous is None:
        right: Point = _across(tangent)
    else:
        along: float = previous[0] * tangent[0] + previous[1] * tangent[1] + previous[2] * tangent[2]
        right = _unit(
            previous[0] - tangent[0] * along,
            previous[1] - tangent[1] * along,
            previous[2] - tangent[2] * along,
            _across(tangent),
        )
    return right, _cross(tangent, right)


def tube_line(vertices: Any) -> tuple[list[Point], float] | None:
    """A tube's centre-line points and radius, read back from its mesh (Tube.mesh); None for another mesh.

    The mesh starts with one ring of SIDES vertices per centre-line point, and ends with the two caps.
    """
    corners: Any = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    caps: int = 2 * (CAP_RINGS * SIDES + 1)
    count: int
    rest: int
    count, rest = divmod(len(corners) - caps, SIDES)
    if count < 2 or rest:
        return None
    rings: Any = corners[: count * SIDES].reshape(count, SIDES, 3)
    centres: Any = rings.mean(axis=1)
    radius: float = float(np.linalg.norm(rings - centres[:, None, :], axis=2).mean())
    return [(float(c[0]), float(c[1]), float(c[2])) for c in centres], radius


def tube_mesh(points: list[Point], radius: float) -> tuple[Any, Any, Any]:
    """The mesh of a tube along these centre-line points, as Tube.mesh makes it."""
    tube: Tube = Tube(radius)
    tube.points = list(points)
    return tube.mesh()


def spacing_for(radius: float, flow: float) -> float:
    """Centre-line spacing for a tube of `radius` coming out at `flow` per second."""
    return max(radius * SPACING, flow / RATE_LIMIT)


def coil_radius(radius: float, speed: float, flow: float) -> float:
    """Coil radius while the nozzle moves at `speed` and the tube comes out at `flow` (both per second)."""
    share: float = speed / flow if flow > 0.0 else 1.0
    return COIL * radius * max(0.0, 1.0 - share)


class Tube:
    def __init__(self, radius: float, spacing: float | None = None) -> None:
        self.radius: float = radius
        self.spacing: float = spacing if spacing is not None else radius * SPACING
        self.points: list[Point] = []
        # Tube length up to each point, and its length across the ground (without climbs and drops).
        self.arc: list[float] = []
        self.run: list[float] = []
        self.phase: float = 0.0
        self.owed: float = 0.0
        self._cell: float = 2.0 * radius
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._tops: dict[tuple[int, int], float] = {}
        # Frames and mesh rings of the points whose tangent is final (every laid point but the last).
        self._frames: list[Frame] = []
        self._rings: Any = np.zeros((0, SIDES, 3), dtype=np.float64)

    @property
    def length(self) -> float:
        return self.arc[-1] if self.arc else 0.0

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return math.floor(x / self._cell), math.floor(y / self._cell)

    def _add(self, point: Point) -> None:
        if self.points:
            last: Point = self.points[-1]
            self.arc.append(self.arc[-1] + math.dist(last, point))
            self.run.append(self.run[-1] + math.hypot(point[0] - last[0], point[1] - last[1]))
        else:
            self.arc.append(0.0)
            self.run.append(0.0)
        self.points.append(point)
        key: tuple[int, int] = self._key(point[0], point[1])
        self._cells.setdefault(key, []).append(len(self.points) - 1)
        self._tops[key] = max(self._tops.get(key, -math.inf), point[2])

    def support(self, x: float, y: float, limit: int | None = None) -> float:
        """Centre height a tube at (x, y) rests at on this tube's points before index `limit`, or -inf."""
        best: float = -math.inf
        reach: float = 2.0 * self.radius
        end: int = len(self.points) if limit is None else limit
        ki: int
        kj: int
        ki, kj = self._key(x, y)
        di: int
        dj: int
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                key: tuple[int, int] = (ki + di, kj + dj)
                # No point in this cell can raise the tube above `best`.
                if self._tops.get(key, -math.inf) + reach <= best:
                    continue
                index: int
                for index in self._cells.get(key, ()):
                    if index >= end:
                        continue
                    point: Point = self.points[index]
                    gap: float = (point[0] - x) ** 2 + (point[1] - y) ** 2
                    if gap < reach * reach:
                        best = max(best, point[2] + math.sqrt(reach * reach - gap))
        return best

    def top(self, x: float, y: float, reach: float) -> float | None:
        """Height of the tube's top surface within about `reach` of (x, y), or None where there is none."""
        best: float | None = None
        low: tuple[int, int] = self._key(x - reach, y - reach)
        high: tuple[int, int] = self._key(x + reach, y + reach)
        i: int
        j: int
        for i in range(low[0], high[0] + 1):
            for j in range(low[1], high[1] + 1):
                found: float | None = self._tops.get((i, j))
                if found is not None and (best is None or found > best):
                    best = found
        return None if best is None else best + self.radius

    def _rest(self, x: float, y: float, floor: Callable[[float, float], float]) -> Point:
        # The tube laid within CLEAR radii before the new point, measured across the ground, does not hold it
        # up. Measured along the tube, a step up onto an earlier loop would shorten that stretch, and the
        # tube would climb a staircase of its own points.
        limit: int = 0
        if self.points:
            last: Point = self.points[-1]
            run: float = self.run[-1] + math.hypot(x - last[0], y - last[1])
            limit = bisect.bisect_right(self.run, run - CLEAR * self.radius)
        return x, y, max(floor(x, y), self.support(x, y, limit))

    def lay(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        length: float,
        coil: float,
        floor: Callable[[float, float], float],
    ) -> int:
        """Lay `length` of tube while the point under the nozzle moves from `start` to `end`.

        `floor(x, y)` is the lowest centre height other objects allow there, and `coil` the coil radius
        (coil_radius). The tube also lays what it needs to catch up with a nozzle moving faster than the
        flow. Returns how many points were laid.
        """
        if not self.points:
            self._add(self._rest(start[0], start[1], floor))
        last: Point = self.points[-1]
        wanted: float = self.owed + max(0.0, length)
        behind: float = math.hypot(end[0] - last[0], end[1] - last[1]) - coil
        steps: int = min(int(max(wanted, behind) / self.spacing), STEP_LIMIT)
        self.owed = min(max(0.0, wanted - steps * self.spacing), STEP_LIMIT * self.spacing)
        # The coil turns as fast as the tube is laid, so a still nozzle's tube follows it round.
        turn: float = self.spacing / (COIL * self.radius)
        x: float = last[0]
        y: float = last[1]
        step: int
        for step in range(steps):
            share: float = (step + 1) / steps
            self.phase = (self.phase + turn) % math.tau
            target_x: float = start[0] + (end[0] - start[0]) * share + coil * math.cos(self.phase)
            target_y: float = start[1] + (end[1] - start[1]) * share + coil * math.sin(self.phase)
            gap: float = math.hypot(target_x - x, target_y - y)
            if gap > self.spacing:
                x += (target_x - x) * self.spacing / gap
                y += (target_y - y) * self.spacing / gap
            else:
                x, y = target_x, target_y
            self._add(self._rest(x, y, floor))
        return steps

    def strand(self, nozzle: Point) -> list[Point]:
        """Centre-line points of the tube hanging from the nozzle down to where it was last laid.

        A cubic curve that leaves the laid tube along its direction and reaches the nozzle from straight below.
        """
        if not self.points:
            return []
        end: Point = self.points[-1]
        span: float = math.dist(end, nozzle)
        if span < self.spacing:
            return []
        # A single point has no direction yet: the strand rises straight from it.
        before: Point = self.points[-2] if len(self.points) > 1 else (end[0], end[1], end[2] - 1.0)
        ahead: Point = _unit(end[0] - before[0], end[1] - before[1], end[2] - before[2], (0.0, 0.0, 1.0))
        reach: float = 0.4 * span
        handles: tuple[Point, Point, Point, Point] = (
            end,
            (end[0] + ahead[0] * reach, end[1] + ahead[1] * reach, end[2] + ahead[2] * reach),
            (nozzle[0], nozzle[1], nozzle[2] - reach),
            nozzle,
        )
        count: int = max(2, min(STRAND_POINTS, math.ceil(span / self.spacing)))
        points: list[Point] = []
        index: int
        for index in range(1, count + 1):
            t: float = index / count
            weights: tuple[float, float, float, float] = (
                (1.0 - t) ** 3,
                3.0 * (1.0 - t) ** 2 * t,
                3.0 * (1.0 - t) * t * t,
                t**3,
            )
            axis: list[float] = [0.0, 0.0, 0.0]
            weight: float
            handle: Point
            for weight, handle in zip(weights, handles, strict=True):
                axis[0] += weight * handle[0]
                axis[1] += weight * handle[1]
                axis[2] += weight * handle[2]
            points.append((axis[0], axis[1], axis[2]))
        return points

    def _ring(self, centre: Point, frame: Frame, scale: float = 1.0) -> Any:
        """SIDES vertices around `centre`, `scale` tube radii out, counter-clockwise about the tangent."""
        right: Any = np.asarray(frame[0], dtype=np.float64)
        up: Any = np.asarray(frame[1], dtype=np.float64)
        return np.asarray(centre, dtype=np.float64) + self.radius * scale * (_COS * right + _SIN * up)

    def _frames_for(self, line: list[Point], first: int) -> list[Frame]:
        """Frames for line[first:], carried on from the cached frame before them."""
        frames: list[Frame] = []
        previous: Point | None = self._frames[first - 1][0] if 0 < first <= len(self._frames) else None
        index: int
        for index in range(first, len(line)):
            before: Point = line[max(0, index - 1)]
            after: Point = line[min(len(line) - 1, index + 1)]
            tangent: Point = _unit(after[0] - before[0], after[1] - before[1], after[2] - before[2], (1.0, 0.0, 0.0))
            frame: Frame = _transport(previous, tangent)
            frames.append(frame)
            previous = frame[0]
        return frames

    def mesh(self, nozzle: Point | None = None) -> tuple[Any, Any, Any]:
        """Vertices, loop vertex indices, and polygon loop starts of the tube, with rounded ends.

        With a nozzle, the strand hanging from it is part of the tube.
        """
        line: list[Point] = self.points + (self.strand(nozzle) if nozzle is not None else [])
        if len(line) < 2:
            return np.zeros((0, 3)), np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.int32)
        # Every laid point but the last has its final tangent, so its ring is kept for the next mesh.
        final: int = len(self.points) - 1
        cached: int = len(self._frames)
        if final > cached:
            added: list[Frame] = self._frames_for(self.points, cached)[: final - cached]
            rings: list[Any] = [self._ring(self.points[cached + k], frame) for k, frame in enumerate(added)]
            self._frames += added
            self._rings = np.concatenate((self._rings, np.stack(rings)))
        tail_frames: list[Frame] = self._frames_for(line, final)
        tail: Any = np.stack([self._ring(line[final + k], frame) for k, frame in enumerate(tail_frames)])
        body: Any = np.concatenate((self._rings[:final], tail)).reshape(-1, 3)
        count: int = len(line)
        side: Any = np.arange(SIDES)
        following: Any = (side + 1) % SIDES
        ring: Any = np.arange(count - 1)[:, None] * SIDES
        quads: Any = np.stack((ring + side, ring + following, ring + SIDES + following, ring + SIDES + side), axis=-1)
        vertices: list[Any] = [body]
        faces: list[Any] = [quads.ravel()]
        sizes: list[Any] = [np.full(SIDES * (count - 1), 4)]
        first: Frame = self._frames[0] if self._frames else tail_frames[0]
        offset: int = len(body)
        end_index: int
        sign: float
        frame: Frame
        for end_index, sign, frame in ((0, -1.0, first), (count - 1, 1.0, tail_frames[-1])):
            centre: Point = line[end_index]
            forward: Point = _cross(frame[0], frame[1])
            previous: Any = end_index * SIDES + side
            level: int
            for level in range(1, CAP_RINGS + 1):
                # A hemisphere's latitudes, narrowing toward the tip.
                angle: float = level * math.pi / (2.0 * (CAP_RINGS + 1))
                along: float = sign * self.radius * math.sin(angle)
                middle: Point = (
                    centre[0] + forward[0] * along,
                    centre[1] + forward[1] * along,
                    centre[2] + forward[2] * along,
                )
                vertices.append(self._ring(middle, frame, math.cos(angle)))
                current: Any = offset + side
                band: Any = np.stack((previous, previous[following], current[following], current), axis=-1)
                # Both ends face outward: the start cap runs backward along the tube.
                faces.append((band if sign > 0 else band[:, ::-1]).ravel())
                sizes.append(np.full(SIDES, 4))
                previous = current
                offset += SIDES
            reach: float = sign * self.radius
            vertices.append(
                np.asarray(
                    [[centre[0] + forward[0] * reach, centre[1] + forward[1] * reach, centre[2] + forward[2] * reach]]
                )
            )
            fan: Any = np.stack((previous, previous[following], np.full(SIDES, offset)), axis=-1)
            faces.append((fan if sign > 0 else fan[:, ::-1]).ravel())
            sizes.append(np.full(SIDES, 3))
            offset += 1
        counts: Any = np.concatenate(sizes)
        starts: Any = np.concatenate(([0], np.cumsum(counts)[:-1])).astype(np.int32)
        return np.concatenate(vertices), np.concatenate(faces).astype(np.int32), starts
