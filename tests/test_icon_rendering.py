"""Icon blend-state regressions without starting Blender or using a GPU."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

ROOT: Path = Path(__file__).resolve().parents[1]


class IconRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gpu: MagicMock = MagicMock()
        self.blf: MagicMock = MagicMock()
        self.bpy: MagicMock = MagicMock()
        self.batch: MagicMock = MagicMock()
        self.blend: str = "ALPHA"
        self.gpu.state.blend_get.side_effect = self.get_blend
        self.gpu.state.blend_set.side_effect = self.set_blend
        blender: ModuleType = ModuleType("_icon_test.core.blender")
        setattr(blender, "gpu", self.gpu)
        setattr(blender, "blf", self.blf)
        setattr(blender, "bpy", self.bpy)
        setattr(blender, "batch_for_shader", MagicMock(return_value=self.batch))
        spec: ModuleSpec | None = importlib.util.spec_from_file_location(
            "_icon_test.ui.paint", ROOT / "app/drawing_app/ui/paint.py"
        )
        assert spec is not None and spec.loader is not None
        self.paint: ModuleType = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {blender.__name__: blender}):
            spec.loader.exec_module(self.paint)
        setattr(self.paint, "_FONT", 0)

    def get_blend(self) -> str:
        return self.blend

    def set_blend(self, mode: str) -> None:
        self.blend = mode

    def finish_text(self, _font: int, _label: str) -> None:
        self.blend = "NONE"

    def assert_alpha_draw(self, _shader: MagicMock) -> None:
        self.assertEqual(self.blend, "ALPHA")

    def assert_premultiplied_draw(self, _shader: MagicMock) -> None:
        self.assertEqual(self.blend, "ALPHA_PREMULT")

    def test_label_preserves_blending_for_following_icons(self) -> None:
        self.blf.draw.side_effect = self.finish_text
        self.paint.text("Drawing", 0, 0)
        self.assertEqual(self.blend, "ALPHA")
        self.blf.draw.assert_called_once()

    def test_all_raster_icons_draw_transparently_even_after_blending_is_disabled(self) -> None:
        name: str
        for name in ("paint", "pipe", "plane"):
            with self.subTest(icon=name):
                self.blend = "NONE"
                self.bpy.data.images.load.return_value.alpha_mode = "STRAIGHT"
                self.batch.draw.side_effect = self.assert_alpha_draw
                self.paint.icon(name, 0, 0, 32)
                self.assertEqual(self.blend, "NONE")

    def test_premultiplied_image_restores_blending_even_on_failure(self) -> None:
        self.bpy.data.images.load.return_value.alpha_mode = "PREMUL"
        self.batch.draw.side_effect = self.assert_premultiplied_draw
        self.paint.image("brush_sphere.png", 0, 0, 32)
        self.assertEqual(self.blend, "ALPHA")
        self.batch.draw.side_effect = RuntimeError("draw failed")
        with self.assertRaises(RuntimeError):
            self.paint.image("brush_sphere.png", 0, 0, 32)
        self.assertEqual(self.blend, "ALPHA")

    def test_grease_pencil_uses_original_geometry_instead_of_text(self) -> None:
        self.paint.icon("grease", 0, 0, 32)
        self.batch.draw.assert_called_once()
        self.blf.draw.assert_not_called()
        self.assertIn("grease", self.paint._ICON_BATCHES)


if __name__ == "__main__":
    unittest.main()
