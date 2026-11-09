"""The 3D Printer's height field: deposits, slumping, growth, floors, and the mesh, without Blender."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT: Path = Path(__file__).resolve().parents[1]


def load() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_print_field_test", ROOT / "app/drawing_app/tools/print_field.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HeightFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module: ModuleType = load()
        self.np: Any = self.module.np

    def field(self, side: int = 32) -> Any:
        """A field over flat ground at height 0, columns 1 cm wide, centred on the origin."""
        field: Any = self.module.HeightField.empty((0.0, 0.0), 0.01, side)
        field.base[:, :] = 0.0
        return field

    def test_a_particle_adds_its_own_height_under_its_footprint(self) -> None:
        field: Any = self.field()
        # One column wide: the whole cube goes into the column it lands in.
        field.land(self.np.array([0.005]), self.np.array([0.005]), 0.01, 0.01**3)
        self.assertAlmostEqual(float(field.height.max()), 0.01)
        self.assertEqual(int(field.solid().sum()), 1)
        # Three columns wide: a 3 cm cube covers 3 x 3 columns, each 3 cm higher.
        field = self.field()
        field.land(self.np.array([0.005, 0.005]), self.np.array([0.005, 0.005]), 0.03, 0.03**3)
        self.assertEqual(int(field.solid().sum()), 9)
        self.assertTrue(self.np.allclose(field.height[field.solid()], 0.06))
        self.assertAlmostEqual(field.volume(), 2 * 0.03**3)
        i: Any
        j: Any
        side: int
        i, j, side = field.footprint(0.005, 0.005, 0.03)
        self.assertEqual(side, 3)
        self.assertEqual(sorted(set(i.tolist())), [15, 16, 17])

    def test_deposits_add_up_in_the_same_column(self) -> None:
        field: Any = self.field()
        i: Any
        j: Any
        i, j = field.index(self.np.array([0.001, 0.002, 0.003]), self.np.array([0.001, 0.001, 0.004]))
        field.deposit(i, j, 0.02)
        self.assertAlmostEqual(float(field.height.max()), 0.06)
        self.assertAlmostEqual(field.volume(), 0.06 * 0.01 * 0.01)
        self.assertAlmostEqual(field.top(-0.005, -0.005, 0.005, 0.005), 0.06)

    def test_slumping_keeps_volume_and_reaches_the_angle_of_repose(self) -> None:
        field: Any = self.field()
        field.height[16, 16] = 0.2
        volume: float = field.volume()
        _step: int
        for _step in range(600):
            field.relax(4)
        self.assertAlmostEqual(field.volume(), volume, places=12)
        surface: Any = field.base + field.height
        steepest: float = max(
            float(self.np.abs(self.np.diff(surface, axis=0)).max()),
            float(self.np.abs(self.np.diff(surface, axis=1)).max()),
        )
        # Within a small tolerance of the repose step, from a 20 cm spike to a mound.
        self.assertLessEqual(steepest, self.module.REPOSE * field.cell * 1.05)
        self.assertLess(float(field.height.max()), 0.05)
        self.assertGreaterEqual(int(field.solid().sum()), 25)

    def test_mounds_are_round_rather_than_square_pyramids(self) -> None:
        field: Any = self.field(64)
        # 2,000 cm³ in one column: a cone at the angle of repose is about 14 columns in radius.
        field.height[32, 32] = 20.0
        _step: int
        for _step in range(1500):
            field.relax(4)
        solid: Any = field.solid()
        reach: int = 0
        while solid[32 + reach + 1, 32]:
            reach += 1
        diagonal: int = 0
        while solid[32 + diagonal + 1, 32 + diagonal + 1]:
            diagonal += 1
        # A pile that slid only along the axes would be a diamond, reaching about 71% as far diagonally.
        self.assertGreater(reach, 10)
        self.assertGreater(diagonal * 2**0.5 / reach, 0.85)

    def test_material_does_not_flow_onto_unknown_floors(self) -> None:
        field: Any = self.field()
        field.base[17, 16] = self.module.UNKNOWN
        field.height[16, 16] = 0.1
        field.relax(8)
        self.assertEqual(float(field.height[17, 16]), 0.0)
        self.assertGreater(float(field.height[15, 16]), 0.0)

    def test_floors_are_looked_up_beside_material_from_just_above_it(self) -> None:
        field: Any = self.module.HeightField.empty((0.0, 0.0), 0.01, 16)
        field.base[8, 8] = 0.5
        field.height[8, 8] = 0.1
        i: Any
        j: Any
        start: Any
        i, j, start = field.floors_needed(100)
        self.assertEqual(len(i), 8)
        self.assertTrue(self.np.allclose(start, 0.61))
        self.assertNotIn((8, 8), set(zip(i.tolist(), j.tolist(), strict=True)))

    def test_growing_keeps_material_in_place(self) -> None:
        field: Any = self.field(16)
        field.height[2, 3] = 0.05
        x: Any
        y: Any
        x, y = field.center(2, 3)
        field.cover(0.5, 0.5, 0.6, 0.6)
        i: Any
        j: Any
        i, j = field.index(x, y)
        self.assertAlmostEqual(float(field.height[int(i), int(j)]), 0.05)
        self.assertTrue(bool(field.inside(*field.index(0.6, 0.6))))
        # New columns (here toward +x and +y) start with unknown floors; old ones keep theirs.
        self.assertEqual(float(field.base[-1, -1]), self.module.UNKNOWN)
        self.assertEqual(float(field.base[0, 0]), 0.0)

    def test_large_extents_coarsen_and_keep_volume(self) -> None:
        field: Any = self.field(64)
        field.height[30:34, 30:34] = 0.04
        volume: float = field.volume()
        field.cover(-5.0, -5.0, 5.0, 5.0)
        self.assertLessEqual(max(field.shape), self.module.MAX_SIDE)
        self.assertGreater(field.cell, 0.01)
        self.assertAlmostEqual(field.volume(), volume, places=12)

    def test_mesh_has_upward_quads_with_a_skirt_down_to_the_floor(self) -> None:
        field: Any = self.field()
        field.height[10:13, 10:12] = 0.03
        vertices: Any
        loops: Any
        starts: Any
        vertices, loops, starts = field.mesh()
        polygons: list[Any] = [
            loops[start : starts[index + 1] if index + 1 < len(starts) else len(loops)]
            for index, start in enumerate(starts.tolist())
        ]
        sizes: list[int] = sorted(len(polygon) for polygon in polygons)
        # The 3 x 2 filled columns and a skirt around them; its four diagonal corners are triangles.
        self.assertEqual(sizes, [3] * 4 + [4] * 16)
        self.assertEqual(len(vertices), 6 * 5 - 4)
        polygon: Any
        for polygon in polygons:
            points: Any = vertices[polygon]
            shifted: Any = self.np.roll(points, -1, axis=0)
            area: float = float((points[:, 0] * shifted[:, 1] - shifted[:, 0] * points[:, 1]).sum())
            self.assertGreater(area, 0.0)
            # Every polygon touches the material, so none lies flat on the floor (which could flicker).
            self.assertGreater(float(points[:, 2].max()), 0.0)
        self.assertAlmostEqual(float(vertices[:, 2].max()), 0.03, places=6)
        # The 4 x 3 corners of the filled columns are raised; the outline's 14 corners rest on the floor.
        self.assertEqual(int((vertices[:, 2] > 1e-9).sum()), 12)
        self.assertTrue(self.np.allclose(self.np.sort(vertices[:, 2])[:14], 0.0))

    def test_saved_grid_round_trip(self) -> None:
        field: Any = self.field(8)
        field.height[3, 4] = 0.07
        copy: Any = self.module.HeightField.restore(field.grid(), field.height.ravel(), field.base.ravel())
        self.assertIsNotNone(copy)
        self.assertEqual(copy.shape, (8, 8))
        self.assertAlmostEqual(float(copy.height[3, 4]), 0.07)
        self.assertIsNone(self.module.HeightField.restore([0, 0, 0, 8, 8], field.height.ravel(), field.base.ravel()))


if __name__ == "__main__":
    unittest.main()
