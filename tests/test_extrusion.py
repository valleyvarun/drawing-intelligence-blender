"""The 3D Extruder's tube: laying, coiling, stacking, the hanging strand, and the mesh, without Blender."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from collections import Counter
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT: Path = Path(__file__).resolve().parents[1]
RADIUS: float = 0.01
FLOW: float = 0.3
TICK: float = 1.0 / 60.0


def load() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_extrusion_test", ROOT / "app/drawing_app/tools/extrusion.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ground(_x: float, _y: float) -> float:
    """Flat ground at height 0: a tube's centre rests one radius above it."""
    return RADIUS


class TubeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module: ModuleType = load()

    def drag(self, tube: Any, start: float, end: float, seconds: float) -> None:
        """Move the nozzle along x from `start` to `end` over `seconds`, a tick at a time."""
        ticks: int = round(seconds / TICK)
        speed: float = abs(end - start) / seconds
        index: int
        for index in range(ticks):
            a: float = start + (end - start) * index / ticks
            b: float = start + (end - start) * (index + 1) / ticks
            coil: float = self.module.coil_radius(RADIUS, speed, FLOW)
            tube.lay((a, 0.0), (b, 0.0), FLOW * TICK, coil, ground)

    def hold(self, tube: Any, seconds: float) -> None:
        _tick: int
        for _tick in range(round(seconds / TICK)):
            tube.lay((0.0, 0.0), (0.0, 0.0), FLOW * TICK, self.module.coil_radius(RADIUS, 0.0, FLOW), ground)

    def test_a_nozzle_faster_than_the_flow_lays_a_straight_tube_on_the_ground(self) -> None:
        tube: Any = self.module.Tube(RADIUS)
        self.drag(tube, 0.0, 1.0, 1.0)
        self.assertAlmostEqual(tube.length, 1.0, delta=tube.spacing)
        self.assertTrue(all(abs(point[2] - RADIUS) < 1e-12 for point in tube.points))
        self.assertTrue(all(abs(point[1]) < 1e-12 for point in tube.points))
        # Evenly spaced: the tube neither bunches up nor stretches.
        gaps: list[float] = [math.dist(a, b) for a, b in zip(tube.points, tube.points[1:], strict=False)]
        self.assertAlmostEqual(min(gaps), tube.spacing, places=9)
        self.assertAlmostEqual(max(gaps), tube.spacing, places=9)

    def test_holding_still_coils_the_tube_into_a_pile(self) -> None:
        tube: Any = self.module.Tube(RADIUS)
        self.hold(tube, 2.0)
        # The flow's length comes out whether or not the nozzle moves.
        self.assertAlmostEqual(tube.length, 2.0 * FLOW, delta=0.05)
        later: list[Any] = tube.points[len(tube.points) // 2 :]
        reach: list[float] = [math.hypot(point[0], point[1]) for point in later]
        self.assertAlmostEqual(min(reach), self.module.COIL * RADIUS, delta=RADIUS * 0.05)
        self.assertAlmostEqual(max(reach), self.module.COIL * RADIUS, delta=RADIUS * 0.05)
        # Each loop rests a tube width above the one under it.
        loop: float = math.tau * self.module.COIL * RADIUS
        loops: float = tube.length / loop
        top: float = max(point[2] for point in tube.points)
        self.assertGreater(top, RADIUS + 2 * RADIUS * (loops - 2))
        self.assertLess(top, RADIUS + 2 * RADIUS * (loops + 1))

    def test_a_slow_nozzle_leaves_loops_between_a_coil_and_a_line(self) -> None:
        tube: Any = self.module.Tube(RADIUS)
        self.drag(tube, 0.0, 0.2, 2.0)
        sideways: float = max(abs(point[1]) for point in tube.points)
        coil: float = self.module.coil_radius(RADIUS, 0.1, FLOW)
        self.assertGreater(sideways, coil * 0.5)
        self.assertLessEqual(sideways, self.module.COIL * RADIUS + 1e-9)
        # The spare length is laid as loops, so the tube is longer than the path.
        self.assertGreater(tube.length, 0.4)

    def test_loops_that_step_onto_each_other_do_not_climb_the_tube(self) -> None:
        # A slow drag whose pointer moves every fifth tick, as real pointer events do: the loops overlap,
        # and a step up onto one used to let the stretch just laid hold up the next point, again and again.
        tube: Any = self.module.Tube(RADIUS)
        footprint: tuple[float, float] = (0.0, 0.0)
        speed: float = 0.0
        tick: int
        for tick in range(180):
            target: tuple[float, float] = (0.6 * tick / 180, 0.0) if tick % 5 == 0 else footprint
            moved: float = math.hypot(target[0] - footprint[0], target[1] - footprint[1])
            speed += (moved / TICK - speed) * min(1.0, TICK / 0.15)
            tube.lay(footprint, target, FLOW * TICK, self.module.coil_radius(RADIUS, speed, FLOW), ground)
            footprint = target
        self.assertLess(max(point[2] for point in tube.points), 5 * RADIUS)
        self.assertLess(tube.length, 1.5 * FLOW * 180 * TICK)

    def test_a_tube_mesh_gives_back_its_centre_line(self) -> None:
        # Erasing part of a tube rebuilds the rest from the line read back from its mesh.
        points: list[tuple[float, float, float]] = [(0.1 * index, 0.02 * index * index, 0.3) for index in range(6)]
        vertices: Any
        vertices, _loops, _starts = self.module.tube_mesh(points, RADIUS)
        found: tuple[list[tuple[float, float, float]], float] | None = self.module.tube_line(vertices)
        assert found is not None
        line: list[tuple[float, float, float]]
        radius: float
        line, radius = found
        self.assertAlmostEqual(radius, RADIUS)
        self.assertEqual(len(line), len(points))
        point: tuple[float, float, float]
        expected: tuple[float, float, float]
        for point, expected in zip(line, points, strict=True):
            self.assertAlmostEqual(math.dist(point, expected), 0.0, places=9)
        # Another mesh is not read as a tube.
        self.assertIsNone(self.module.tube_line(vertices[:-5]))

    def test_a_second_pass_stacks_on_the_first(self) -> None:
        tube: Any = self.module.Tube(RADIUS)
        self.drag(tube, 0.0, 1.0, 1.0)
        first: int = len(tube.points)
        self.drag(tube, 1.0, 0.0, 1.0)
        middle: list[Any] = [point for point in tube.points[first:] if 0.1 < point[0] < 0.9]
        self.assertTrue(middle)
        # A tube width above the first pass, a little less where its points sit between the ones below.
        self.assertTrue(all(abs(point[2] - 3 * RADIUS) < 0.01 * RADIUS for point in middle))

    def test_the_tube_rests_on_what_the_floor_reports(self) -> None:
        tube: Any = self.module.Tube(RADIUS)

        def step(x: float, _y: float) -> float:
            # A 5 cm box from x = 0.5 on.
            return RADIUS + (0.05 if x >= 0.5 else 0.0)

        tube.lay((0.0, 0.0), (1.0, 0.0), 0.0, 0.0, step)
        self.assertTrue(all(abs(point[2] - step(point[0], 0.0)) < 1e-12 for point in tube.points))

    def test_the_point_rate_is_capped_for_thin_fast_tubes(self) -> None:
        self.assertEqual(self.module.spacing_for(0.01, 0.3), 0.005)
        self.assertAlmostEqual(self.module.spacing_for(0.001, 3.0), 3.0 / self.module.RATE_LIMIT)
        self.assertEqual(self.module.coil_radius(RADIUS, 0.6, FLOW), 0.0)
        self.assertAlmostEqual(self.module.coil_radius(RADIUS, 0.0, FLOW), self.module.COIL * RADIUS)

    def test_the_strand_hangs_from_the_nozzle_to_the_laid_tube(self) -> None:
        tube: Any = self.module.Tube(RADIUS)
        self.drag(tube, 0.0, 0.5, 0.5)
        end: Any = tube.points[-1]
        nozzle: tuple[float, float, float] = (end[0], end[1], 0.5)
        strand: list[Any] = tube.strand(nozzle)
        self.assertEqual(strand[-1], nozzle)
        self.assertLessEqual(len(strand), self.module.STRAND_POINTS)
        self.assertLess(math.dist(strand[0], end), 0.5 / len(strand) * 2)
        # It leaves the laid tube along its direction, before rising to the nozzle.
        self.assertGreater(strand[0][0], end[0])
        self.assertEqual(tube.strand((end[0], end[1], end[2] + tube.spacing / 2)), [])

    def test_mesh_is_a_closed_tube_facing_outward(self) -> None:
        tube: Any = self.module.Tube(RADIUS)
        self.drag(tube, 0.0, 0.3, 0.3)
        nozzle: tuple[float, float, float] = (0.3, 0.0, 0.1)
        line: list[Any] = tube.points + tube.strand(nozzle)
        vertices: Any
        loops: Any
        starts: Any
        vertices, loops, starts = tube.mesh(nozzle)
        sides: int = self.module.SIDES
        caps: int = 2 * (self.module.CAP_RINGS * sides + 1)
        self.assertEqual(len(vertices), len(line) * sides + caps)
        polygons: list[list[int]] = []
        index: int
        for index, start in enumerate(starts.tolist()):
            stop: int = starts[index + 1] if index + 1 < len(starts) else len(loops)
            polygons.append(loops[start:stop].tolist())
        # Closed: every edge joins exactly two polygons, in opposite directions.
        edges: Counter[tuple[int, int]] = Counter()
        polygon: list[int]
        for polygon in polygons:
            for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
                edges[(a, b)] += 1
        self.assertTrue(all(count == 1 for count in edges.values()))
        self.assertTrue(all((b, a) in edges for a, b in edges))
        # Outward: each face's normal points away from the nearest centre-line point.
        centres: Any = self.module.np.asarray(line)
        for polygon in polygons:
            points: Any = vertices[polygon]
            normal: Any = self.module.np.cross(points[1] - points[0], points[2] - points[0])
            middle: Any = points.mean(axis=0)
            nearest: Any = centres[self.module.np.argmin(((centres - middle) ** 2).sum(axis=1))]
            self.assertGreater(float(normal @ (middle - nearest)), 0.0)

    def test_cached_rings_match_a_mesh_built_at_once(self) -> None:
        stepped: Any = self.module.Tube(RADIUS)
        whole: Any = self.module.Tube(RADIUS)
        tube: Any
        for tube in (stepped, whole):
            self.hold(tube, 0.5)
            if tube is stepped:
                tube.mesh((0.0, 0.0, 0.2))
            self.drag(tube, 0.0, 0.4, 0.4)
            if tube is stepped:
                tube.mesh(None)
            self.hold(tube, 0.3)
        a: tuple[Any, Any, Any] = stepped.mesh((0.0, 0.0, 0.2))
        b: tuple[Any, Any, Any] = whole.mesh((0.0, 0.0, 0.2))
        part: int
        for part in range(3):
            self.assertTrue(self.module.np.allclose(a[part], b[part]))

    def test_the_top_is_the_highest_tube_surface_nearby(self) -> None:
        tube: Any = self.module.Tube(RADIUS)
        self.assertIsNone(tube.top(0.0, 0.0, 0.05))
        self.hold(tube, 1.0)
        highest: float = max(point[2] for point in tube.points)
        self.assertAlmostEqual(tube.top(0.0, 0.0, 0.05), highest + RADIUS)
        self.assertIsNone(tube.top(1.0, 1.0, 0.05))


if __name__ == "__main__":
    unittest.main()
