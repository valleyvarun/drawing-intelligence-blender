"""Physical pipe trim contacts and interpolation without Blender."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from drawing_app.core.state import Vec3
    from drawing_app.tools.geometry import Sample
    from drawing_app.tools.pipe_geometry import Network

ROOT: Path = Path(__file__).resolve().parents[1]


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


class PipeEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        state: ModuleType = module("_pipe_test.core.state", "app/drawing_app/core/state.py")
        with patch.dict(sys.modules, {state.__name__: state}):
            self.geometry: ModuleType = module("_pipe_test.tools.geometry", "app/drawing_app/tools/geometry.py")
            with patch.dict(sys.modules, {self.geometry.__name__: self.geometry}):
                self.edit: ModuleType = module(
                    "_pipe_test.tools.pipe_geometry", "app/drawing_app/tools/pipe_geometry.py"
                )

    def path(self, points: list[Vec3], radius: float = 0.02) -> list[Sample]:
        samples: list[Sample] = []
        point: Vec3
        for point in points:
            samples.append(self.geometry.Sample(point, radius, 0.7, 0.6))
        return samples

    def test_trim_uses_physical_contacts_and_preserves_samples(self) -> None:
        paths: list[list[Sample]] = [
            self.path([(-3, 0, 4), (3, 0, 4)]),
            self.path([(-1, -1, 4), (-1, 1, 4)]),
            self.path([(1, -1, 4), (1, 1, 4)]),
        ]
        parts: list[list[Sample]] | None = self.edit.trim_parts(self.edit.build_network(paths), 0, 0.5)
        assert parts is not None
        self.assertEqual(len(parts), 2)
        self.assertAlmostEqual(parts[0][-1].position[0], -1.0)
        self.assertAlmostEqual(parts[1][0].position[0], 1.0)
        self.assertAlmostEqual(parts[1][0].opacity, 0.6)
        self.assertAlmostEqual(parts[1][0].radius, 0.02)
        paths[1] = self.path([(-1, -1, 5), (-1, 1, 5)])
        paths[2] = self.path([(1, -1, 5), (1, 1, 5)])
        self.assertIsNone(self.edit.trim_parts(self.edit.build_network(paths), 0, 0.5))

    def test_tube_thickness_counts_as_contact_but_depth_separation_does_not(self) -> None:
        paths: list[list[Sample]] = [
            self.path([(-2, 0, 0), (2, 0, 0)], 0.1),
            self.path([(0, -1, 0.19), (0, 1, 0.19)], 0.1),
        ]
        self.assertEqual(len(self.edit.build_network(paths).junctions), 1)
        paths[1] = self.path([(0, -1, 0.21), (0, 1, 0.21)], 0.1)
        self.assertEqual(len(self.edit.build_network(paths).junctions), 0)

    def test_smooth_closed_stroke_has_no_neighbor_junctions(self) -> None:
        points: list[Vec3] = []
        index: int
        for index in range(301):
            angle: float = index * math.tau / 300
            points.append((math.cos(angle), math.sin(angle), 7.0))
        network: Network = self.edit.build_network([self.path(points, 0.04)])
        self.assertLessEqual(len(network.junctions), 1)


if __name__ == "__main__":
    unittest.main()
