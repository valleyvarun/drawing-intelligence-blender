"""Centerline fill geometry without Blender or pipe mesh data."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from struct import pack, unpack
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from drawing_app.core.state import Vec3
    from drawing_app.tools.curve_fill import Mesh, Prepared

ROOT: Path = Path(__file__).resolve().parents[1]


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


class CurveFillTests(unittest.TestCase):
    def setUp(self) -> None:
        imports: dict[str, ModuleType] = {}
        name: str
        for name in ("core.state", "tools.geometry", "tools.pipe_geometry", "tools.curve_fill"):
            with patch.dict(sys.modules, imports):
                item: ModuleType = module("_curve_test." + name, "app/drawing_app/" + name.replace(".", "/") + ".py")
            imports[item.__name__] = item
        self.fill: ModuleType = imports["_curve_test.tools.curve_fill"]

    def square(self, radius: float = 1.0, z: float = 5.0) -> list[Vec3]:
        return [
            (-radius, -radius, z),
            (radius, -radius, z),
            (radius, radius, z),
            (-radius, radius, z),
            (-radius, -radius, z),
        ]

    def area(self, mesh: Mesh) -> float:
        total: float = 0.0
        face: tuple[int, int, int]
        for face in mesh.faces:
            a: Vec3 = mesh.points[face[0]]
            b: Vec3 = mesh.points[face[1]]
            c: Vec3 = mesh.points[face[2]]
            ab: Vec3 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            ac: Vec3 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            cross: Vec3 = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
            total += math.hypot(*cross) * 0.5
        return total

    def test_offset_tilted_plane_and_cached_mesh(self) -> None:
        path: list[Vec3] = [(4, 2, 3), (6, 2, 5), (6, 4, 5), (4, 4, 3), (4, 2, 3)]
        prepared: Prepared = self.fill.prepare([path])
        mesh: Mesh | None = self.fill.fill(prepared, (5, 3, 10), (0, 0, -1))
        assert mesh is not None
        point: Vec3
        for point in mesh.points:
            self.assertAlmostEqual(point[2], point[0] - 1.0)
        self.assertAlmostEqual(self.area(mesh), 4.0 * math.sqrt(2))
        self.assertIs(mesh, self.fill.fill(prepared, (5.2, 3, 10), (0, 0, -1)))

    def test_separate_curve_edges_allow_small_depth_errors_but_not_separate_planes(self) -> None:
        square: list[Vec3] = self.square()
        index: int
        paths: list[list[Vec3]] = []
        for index in range(4):
            paths.append(square[index : index + 2])
        mesh: Mesh | None = self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0)
        paths[-1] = [(-1, 1, 5.02), (-1, -1, 5.02)]
        mesh = self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0, delta=0.005)
        paths[-1] = [(-1, 1, 5.5), (-1, -1, 5.5)]
        self.assertIsNone(self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1)))

    def test_open_curve_closes_without_changing_source_points(self) -> None:
        path: list[Vec3] = self.square()
        path[-1] = (-1.0, -0.97, 5.0)
        original: list[Vec3] = path[:]
        mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0)
        self.assertEqual(path, original)

    def test_large_open_arc_uses_straight_endpoint_chord(self) -> None:
        path: list[Vec3] = []
        index: int
        for index in range(101):
            angle: float = math.pi * index / 100
            path.append((math.cos(angle), math.sin(angle), 5.0))
        mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), (0, 0.5, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), math.pi * 0.5, places=3)
        point: Vec3
        for point in mesh.points:
            self.assertAlmostEqual(point[2], 5.0)
            self.assertGreaterEqual(point[1], -1e-7)
        self.assertIsNone(self.fill.fill(self.fill.prepare([path]), (0, -0.5, 9), (0, 0, -1)))

    def test_open_tilted_curve_closes_with_diagonal_and_lines_have_no_area(self) -> None:
        path: list[Vec3] = [(4, 2, 3), (6, 2, 5), (6, 4, 5)]
        mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), (5.5, 2.5, 10), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 2.0 * math.sqrt(2))
        point: Vec3
        for point in mesh.points:
            self.assertAlmostEqual(point[2], point[0] - 1.0)
            self.assertLessEqual(point[1], point[0] - 2.0 + 1e-7)
        self.assertIsNone(self.fill.fill(self.fill.prepare([path[:2]]), (5, 2.5, 10), (0, 0, -1)))
        self.assertIsNone(self.fill.fill(self.fill.prepare([[(0, 0, 5), (1, 0, 5), (2, 0, 5)]]), (1, 0, 9), (0, 0, -1)))

    def test_pen_overshoot_and_interior_branch_keep_closed_region(self) -> None:
        path: list[Vec3] = self.square() + [(-0.8, -0.8, 5.0)]
        mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0)
        paths: list[list[Vec3]] = [self.square(), [(0, -1, 5), (0, 0, 5)]]
        mesh = self.fill.fill(self.fill.prepare(paths), (0.5, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0)

    def test_chord_splits_only_clicked_half(self) -> None:
        paths: list[list[Vec3]] = [self.square(), [(0, -2, 5), (0, 2, 5)]]
        mesh: Mesh | None = self.fill.fill(self.fill.prepare(paths), (-0.5, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 2.0)
        self.assertLessEqual(max(point[0] for point in mesh.points), 1e-6)

    def test_holes_and_nested_regions_keep_correct_area(self) -> None:
        paths: list[list[Vec3]] = [self.square(3.0), self.square(2.0), self.square(1.0)]
        prepared: Prepared = self.fill.prepare(paths)
        mesh: Mesh | None = self.fill.fill(prepared, (2.5, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 20.0)
        mesh = self.fill.fill(prepared, (1.5, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 12.0)
        mesh = self.fill.fill(prepared, (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0)

    def test_dense_curved_loop_retains_shape_and_dense_lines_simplify(self) -> None:
        count: int = 2000
        index: int
        path: list[Vec3] = []
        for index in range(count + 1):
            path.append((math.cos(math.tau * index / count), math.sin(math.tau * index / count), 5.0))
        prepared: Prepared = self.fill.prepare([path])
        mesh: Mesh | None = self.fill.fill(prepared, (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertGreater(prepared.point_count, 100)
        self.assertAlmostEqual(self.area(mesh), math.pi, places=4)
        line: list[Vec3] = []
        for index in range(count + 1):
            line.append((float(index) / count, 0, 5))
        self.assertEqual(self.fill.prepare([line]).point_count, 2)

    def test_nearest_plane_wins_and_nonplanar_loop_is_rejected(self) -> None:
        paths: list[list[Vec3]] = [self.square(z=3), self.square(z=6)]
        prepared: Prepared = self.fill.prepare(paths)
        self.assertFalse(any(plane.ready for plane in prepared.planes))
        mesh: Mesh | None = self.fill.fill(prepared, (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertTrue(all(abs(point[2] - 6.0) < 1e-7 for point in mesh.points))
        self.assertEqual(sum(plane.ready for plane in prepared.planes), 1)
        paths = [self.square()]
        paths[0][2] = (1, 1, 6.0)
        self.assertIsNone(self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1)))

    def test_slightly_warped_strokes_fill_on_a_shared_plane_at_different_scales(self) -> None:
        scale: float
        for scale in (0.01, 1.0, 100.0):
            path: list[Vec3] = []
            x: float
            y: float
            z: float
            for x, y, z in ((-1, -1, 0.008), (1, -1, -0.005), (1, 1, 0.006), (-1, 1, -0.007)):
                path.append(
                    (
                        4 + scale * (math.cos(0.6) * x + math.sin(0.6) * z),
                        -7 + scale * y,
                        5 + scale * (-math.sin(0.6) * x + math.cos(0.6) * z),
                    )
                )
            original: list[Vec3] = path[:]
            prepared: Prepared = self.fill.prepare([path])
            direction: Vec3 = (-math.sin(0.6), 0.0, -math.cos(0.6))
            origin: Vec3 = (4 + math.sin(0.6) * scale * 8, -7, 5 + math.cos(0.6) * scale * 8)
            mesh: Mesh | None = self.fill.fill(prepared, origin, direction)
            assert mesh is not None, scale
            self.assertAlmostEqual(self.area(mesh) / (scale * scale), 4.0, delta=0.001)
            self.assertEqual(path, original)
            self.assertIs(mesh, self.fill.fill(prepared, origin, direction))
            point: Vec3
            for point in mesh.points:
                local_z: float = math.sin(0.6) * (point[0] - 4) + math.cos(0.6) * (point[2] - 5)
                self.assertLess(abs(local_z), 0.02 * scale)

    def test_separate_uneven_edges_snap_small_junction_errors(self) -> None:
        paths: list[list[Vec3]] = [
            [(-1, -1, 5.006), (1, -1, 5.006)],
            [(1.001, -1, 4.994), (1.001, 1, 4.994)],
            [(1, 1.001, 5.008), (-1, 1.001, 5.008)],
            [(-1.001, 1, 4.995), (-1.001, -1, 4.995)],
        ]
        original: list[list[Vec3]] = []
        path: list[Vec3]
        for path in paths:
            original.append(path[:])
        mesh: Mesh | None = self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0, delta=0.02)
        self.assertEqual(paths, original)
        point: Vec3
        for point in mesh.points:
            self.assertLess(abs(point[2] - 5.0), 0.02)

    def test_short_connector_does_not_shrink_boundary_planarity_tolerance(self) -> None:
        source: list[list[Vec3]] = [
            [(0, 0, 5), (2, 0, 5)],
            [(2, 0, 5), (2, 2, 5)],
            [(2, 2, 5), (0.02, 2, 5)],
            [(0.02, 2, 5.001), (0, 2, 5.001)],
            [(0, 2, 5), (0, 0, 5)],
        ]
        reverse_order: bool
        reverse_points: bool
        for reverse_order in (False, True):
            for reverse_points in (False, True):
                paths: list[list[Vec3]] = []
                path: list[Vec3]
                for path in source:
                    paths.append(list(reversed(path)) if reverse_points else path[:])
                if reverse_order:
                    paths.reverse()
                original: list[list[Vec3]] = []
                for path in paths:
                    original.append(path[:])
                mesh: Mesh | None = self.fill.fill(self.fill.prepare(paths), (1, 1, 9), (0, 0, -1))
                assert mesh is not None, (reverse_order, reverse_points)
                self.assertAlmostEqual(self.area(mesh), 4.0, delta=0.001)
                self.assertEqual(paths, original)
                point: Vec3
                for point in mesh.points:
                    self.assertLess(abs(point[2] - 5.0), 0.002)

    def test_small_depth_offset_of_one_edge_still_fills(self) -> None:
        paths: list[list[Vec3]] = [
            [(-1, -1, 5), (1, -1, 5)],
            [(1, -1, 5.03), (1, 1, 5.03)],
            [(1, 1, 5), (-1, 1, 5)],
            [(-1, 1, 5), (-1, -1, 5)],
        ]
        mesh: Mesh | None = self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0, delta=0.01)
        point: Vec3
        for point in mesh.points:
            self.assertLess(abs(point[2] - 5.0), 0.05)

    def test_wavy_hand_drawn_loop_allows_visible_planarity_error(self) -> None:
        amplitude: float
        for amplitude in (0.02, 0.04):
            path: list[Vec3] = []
            index: int
            for index in range(129):
                angle: float = math.tau * index / 128
                path.append((math.cos(angle), math.sin(angle), 5.0 + amplitude * math.sin(3.0 * angle)))
            original: list[Vec3] = path[:]
            mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), (0, 0, 9), (0, 0, -1))
            assert mesh is not None, amplitude
            self.assertAlmostEqual(self.area(mesh), math.pi, delta=0.002)
            self.assertEqual(path, original)
            point: Vec3
            for point in mesh.points:
                self.assertAlmostEqual(point[2], 5.0, places=6)

    def test_near_planar_holes_preserve_their_empty_interior(self) -> None:
        paths: list[list[Vec3]] = [self.square(2), self.square(1)]
        paths[0][1] = (2, -2, 5.012)
        paths[0][3] = (-2, 2, 4.99)
        paths[1][0] = paths[1][-1] = (-1, -1, 5.004)
        paths[1][2] = (1, 1, 4.996)
        mesh: Mesh | None = self.fill.fill(self.fill.prepare(paths), (1.5, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 12.0, delta=0.01)
        face: tuple[int, int, int]
        for face in mesh.faces:
            center_x: float = 0.0
            center_y: float = 0.0
            index: int
            for index in face:
                center_x += mesh.points[index][0] / 3.0
                center_y += mesh.points[index][1] / 3.0
            self.assertFalse(abs(center_x) < 0.99 and abs(center_y) < 0.99)

    def test_unrelated_large_curve_does_not_expand_planarity_tolerance(self) -> None:
        warped: list[Vec3] = self.square()
        warped[2] = (1, 1, 5.8)
        paths: list[list[Vec3]] = [warped, [(-1000, -1000, 50), (1000, 1000, 50)]]
        self.assertIsNone(self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1)))

    def test_connected_three_dimensional_frame_keeps_individual_fill_planes(self) -> None:
        paths: list[list[Vec3]] = []
        z: float
        index: int
        for z in (5.0, 7.0):
            square: list[Vec3] = self.square(z=z)
            for index in range(4):
                paths.append(square[index : index + 2])
        x: float
        y: float
        for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            paths.append([(x, y, 5), (x, y, 7)])
        prepared: Prepared = self.fill.prepare(paths)
        top: Mesh | None = self.fill.fill(prepared, (0, 0, 10), (0, 0, -1))
        assert top is not None
        self.assertAlmostEqual(self.area(top), 4.0)
        point: Vec3
        for point in top.points:
            self.assertAlmostEqual(point[2], 7.0)
        side: Mesh | None = self.fill.fill(prepared, (5, 0, 6), (-1, 0, 0))
        assert side is not None
        self.assertAlmostEqual(self.area(side), 4.0)
        for point in side.points:
            self.assertAlmostEqual(point[0], 1.0)

    def test_drawing_tolerance_preserves_small_boundary_details(self) -> None:
        path: list[Vec3] = [
            (-1, -1, 5),
            (1, -1, 5),
            (1, 1, 5),
            (0.005, 1, 5),
            (0.005, 0.5, 5),
            (0, 0.5, 5),
            (0, 1, 5),
            (-1, 1, 5),
            (-1, -1, 5),
        ]
        mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), (0.5, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0 - 0.005 * 0.5, places=6)

    def test_large_offsets_and_concave_boundaries(self) -> None:
        local: list[Vec3] = [
            (0, 0, 5),
            (4, 0, 5),
            (4, 4, 5),
            (3, 4, 5),
            (3, 1, 5),
            (1, 1, 5),
            (1, 4, 5),
            (0, 4, 5),
            (0, 0, 5),
        ]
        point: Vec3
        path: list[Vec3] = []
        for point in local:
            path.append((point[0] + 1e6, point[1] - 1e6, point[2] + 1e6))
        mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), (1e6 + 2, -1e6 + 0.5, 1e6 + 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 10.0)

    def test_hole_connected_by_stem_and_float_noise(self) -> None:
        paths: list[list[Vec3]] = [self.square(2), self.square(1), [(-2, -2, 5), (-1, -1, 5)]]
        mesh: Mesh | None = self.fill.fill(self.fill.prepare(paths), (1.5, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 12.0)
        paths = [[(-1, -1, 5), (0, -1, 5.0000001), (1, -1, 5), (1, 1, 5.0000003), (-1, 1, 5), (-1, -1, 5)]]
        mesh = self.fill.fill(self.fill.prepare(paths), (0, 0, 9), (0, 0, -1))
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), 4.0, places=5)

    def test_float32_tilted_stroke_fills_with_or_without_endpoint_crossing(self) -> None:
        def world(x: float, y: float) -> Vec3:
            values: list[float] = [4.0 + math.cos(0.6) * x, -7.0 + y, 5.0 - math.sin(0.6) * x]
            rounded: list[float] = []
            value: float
            for value in values:
                rounded.append(float(unpack("f", pack("f", value))[0]))
            return rounded[0], rounded[1], rounded[2]

        path: list[Vec3] = []
        index: int
        for index in range(80):
            angle: float = math.tau * index / 80
            path.append(world(math.cos(angle), math.sin(angle)))
        path.extend((world(1.03, 0.0292), world(0.97, -0.0309)))
        origin: Vec3 = (4.0 + math.sin(0.6) * 8.0, -7.0, 5.0 + math.cos(0.6) * 8.0)
        direction: Vec3 = (-math.sin(0.6), 0.0, -math.cos(0.6))
        mesh: Mesh | None = self.fill.fill(self.fill.prepare([path]), origin, direction)
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), math.pi, places=2)
        path[-1] = world(0.96, -0.0309)
        mesh = self.fill.fill(self.fill.prepare([path]), origin, direction)
        assert mesh is not None
        self.assertAlmostEqual(self.area(mesh), math.pi, places=2)


if __name__ == "__main__":
    unittest.main()
