"""Sketch Mesh surfaces without Blender: line clean-up, lofts, the patch's outline rules, and solids.

Blender's triangulation is replaced by a fan over points in convex position, which is all these sketches need;
tests/test_sketch_mesh_native.py builds every kind of surface with the real one.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from importlib import import_module
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

np: Any = import_module("numpy")

ROOT: Path = Path(__file__).resolve().parents[1]
RADIUS: float = 0.01
Edge = tuple[int, int]
Triangle = tuple[int, int, int]


def load() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_sketch_surface_test", ROOT / "app/drawing_app/tools/sketch_surface.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dense(*corners: tuple[float, float, float], step: float = 0.004) -> Any:
    """A polyline through the corners with points `step` apart, as a drawn stroke has."""
    points: list[Any] = [np.array(corners[0], dtype=float)]
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    for start, end in zip(corners[:-1], corners[1:]):
        a: Any = np.array(start, dtype=float)
        b: Any = np.array(end, dtype=float)
        count: int = max(1, round(float(np.linalg.norm(b - a)) / step))
        index: int
        for index in range(1, count + 1):
            points.append(a + (b - a) * index / count)
    return np.array(points)


def circle(radius: float, height: float, start: float = 0.0, sweep: float = math.tau, count: int = 400) -> Any:
    angles: Any = start + np.linspace(0.0, sweep, count)
    return np.stack((radius * np.cos(angles), radius * np.sin(angles), np.full(count, height)), axis=1)


def arc(x: float, radius: float = 1.0, count: int = 200) -> Any:
    """A half circle standing in the plane at `x`."""
    angles: Any = np.linspace(0.0, math.pi, count)
    return np.stack((np.full(count, x), radius * np.cos(angles), radius * np.sin(angles)), axis=1)


def edge_faces(faces: list[tuple[int, ...]]) -> dict[Edge, list[Edge]]:
    """Every edge with the direction each of its faces runs it."""
    found: dict[Edge, list[Edge]] = {}
    face: tuple[int, ...]
    for face in faces:
        index: int
        for index in range(len(face)):
            a: int = face[index]
            b: int = face[(index + 1) % len(face)]
            found.setdefault((min(a, b), max(a, b)), []).append((a, b))
    return found


def open_edges(faces: list[tuple[int, ...]]) -> int:
    return sum(1 for runs in edge_faces(faces).values() if len(runs) == 1)


def faulty_edges(faces: list[tuple[int, ...]]) -> int:
    """Edges shared by more than two faces, or by two faces wound against each other."""
    return sum(1 for runs in edge_faces(faces).values() if len(runs) > 2 or (len(runs) == 2 and runs[0] == runs[1]))


def volume(points: Any, faces: list[tuple[int, ...]]) -> float:
    total: float = 0.0
    face: tuple[int, ...]
    for face in faces:
        corner: int
        for corner in range(1, len(face) - 1):
            total += float(np.dot(points[face[0]], np.cross(points[face[corner]], points[face[corner + 1]])))
    return total / 6.0


def nearest(points: Any, target: tuple[float, float, float]) -> float:
    return float(np.linalg.norm(np.asarray(points) - np.array(target), axis=1).min())


class SketchSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module: ModuleType = load()

    def fan(self, points: Any, edges: list[Edge]) -> Any:
        """A triangulation of points in convex position, given in order around their outline."""
        count: int = len(points)
        turn: float = 0.0
        index: int
        for index in range(count):
            a: Any = points[index]
            b: Any = points[(index + 1) % count]
            turn += float(a[0] * b[1] - b[0] * a[1])
        triangles: list[Triangle] = []
        for index in range(1, count - 1):
            triangles.append((0, index, index + 1) if turn > 0.0 else (0, index + 1, index))
        constraints: dict[Edge, list[int]] = {}
        edge: Edge
        for index, edge in enumerate(edges):
            constraints[(min(edge), max(edge))] = [index]
        return self.module.Triangulation(
            np.asarray(points, dtype=float), triangles, [[i] for i in range(count)], constraints
        )

    def test_lines_are_cleaned_and_closed_where_their_ends_meet(self) -> None:
        loop: Any = circle(1.0, 0.0, sweep=math.tau - 0.05)
        # The pen overshoots the start: the two tails beyond the crossing are cut off.
        overshot: Any = circle(1.0, 0.0, start=-0.2, sweep=math.tau + 0.4)
        dab: Any = dense((5.0, 5.0, 0.0), (5.01, 5.0, 0.0))
        broken: Any = np.array([[0.0, 0.0, 0.0], [math.nan, 0.0, 0.0], [0.0, 0.0, 0.0]])
        lines: list[Any] = self.module.prepare(
            [loop, overshot, dense((0, 0, 0), (2, 0, 0)), circle(1.0, 0.0, sweep=4.0), dab, broken], RADIUS
        )
        self.assertEqual([line.closed for line in lines], [True, True, False, False])
        self.assertAlmostEqual(lines[0].length, math.tau, delta=0.02)
        self.assertAlmostEqual(lines[1].length, math.tau, delta=0.05)
        self.assertEqual(self.module.prepare([dab], RADIUS), [])

    def test_resampling_keeps_corners_and_ends(self) -> None:
        bent: Any = self.module.Line(dense((0, 0, 0), (1.03, 0, 0), (1.03, 0.71, 0)))
        sampled: Any = self.module._resample(bent, 0.1)
        self.assertLess(nearest(sampled.points, (1.03, 0.0, 0.0)), 1e-9)
        self.assertLess(nearest(sampled.points[:1], (0.0, 0.0, 0.0)), 1e-9)
        self.assertLess(nearest(sampled.points[-1:], (1.03, 0.71, 0.0)), 1e-9)
        steps: Any = np.linalg.norm(np.diff(sampled.points, axis=0), axis=1)
        self.assertLess(float(steps.max()), 0.12)
        self.assertGreater(float(steps.min()), 0.08)
        square: Any = self.module.prepare([dense((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0))], RADIUS)[0]
        self.assertTrue(square.closed)
        corners: Any = self.module._resample(square, 0.13).points
        corner: tuple[float, float, float]
        for corner in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)):
            self.assertLess(nearest(corners, corner), 1e-6, corner)
        # A smooth curve has no corners, however finely it is drawn.
        self.assertEqual(len(self.module._corners(self.module.Line(circle(1.0, 0.0), True), 0.1)), 0)

    def test_ribs_are_lofted_in_order_whichever_way_each_was_drawn(self) -> None:
        ribs: list[Any] = [arc(0.0), arc(0.8), arc(1.6), arc(2.4)]
        straight: Any = self.module.build(ribs, RADIUS, closed=False, curved=False)
        self.assertEqual((straight.method, straight.smooth), ("loft", False))
        self.assertTrue(all(len(face) == 4 for face in straight.faces))
        self.assertEqual(faulty_edges(straight.faces), 0)
        # Four ribs: three strips of quads, open on all four sides.
        columns: int = len(straight.points) // 4
        self.assertEqual(len(straight.faces), 3 * (columns - 1))
        self.assertEqual(open_edges(straight.faces), 2 * (columns - 1) + 2 * 3)
        sample: Any
        for sample in (ribs[0][0], ribs[0][-1], ribs[3][100], ribs[2][0]):
            self.assertLess(nearest(straight.points, tuple(sample)), 0.06)
        # Drawn out of order, and one of them backwards: the same strips.
        shuffled: Any = self.module.build(
            [ribs[2], ribs[0][::-1], ribs[3], ribs[1]], RADIUS, closed=False, curved=False
        )
        self.assertEqual(len(shuffled.faces), len(straight.faces))
        self.assertAlmostEqual(self.area(shuffled), self.area(straight), places=6)
        # Curved adds rows between the ribs and still passes through them.
        curved: Any = self.module.build(ribs, RADIUS, closed=False, curved=True)
        self.assertTrue(curved.smooth)
        self.assertGreater(len(curved.faces), 3 * len(straight.faces))
        self.assertLess(nearest(curved.points, tuple(ribs[1][100])), 0.06)
        self.assertLess(float(np.abs(np.linalg.norm(curved.points[:, 1:], axis=1) - 1.0).max()), 0.02)

    def area(self, surface: Any) -> float:
        total: float = 0.0
        face: tuple[int, ...]
        for face in surface.faces:
            corner: int
            for corner in range(1, len(face) - 1):
                a: Any = surface.points[face[corner]] - surface.points[face[0]]
                b: Any = surface.points[face[corner + 1]] - surface.points[face[0]]
                total += 0.5 * float(np.linalg.norm(np.cross(a, b)))
        return total

    def test_rings_make_a_tube_and_closed_a_capped_solid_without_twist(self) -> None:
        rings: list[Any] = [circle(1.0, 0.0), circle(1.0, 1.0), circle(1.0, 2.0)]
        tube: Any = self.module.build(rings, RADIUS, closed=False, curved=False)
        self.assertEqual(tube.method, "loft")
        self.assertEqual(faulty_edges(tube.faces), 0)
        columns: int = len(tube.points) // 3
        self.assertEqual(open_edges(tube.faces), 2 * columns)
        solid: Any = self.module.build(rings, RADIUS, closed=True, curved=False)
        self.assertEqual((open_edges(solid.faces), faulty_edges(solid.faces)), (0, 0))
        self.assertAlmostEqual(volume(solid.points, solid.faces), math.pi * 2.0, delta=0.06)
        # Rings started elsewhere and drawn the other way round line up: no twisted, thinner tube.
        turned: list[Any] = [rings[0], circle(1.0, 1.0, start=2.0)[::-1], circle(1.0, 2.0, start=-1.3)]
        again: Any = self.module.build(turned, RADIUS, closed=True, curved=False)
        self.assertAlmostEqual(volume(again.points, again.faces), volume(solid.points, solid.faces), delta=0.01)
        # Open ribs, closed: each gets a straight closing side, and the ends are capped.
        vault: Any = self.module.build([arc(0.0), arc(1.0), arc(2.0)], RADIUS, closed=True, curved=False)
        self.assertEqual((vault.method, open_edges(vault.faces), faulty_edges(vault.faces)), ("loft", 0, 0))
        self.assertAlmostEqual(volume(vault.points, vault.faces), math.pi, delta=0.05)

    def test_ribs_with_corners_keep_them(self) -> None:
        frames: list[Any] = [
            dense((0, 0, level), (1, 0, level), (1, 1, level), (0, 1, level), (0, 0, level))
            for level in (0.0, 0.6, 1.2)
        ]
        box: Any = self.module.build(frames, RADIUS, closed=True, curved=False)
        self.assertEqual((box.method, open_edges(box.faces), faulty_edges(box.faces)), ("loft", 0, 0))
        corner: tuple[float, float, float]
        for corner in ((0, 0, 0), (1, 0, 0), (1, 1, 0.6), (0, 1, 1.2)):
            self.assertLess(nearest(box.points, corner), 1e-6, corner)
        self.assertAlmostEqual(volume(box.points, box.faces), 1.2, delta=1e-6)

    def test_lines_that_do_not_run_alongside_each_other_are_not_ribs(self) -> None:
        # Two lines at a right angle, at different heights: not a loft's ribs.
        lines: list[Any] = self.module.prepare(
            [dense((0, 0, 0), (1, 0, 0)), dense((1.2, 0.2, 0.5), (1.2, 1.2, 0.5))], RADIUS
        )
        self.assertIsNone(self.module._loft(lines, 0.05, False, False))
        ribs: list[Any] = self.module.prepare([arc(0.0), arc(1.0)], RADIUS)
        self.assertIsNotNone(self.module._loft(ribs, 0.05, False, False))

    def test_outline_rules_drop_hollows_and_wide_openings_but_never_cross_a_line(self) -> None:
        # Line 0 is a tent over (1, 0.5); line 1 runs above it. The hull adds the hollow under the tent.
        points: Any = np.array([[0.0, 0.0], [1.0, 0.5], [2.0, 0.0], [0.0, 2.0], [2.0, 2.0]])
        triangles: list[Triangle] = [(0, 2, 1), (0, 1, 3), (1, 4, 3), (1, 2, 4)]
        constraints: dict[Edge, list[int]] = {(0, 1): [0], (1, 2): [1], (3, 4): [2]}
        result: Any = self.module.Triangulation(points, triangles, [[0], [1], [2], [3], [4]], constraints)
        owners: list[frozenset[int]] = [frozenset({0})] * 3 + [frozenset({1})] * 2
        # The two lines are 1.5 apart: the sides between their ends (2 long) stay closed up to a limit of 2.
        self.assertEqual(self.module._region(result, owners, 2.5), [False, True, True, True])
        # A narrower limit lets the outside in between the lines.
        self.assertEqual(self.module._region(result, owners, 1.0), [False, False, False, False])
        # With every outer edge a line, nothing is reached, not even a hollow.
        enclosed: Any = self.module.Triangulation(
            points, triangles, [[0], [1], [2], [3], [4]], {**constraints, (0, 2): [3], (0, 3): [4], (2, 4): [5]}
        )
        self.assertEqual(self.module._region(enclosed, owners, 0.0), [True, True, True, True])

    def test_enclosed_triangles_are_found_from_the_outline_alone(self) -> None:
        # A square of two triangles with a triangle hanging off each of two sides.
        triangles: list[Triangle] = [(0, 1, 2), (0, 2, 3), (1, 4, 2), (3, 2, 5)]
        outline: list[Edge] = self.module._rim(triangles[:2])
        self.assertEqual(sorted(outline), [(0, 1), (1, 2), (2, 3), (3, 0)])
        self.assertEqual(self.module._enclosed(triangles, set(outline)), [True, True, False, False])
        # The same outline run the other way surrounds the rest instead.
        reverse: set[Edge] = {(b, a) for a, b in outline}
        self.assertEqual(self.module._enclosed(triangles, reverse), [False, False, True, True])

    def test_straight_spans_are_turned_to_reach_another_line(self) -> None:
        # A floor's corner (0, 1, 2 on line 0) next to a triangle that reaches the ridge (3 on line 1).
        points: Any = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        owners: list[frozenset[int]] = [frozenset({0})] * 3 + [frozenset({1})]
        constraints: dict[Edge, list[int]] = {(0, 1): [0], (0, 2): [1]}
        turned: list[Triangle] = self.module._bridge(points, [(0, 1, 2), (1, 3, 2)], owners, constraints)
        self.assertEqual(sorted(tuple(sorted(triangle)) for triangle in turned), [(0, 1, 3), (0, 2, 3)])
        self.assertEqual(faulty_edges(list(turned)), 0)
        # An edge that is a line, or four points that do not make a convex shape, stay as they are.
        fixed: dict[Edge, list[int]] = {**constraints, (1, 2): [2]}
        self.assertEqual(self.module._bridge(points, [(0, 1, 2), (1, 3, 2)], owners, fixed), [(0, 1, 2), (1, 3, 2)])
        dented: Any = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.2, 0.2]])
        self.assertEqual(
            self.module._bridge(dented, [(0, 1, 3), (0, 3, 2)], [owners[0]] * 4, {}), [(0, 1, 3), (0, 3, 2)]
        )

    def test_ends_that_meet_join_into_one_network(self) -> None:
        spacing: float = 0.1
        corner: list[Any] = [
            self.module._resample(self.module.Line(dense((0, 0, 0), (1, 0, 0))), spacing),
            self.module._resample(self.module.Line(dense((1.005, 0.004, 0), (1, 1, 0))), spacing),
            self.module._resample(self.module.Line(dense((1.003, -0.002, 0.004), (1, 0, 1))), spacing),
        ]
        network: Any = self.module._network(corner, 0.04, spacing)
        # Three ends became one point, which all three lines' segments reach.
        self.assertEqual(len(network.points), sum(len(line.points) for line in corner) - 2)
        shared: list[int] = [
            index for index in range(len(network.points)) if sum(index in edge for edge in network.edges) == 3
        ]
        self.assertEqual(len(shared), 1)
        self.assertEqual(network.edge_line.count(-1), 0)
        # An end a little farther off keeps its place and gets a joining edge instead.
        tee: list[Any] = [
            self.module._resample(self.module.Line(dense((0, 0, 0), (1, 0, 0))), 0.02),
            self.module._resample(self.module.Line(dense((0.5, 0.03, 0), (0.5, 1, 0))), 0.02),
        ]
        joined: Any = self.module._network(tee, 0.04, 0.02)
        self.assertEqual(len(joined.points), sum(len(line.points) for line in tee))
        self.assertEqual(joined.edge_line.count(-1), 1)
        # Lines that are apart stay apart.
        apart: Any = self.module._network(
            [tee[0], self.module.Line(tee[1].points + np.array([0.0, 0.2, 0.0]))], 0.04, 0.02
        )
        self.assertEqual(apart.edge_line.count(-1), 0)

    def test_the_plane_is_the_one_the_lines_are_seen_whole_from(self) -> None:
        def network(paths: list[Any]) -> Any:
            lines: list[Any] = [self.module._resample(line, 0.08) for line in self.module.prepare(paths, RADIUS)]
            return self.module._network(lines, 0.04, 0.08)

        # A tall dome: its base circle and two arches three times as high as it is wide.
        tall: list[Any] = [circle(0.5, 0.0)]
        angles: Any = np.linspace(0.0, math.pi, 300)
        tall.append(np.stack((0.5 * np.cos(angles), np.zeros(300), 3.0 * np.sin(angles)), axis=1))
        tall.append(np.stack((np.zeros(300), 0.5 * np.cos(angles), 3.0 * np.sin(angles)), axis=1))
        frame: Any
        folds: float
        frame, folds = self.module._frame(network(tall), 3.0)
        self.assertAlmostEqual(abs(float(frame[2][2])), 1.0, places=6)
        self.assertLess(folds, self.module.FOLD_LIMIT)
        self.assertAlmostEqual(float(np.linalg.det(frame)), 1.0, places=9)
        # A box's edges fold over each other from every side: a cage, not a sheet.
        edges: list[Any] = []
        level: float
        for level in (0.0, 1.0):
            edges += [
                dense((0, 0, level), (1.5, 0, level)),
                dense((1.5, 0, level), (1.5, 1, level)),
                dense((1.5, 1, level), (0, 1, level)),
                dense((0, 1, level), (0, 0, level)),
            ]
        x: float
        y: float
        for x, y in ((0.0, 0.0), (1.5, 0.0), (1.5, 1.0), (0.0, 1.0)):
            edges.append(dense((x, y, 0), (x, y, 1)))
        self.assertGreater(self.module._frame(network(edges), 1.5)[1], self.module.FOLD_LIMIT)

    def test_the_spline_passes_through_its_points_and_keeps_a_plane_flat(self) -> None:
        generator: Any = np.random.default_rng(7)
        centres: Any = generator.uniform(-1.0, 1.0, (60, 2))
        tilted: Any = 0.3 * centres[:, 0] - 0.7 * centres[:, 1] + 2.0
        queries: Any = generator.uniform(-1.0, 1.0, (40, 2))
        values: Any = self.module._spline_values(self.module._spline(centres, tilted), queries)
        self.assertLess(float(np.abs(values - (0.3 * queries[:, 0] - 0.7 * queries[:, 1] + 2.0)).max()), 1e-6)
        bumpy: Any = np.sin(3.0 * centres[:, 0]) * np.cos(2.0 * centres[:, 1])
        spline: Any = self.module._spline(centres, bumpy)
        self.assertLess(float(np.abs(self.module._spline_values(spline, centres) - bumpy).max()), 1e-3)
        # Points all in one line still solve, and two values at one place are averaged.
        row: Any = np.stack((np.linspace(0.0, 1.0, 9), np.zeros(9)), axis=1)
        flat: Any = self.module._spline(np.concatenate((row, row[:1])), np.concatenate((np.ones(9), [3.0])))
        self.assertAlmostEqual(float(self.module._spline_values(flat, row[4:5])[0]), 1.0, delta=0.3)
        self.assertAlmostEqual(float(self.module._spline_values(flat, row[:1])[0]), 2.0, delta=0.3)

    def disc(self, rings: int = 10, around: int = 36) -> tuple[Any, list[Triangle]]:
        """A unit disc of triangles between concentric rings, counter-clockwise."""
        points: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
        ring: int
        step: int
        for ring in range(1, rings + 1):
            for step in range(around):
                angle: float = math.tau * step / around
                points.append((ring / rings * math.cos(angle), ring / rings * math.sin(angle), 0.0))
        triangles: list[Triangle] = []
        for step in range(around):
            triangles.append((0, 1 + step, 1 + (step + 1) % around))
        for ring in range(1, rings):
            inner: int = 1 + (ring - 1) * around
            outer: int = inner + around
            for step in range(around):
                following: int = (step + 1) % around
                triangles.append((inner + step, outer + step, outer + following))
                triangles.append((inner + step, outer + following, inner + following))
        return np.array(points), triangles

    def test_a_flat_disc_inflates_into_a_ball(self) -> None:
        local: Any
        triangles: list[Triangle]
        local, triangles = self.disc()
        rise: Any
        inner: Any
        rise, inner = self.module._bulge(local[:, :2], triangles)
        self.assertAlmostEqual(float(rise[0]), 1.0, delta=0.03)
        expected: Any = np.sqrt(np.maximum(0.0, 1.0 - (local[:, :2] ** 2).sum(axis=1)))
        self.assertLess(float(np.abs(rise - expected).max()), 0.05)
        self.assertEqual(len(inner), len(local) - 36)
        sheet: Any = self.module._Sheet(local, triangles, np.eye(3), np.array([0.0, 0.0, 5.0]))
        ball: Any = self.module._inflate(sheet)
        self.assertEqual((ball.method, open_edges(ball.faces), faulty_edges(ball.faces)), ("inflate", 0, 0))
        self.assertAlmostEqual(
            volume(ball.points - np.array([0.0, 0.0, 5.0]), ball.faces), 4.0 / 3.0 * math.pi, delta=0.3
        )
        self.assertAlmostEqual(float(ball.points[:, 2].max()), 6.0, delta=0.03)
        self.assertAlmostEqual(float(ball.points[:, 2].min()), 4.0, delta=0.03)

    def test_a_patch_only_one_edge_wide_still_inflates_into_a_closed_surface(self) -> None:
        # A strip of two triangles has no point inside its outline: its middle edge gets one.
        strip: Any = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]])
        local: Any
        triangles: list[Triangle]
        local, triangles = self.module._roomy(strip, [(0, 1, 2), (0, 2, 3)])
        self.assertEqual((len(local), len(triangles)), (5, 4))
        pillow: Any = self.module._inflate(self.module._Sheet(strip, [(0, 1, 2), (0, 2, 3)], np.eye(3), np.zeros(3)))
        self.assertEqual((open_edges(pillow.faces), faulty_edges(pillow.faces)), (0, 0))
        self.assertGreater(volume(pillow.points, pillow.faces), 0.0)
        # A lone triangle has nothing to lift: it stays a flat patch.
        flat: Any = self.module._inflate(self.module._Sheet(strip[:3], [(0, 1, 2)], np.eye(3), np.zeros(3)))
        self.assertEqual((flat.method, len(flat.faces)), ("patch", 1))

    def test_a_patch_becomes_a_solid_with_walls_to_a_flat_base(self) -> None:
        # A pyramid's four sides over a square whose corners sit at different heights.
        local: Any = np.array(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.2], [1.0, 1.0, 0.0], [0.0, 1.0, 0.2], [0.5, 0.5, 1.0]], dtype=float
        )
        sides: list[Triangle] = [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
        sheet: Any = self.module._Sheet(local, sides, np.eye(3), np.zeros(3))
        solid: Any = self.module._solid(sheet, 1.0, False, self.fan)
        self.assertEqual((solid.method, open_edges(solid.faces), faulty_edges(solid.faces)), ("solid", 0, 0))
        # Two corners are already in the base; the other two get a partner below them.
        self.assertEqual(len(solid.points), 7)
        self.assertAlmostEqual(float(solid.points[:, 2].min()), 0.0)
        self.assertGreater(volume(solid.points, solid.faces), 1.0 / 3.0)
        # Hanging below its outline, the patch is closed from above instead.
        bowl: Any = self.module._solid(
            self.module._Sheet(local * np.array([1, 1, -1]), sides, np.eye(3), np.zeros(3)), 1.0, False, self.fan
        )
        self.assertEqual((open_edges(bowl.faces), faulty_edges(bowl.faces)), (0, 0))
        self.assertAlmostEqual(float(bowl.points[:, 2].max()), 0.0)
        self.assertAlmostEqual(volume(bowl.points, bowl.faces), volume(solid.points, solid.faces), places=9)
        # Parts of the patch lying in the base have no thickness and are left out; a flat patch stays a patch.
        floor: Any = np.array(
            [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 1.0], [2, 0, 0], [2, 1, 0]], dtype=float
        )
        tent: Any = self.module._solid(
            self.module._Sheet(floor, [*sides, (1, 5, 6), (1, 6, 2)], np.eye(3), np.zeros(3)), 1.0, False, self.fan
        )
        self.assertEqual((open_edges(tent.faces), faulty_edges(tent.faces), len(tent.points)), (0, 0, 5))
        self.assertAlmostEqual(volume(tent.points, tent.faces), 1.0 / 3.0, places=9)
        level: Any = self.module._solid(
            self.module._Sheet(floor[:4], [(0, 1, 2), (0, 2, 3)], np.eye(3), np.zeros(3)), 1.0, False, self.fan
        )
        self.assertEqual((level.method, len(level.faces)), ("patch", 2))

    def test_a_single_outline_is_filled_and_an_open_line_is_closed_by_a_straight_side(self) -> None:
        outline: Any = dense((0, 0, 0.5), (2, 0, 0.5), (2, 1, 0.5), (0, 1, 0.5), (0, 0, 0.5))
        patch: Any = self.module.build([outline], RADIUS, closed=False, curved=False, triangulate=self.fan)
        self.assertEqual((patch.method, patch.smooth, faulty_edges(patch.faces)), ("patch", False, 0))
        self.assertAlmostEqual(self.area(patch), 2.0, places=6)
        self.assertLess(float(np.abs(patch.points[:, 2] - 0.5).max()), 1e-9)
        # Flat faces cannot close a flat outline into a solid: it stays the filled outline.
        solid: Any = self.module.build([outline], RADIUS, closed=True, curved=False, triangulate=self.fan)
        self.assertEqual(solid.method, "patch")
        self.assertAlmostEqual(self.area(solid), 2.0, places=6)
        half: Any = self.module.build([circle(1.0, 0.0, sweep=math.pi)], RADIUS, curved=False, triangulate=self.fan)
        self.assertAlmostEqual(self.area(half), math.pi / 2.0, delta=0.02)
        # Standing on its edge, the outline is filled in its own plane.
        wall: Any = self.module.build([outline[:, [0, 2, 1]]], RADIUS, curved=False, triangulate=self.fan)
        self.assertAlmostEqual(self.area(wall), 2.0, places=6)
        self.assertLess(float(np.abs(wall.points[:, 1] - 0.5).max()), 1e-9)

    def test_sketches_that_cannot_make_a_surface_say_why(self) -> None:
        cases: tuple[tuple[list[Any], str], ...] = (
            ([], "Draw the lines"),
            ([dense((0, 0, 0), (0.005, 0, 0))], "Draw the lines"),
            ([dense((0, 0, 0), (1, 0, 0))], "straight line"),
            ([dense((0, 0, 0), (1, 0, 0)), dense((1.5, 0, 0), (3, 0, 0))], "straight line"),
        )
        paths: list[Any]
        message: str
        for paths, message in cases:
            with self.assertRaises(self.module.SketchError) as raised:
                self.module.build(paths, RADIUS, triangulate=self.fan)
            self.assertIn(message, str(raised.exception))

    def test_the_tool_box_offers_the_same_detail_range(self) -> None:
        spec: ModuleSpec | None = importlib.util.spec_from_file_location(
            "_sketch_state_test", ROOT / "app/drawing_app/core/state.py"
        )
        assert spec is not None and spec.loader is not None
        state: ModuleType = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = state
        spec.loader.exec_module(state)
        self.assertEqual(state.SKETCH_DETAIL, self.module.DETAIL_LIMITS)
        self.assertEqual(state.SketchMesh().detail, self.module.DETAIL)
        self.assertEqual((state.SketchMesh().closed, state.SketchMesh().curved), (False, True))

    def test_detail_sets_how_many_faces_span_the_sketch(self) -> None:
        rings: list[Any] = [circle(1.0, 0.0), circle(1.0, 2.0)]
        coarse: Any = self.module.build(rings, RADIUS, detail=8, curved=False)
        fine: Any = self.module.build(rings, RADIUS, detail=32, curved=False)
        self.assertGreater(len(fine.faces), 3 * len(coarse.faces))
        # Out-of-range values are brought into range rather than refused.
        self.assertEqual(len(self.module.build(rings, RADIUS, detail=1, curved=False).faces), len(coarse.faces))
        self.assertEqual(
            len(self.module.build(rings, RADIUS, detail=1000, curved=False).faces),
            len(self.module.build(rings, RADIUS, detail=64, curved=False).faces),
        )


if __name__ == "__main__":
    unittest.main()
