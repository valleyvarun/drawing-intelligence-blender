"""Incremental stroke geometry and upload scheduling without starting Blender."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from drawing_app.tools.drawing import DrawingController
    from drawing_app.tools.geometry import PipeCache, Sample

ROOT: Path = Path(__file__).resolve().parents[1]


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


def load_geometry() -> ModuleType:
    state: ModuleType = ModuleType("_stroke_test.core.state")
    setattr(state, "Vec3", tuple[float, float, float])
    with patch.dict(sys.modules, {state.__name__: state}):
        return module("_stroke_test.tools.geometry", "app/drawing_app/tools/geometry.py")


class StrokeUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry: ModuleType = load_geometry()
        self.samples: list[Sample] = []
        index: int
        for index in range(90):
            self.samples.append(
                self.geometry.Sample(
                    (math.sin(index * 0.13), math.cos(index * 0.07), index * 0.03),
                    0.04 + index * 0.0001,
                    0.8,
                    0.4 + index * 0.003,
                )
            )

    def test_incremental_pipe_matches_full_geometry_with_pressure_and_turns(self) -> None:
        shape: str
        for shape in ("circle", "square", "triangle"):
            cache: PipeCache = self.geometry.PipeCache()
            count: int
            for count in (1, 2, 3, 15, 16, 45, 90):
                with self.subTest(shape=shape, samples=count):
                    self.assertEqual(
                        self.geometry.pipe_mesh(self.samples[:count], shape, cache),
                        self.geometry.pipe_mesh(self.samples[:count], shape),
                    )
            self.assertEqual(self.geometry.pipe_mesh([], shape, cache), ([], [], []))

    def test_pipe_cache_restarts_when_shape_or_stroke_changes(self) -> None:
        cache: PipeCache = self.geometry.PipeCache()
        self.geometry.pipe_mesh(self.samples, "circle", cache)
        self.assertEqual(
            self.geometry.pipe_mesh(self.samples, "triangle", cache), self.geometry.pipe_mesh(self.samples, "triangle")
        )
        self.assertEqual(
            self.geometry.pipe_mesh(self.samples[:10], "triangle", cache),
            self.geometry.pipe_mesh(self.samples[:10], "triangle"),
        )

    def test_pending_samples_flush_once_without_more_mouse_events(self) -> None:
        modules: dict[str, ModuleType] = {"_stroke_test.tools.geometry": self.geometry}
        name: str
        for name in ("core.blender", "core.state", "tools", "tools.canvas", "tools.preview", "tools.surface"):
            modules["_stroke_test." + name] = ModuleType("_stroke_test." + name)
        state: ModuleType = modules["_stroke_test.core.state"]
        setattr(state, "Vec3", tuple[float, float, float])
        setattr(state, "get_state", MagicMock(return_value=SimpleNamespace(fps_limit=60)))
        for name in ("AppState", "Brush"):
            setattr(state, name, MagicMock())
        blender: ModuleType = modules["_stroke_test.core.blender"]
        setattr(blender, "Vector", MagicMock())
        setattr(blender, "bpy", MagicMock())
        setattr(modules["_stroke_test.tools"], "canvas", modules["_stroke_test.tools.canvas"])
        setattr(modules["_stroke_test.tools"], "layers", MagicMock())
        setattr(modules["_stroke_test.tools.canvas"], "Hit", MagicMock())
        setattr(modules["_stroke_test.tools.canvas"], "place_cursor", MagicMock())
        setattr(modules["_stroke_test.tools.preview"], "BrushPreview", MagicMock())
        setattr(modules["_stroke_test.tools.surface"], "PaintSurface", MagicMock())
        with patch.dict(sys.modules, modules):
            drawing: ModuleType = module("_stroke_test.tools.drawing", "app/drawing_app/tools/drawing.py")
            controller: DrawingController = cast("DrawingController", drawing.DrawingController())
        controller._object = MagicMock()
        controller._area = MagicMock()
        controller._samples = list(self.samples)
        upload: MagicMock
        with patch.object(drawing, "write_mesh") as upload, patch.object(drawing.time, "monotonic", return_value=1.0):
            controller.tick()
            controller.tick()
            controller._upload()
            upload.assert_called_once()
        controller._samples.append(self.samples[-1])
        with patch.object(drawing, "write_mesh") as upload, patch.object(drawing.time, "monotonic", return_value=1.02):
            controller.tick()
            upload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
