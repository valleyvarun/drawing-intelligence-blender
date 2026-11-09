"""Erase and Trim geometry without Blender: circle cuts, fades, trim pieces, face clipping, and what is in front."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT: Path = Path(__file__).resolve().parents[1]


def load() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_erase_geometry_test", ROOT / "app/drawing_app/tools/erase_geometry.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class EraseGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module: ModuleType = load()
        self.np: Any = self.module.np

    def line(self, count: int, y: float = 0.0) -> Any:
        """Points every 10 pixels along x, starting at x = 0."""
        return self.np.array([(10.0 * index, y) for index in range(count)])

    def test_the_circle_cuts_a_stroke_at_its_edge(self) -> None:
        points: Any = self.line(11)
        spans: list[tuple[float, float]] = self.module.erase_spans(
            points, (50.0, 0.0), 15.0, self.np.ones(11, dtype=bool), False
        )
        # 35 to 65 pixels is gone: points 3.5 to 6.5.
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0][0], 0.0)
        self.assertAlmostEqual(spans[0][1], 3.5)
        self.assertAlmostEqual(spans[1][0], 6.5)
        self.assertAlmostEqual(spans[1][1], 10.0)
        a: Any
        b: Any
        t: Any
        a, b, t = self.module.span_points(spans[0], 11, False)
        self.assertEqual(a.tolist(), [0, 1, 2, 3, 3])
        self.assertAlmostEqual(float(t[-1]), 0.5)
        # Nothing under the circle, or only points that are hidden: nothing changes.
        self.assertIsNone(self.module.erase_spans(points, (50.0, 40.0), 15.0, self.np.ones(11, dtype=bool), False))
        hidden: Any = self.np.ones(11, dtype=bool)
        hidden[3:8] = False
        self.assertIsNone(self.module.erase_spans(points, (50.0, 0.0), 15.0, hidden, False))
        # A circle covering everything removes the stroke.
        self.assertEqual(self.module.erase_spans(points, (50.0, 0.0), 80.0, self.np.ones(11, dtype=bool), False), [])

    def test_a_long_segment_is_cut_in_its_middle(self) -> None:
        points: Any = self.np.array([(0.0, 0.0), (100.0, 0.0)])
        spans: list[tuple[float, float]] = self.module.erase_spans(
            points, (50.0, 0.0), 10.0, self.np.ones(2, dtype=bool), False
        )
        self.assertEqual([(round(a, 6), round(b, 6)) for a, b in spans], [(0.0, 0.4), (0.6, 1.0)])

    def test_a_closed_stroke_opens_into_one_piece_around_its_first_point(self) -> None:
        corners: Any = self.np.array([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)])
        spans: list[tuple[float, float]] = self.module.erase_spans(
            corners, (100.0, 50.0), 10.0, self.np.ones(4, dtype=bool), True
        )
        # The cut is on the edge from point 1 to point 2; the rest runs from there round past point 0.
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0][0], 1.6)
        self.assertAlmostEqual(spans[0][1], 5.4)
        a: Any
        b: Any
        t: Any
        a, b, t = self.module.span_points(spans[0], 4, True)
        # From 1.6 on the edge after point 1, through points 2, 3, 0, and 1, to 0.4 along that edge again.
        self.assertEqual(a.tolist(), [1, 2, 3, 0, 1, 1])
        self.assertEqual(b.tolist(), [2, 3, 0, 1, 2, 2])
        self.assertAlmostEqual(float(t[0]), 0.6)
        self.assertAlmostEqual(float(t[-1]), 0.4)

    def test_faded_points_leave_runs_and_a_soft_edge(self) -> None:
        keep: Any = self.np.array([True, True, False, True, False, False, True, True])
        self.assertEqual(self.module.mask_spans(keep, False), [(0.0, 1.0), (3.0, 3.0), (6.0, 7.0)])
        self.assertEqual(self.module.mask_spans(keep, True), [(6.0, 9.0), (3.0, 3.0)])
        self.assertIsNone(self.module.mask_spans(self.np.ones(4, dtype=bool), False))
        strength: Any = self.module.falloff(self.np.array([0.0, 5.0, 9.0, 10.0, 12.0]), 10.0, 0.5)
        self.assertEqual(float(strength[0]), 1.0)
        self.assertEqual(float(strength[1]), 1.0)
        self.assertGreater(float(strength[2]), 0.0)
        self.assertLess(float(strength[2]), 0.5)
        self.assertEqual(float(strength[3]), 0.0)
        self.assertEqual(float(strength[4]), 0.0)

    def test_trim_removes_the_touched_piece_between_contacts(self) -> None:
        # Contacts at 3 and 7 on a stroke of 11 points: touching at 5 leaves both ends.
        self.assertEqual(self.module.trim_spans(11, False, [3.0, 7.0], [5.0]), [(0.0, 3.0), (7.0, 10.0)])
        # Touching an end piece removes up to the nearest contact; both touches remove two pieces.
        self.assertEqual(self.module.trim_spans(11, False, [3.0, 7.0], [1.0]), [(3.0, 10.0)])
        self.assertEqual(self.module.trim_spans(11, False, [3.0, 7.0], [1.0, 9.0]), [(3.0, 7.0)])
        # A stroke that meets nothing goes whole, as Grease Pencil's Trim does.
        self.assertEqual(self.module.trim_spans(11, False, [], [5.0]), [])
        # A closed stroke between two contacts keeps the other side, running past its first point.
        self.assertEqual(self.module.trim_spans(8, True, [2.0, 6.0], [4.0]), [(6.0, 10.0)])
        self.assertEqual(self.module.trim_spans(8, True, [2.0, 6.0], [7.0]), [(2.0, 6.0)])
        self.assertEqual(self.module.trim_spans(8, True, [2.0], [7.0]), [])

    def test_faces_are_clipped_along_the_circle(self) -> None:
        cutter: list[tuple[float, float]] = self.module.circle((0.0, 0.0), 10.0)
        self.assertGreater(self.module.area(cutter), math.pi * 100.0)
        square: list[tuple[float, float]] = [(-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0)]
        pieces: list[list[tuple[float, float]]] = self.module.subtract(square, cutter)
        remaining: float = sum(self.module.area(piece) for piece in pieces)
        self.assertAlmostEqual(remaining, 1600.0 - self.module.area(cutter), places=6)
        # Pieces keep the face's winding.
        self.assertTrue(all(self.module.area(piece) > 0.0 for piece in pieces))
        self.assertIsNone(self.module.subtract([(30.0, 30.0), (40.0, 30.0), (40.0, 40.0)], cutter))
        self.assertEqual(self.module.subtract([(-1.0, -1.0), (1.0, -1.0), (0.0, 1.0)], cutter), [])
        self.assertEqual(self.module.inside(self.np.array([(0.0, 0.0), (15.0, 0.0)]), cutter).tolist(), [True, False])

    def test_a_fill_with_a_hole_is_one_outline_joined_through_a_cut(self) -> None:
        # A 3 x 3 grid of unit squares without the middle one, as two triangles each.
        points: list[tuple[float, float]] = [(float(x), float(y)) for y in range(4) for x in range(4)]
        triangles: list[tuple[int, int, int]] = []
        x: int
        y: int
        for y in range(3):
            for x in range(3):
                if (x, y) == (1, 1):
                    continue
                a: int = y * 4 + x
                triangles += [(a, a + 1, a + 5), (a, a + 5, a + 4)]
        found: list[list[tuple[float, float]]] = self.module.outlines(points, triangles)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(self.module.area(found[0]), 8.0)
        # Two separate pieces are two outlines.
        apart: list[list[tuple[float, float]]] = self.module.outlines(
            points, [(0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6)]
        )
        self.assertEqual(sorted(round(self.module.area(outline), 6) for outline in apart), [1.0, 1.0])

    def test_only_the_front_is_erased_until_it_is_gone(self) -> None:
        layer: Any = self.module.Layer
        front: Any = layer("line", 1.0, 0, 0.01)
        back: Any = layer("pipe", 2.0, 2, 0.01)
        # Where the line covers the pipe, the pipe waits, even where it alone is under the eraser.
        active: set[str]
        fronts: list[set[str]]
        covered: set[str]
        active, fronts, covered = self.module.resolve([[front, back], [back]])
        self.assertEqual(active, {"line"})
        self.assertEqual(fronts, [{"line"}, {"pipe"}])
        self.assertEqual(covered, {"pipe"})
        active, _fronts, _covered = self.module.resolve([[back]])
        self.assertEqual(active, {"pipe"})
        # Strokes at the same depth are both in front; a mark on a surface is in front of the surface.
        level: Any = layer("other line", 1.0005, 0, 0.01)
        self.assertEqual(self.module.resolve([[front, level]])[0], {"line", "other line"})
        surface: Any = layer("pipe", 1.0, 2, 0.01)
        self.assertEqual(self.module.resolve([[front, surface]])[0], {"line"})
        # Something that cannot be erased hides what is behind it but holds nothing back elsewhere.
        wall: Any = layer("other:box", 0.5, 2, 0.01, False)
        self.assertEqual(self.module.resolve([[wall, front], [front]])[0], {"line"})
        self.assertEqual(self.module.resolve([[wall, front]])[0], set())
        # Strokes of one 2D-ordered Grease Pencil object stack by drawing order, whatever their depth.
        early: Any = layer("fill", 0.2, 0, 0.01, True, "Pencil", (0, 1), True)
        late: Any = layer("line", 3.0, 0, 0.01, True, "Pencil", (0, 2), True)
        self.assertEqual(self.module.resolve([[early, late], [early]])[0], {"line"})
        # In 3D they stack by depth, and a stroke drawn over another at the same depth is on top of it.
        under: Any = layer("first", 1.0, 0, 0.01, True, "Pencil", (0, 1))
        over: Any = layer("second", 1.0, 0, 0.01, True, "Pencil", (0, 2))
        self.assertEqual(self.module.resolve([[under, over]])[0], {"second"})
        nearer: Any = layer("first", 0.5, 0, 0.01, True, "Pencil", (0, 1))
        self.assertEqual(self.module.resolve([[nearer, over]])[0], {"first"})


if __name__ == "__main__":
    unittest.main()
