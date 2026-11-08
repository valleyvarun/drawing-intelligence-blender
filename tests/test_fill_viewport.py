"""Translucent fill visibility, cache and lifecycle regressions without Blender."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

ROOT: Path = Path(__file__).resolve().parents[1]


@dataclass
class Point:
    x: float
    y: float
    z: float

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y, self.z + other.z)

    def __mul__(self, scale: float) -> Point:
        return Point(self.x * scale, self.y * scale, self.z * scale)


@dataclass
class Transform:
    offset: float = 0.0

    def __matmul__(self, point: Point) -> Point:
        return Point(point.x, point.y, point.z + self.offset)


@dataclass
class Batch:
    name: str
    drawn: list[str]
    fail: bool = False

    def draw(self, _shader: object) -> None:
        if self.fail:
            raise RuntimeError("Invalid test GPU batch")
        self.drawn.append(self.name)


def vector(values: tuple[float, float, float]) -> Point:
    return Point(*values)


def identity(transform: Transform) -> Transform:
    return transform


def persistent(callback: Callable[..., object]) -> Callable[..., object]:
    return callback


def load_fills(blender: ModuleType) -> ModuleType:
    state: ModuleType = ModuleType("_fill_viewport.core.state")
    setattr(state, "Vec3", tuple[float, float, float])
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_fill_viewport.viewport.fills", ROOT / "app/drawing_app/viewport/fills.py"
    )
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {blender.__name__: blender, state.__name__: state, spec.name: result}):
        spec.loader.exec_module(result)
    return result


class FillViewportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bpy: MagicMock = MagicMock()
        self.gpu: MagicMock = MagicMock()
        self.builder: MagicMock = MagicMock()
        self.objects: dict[str, MagicMock] = {}
        self.drawn: list[str] = []
        self.handlers: SimpleNamespace = SimpleNamespace(
            persistent=persistent,
            depsgraph_update_post=[],
            undo_post=[],
            redo_post=[],
            load_post=[],
            save_pre=[],
            save_post=[],
            save_post_fail=[],
        )
        self.bpy.app.handlers = self.handlers
        self.scene: SimpleNamespace = SimpleNamespace(objects=self.objects.values())
        self.bpy.context.scene = self.scene
        self.bpy.context.view_layer.objects = self.objects
        self.bpy.context.space_data = SimpleNamespace(type="VIEW_3D", shading=SimpleNamespace(type="SOLID"))
        self.gpu.state.depth_test_get.return_value = "NONE"
        self.gpu.state.depth_mask_get.return_value = True
        self.gpu.state.blend_get.return_value = "NONE"
        self.gpu.matrix.get_model_view_matrix.return_value.__matmul__.side_effect = identity
        blender: ModuleType = ModuleType("_fill_viewport.core.blender")
        setattr(blender, "bpy", self.bpy)
        setattr(blender, "gpu", self.gpu)
        setattr(blender, "batch_for_shader", self.builder)
        setattr(blender, "Vector", vector)
        self.fills: ModuleType = load_fills(blender)
        self.fills.install()

    def object(
        self,
        name: str,
        alpha: float = 0.5,
        depth: float = -3.0,
        display: str = "TEXTURED",
        instance: str = "NONE",
        show_self: bool = True,
    ) -> MagicMock:
        obj: MagicMock = MagicMock()
        obj.name = name
        obj.type = "MESH"
        obj.get.return_value = "fill"
        obj.as_pointer.return_value = len(self.objects) + 1
        obj.data.as_pointer.return_value = 100 + len(self.objects)
        obj.active_material.diffuse_color = (0.2, 0.4, 0.6, alpha)
        obj.color = (1.0, 0.0, 0.0, 1.0)
        obj.display_type = display
        obj.instance_type = instance
        obj.show_instancer_for_viewport = show_self
        obj.bound_box = ((0.0, 0.0, depth),) * 8
        obj.matrix_world = Transform()
        obj.visible_get.return_value = True
        obj.data.vertices = [SimpleNamespace(co=Point(0.0, 0.0, depth))] * 3
        obj.data.loop_triangles = [SimpleNamespace(vertices=(0, 1, 2))]
        self.objects[name] = obj
        self.fills.prepare(obj)
        return obj

    def track_draw(self, obj: MagicMock) -> None:
        self.fills._FILLS[obj.as_pointer()].batch = Batch(str(obj.name), self.drawn)

    def update(self, target: MagicMock | None = None, geometry: bool = True) -> None:
        updates: list[SimpleNamespace] = []
        if target is not None:
            updates.append(SimpleNamespace(is_updated_geometry=geometry, id=SimpleNamespace(original=target)))
        self.fills._update(self.scene, SimpleNamespace(updates=updates))

    def test_material_opacity_controls_zero_and_full_opacity(self) -> None:
        invisible: MagicMock = self.object("Invisible", alpha=0.0)
        opaque: MagicMock = self.object("Opaque", alpha=1.0)
        self.assertEqual(invisible.display_type, "TEXTURED")
        self.assertEqual(invisible.instance_type, "VERTS")
        self.assertFalse(invisible.show_instancer_for_viewport)
        self.assertEqual(opaque.display_type, "TEXTURED")
        self.assertNotIn(opaque.as_pointer(), self.fills._FILLS)
        self.fills._draw()
        self.builder.assert_not_called()
        invisible.active_material.diffuse_color = (0.2, 0.4, 0.6, 1.0)
        self.update()
        self.assertEqual(invisible.display_type, "TEXTURED")
        self.assertEqual(invisible.instance_type, "NONE")
        self.assertTrue(invisible.show_instancer_for_viewport)
        self.assertFalse(self.fills._FILLS)

    def test_save_restores_native_geometry_until_success_or_failure(self) -> None:
        obj: MagicMock = self.object("Fill", display="SOLID")
        callback: Callable[..., None]
        for callback in (self.handlers.save_post[0], self.handlers.save_post_fail[0]):
            self.handlers.save_pre[0](None)
            self.assertEqual(obj.display_type, "SOLID")
            self.assertEqual(obj.instance_type, "NONE")
            self.assertTrue(obj.show_instancer_for_viewport)
            self.update()
            self.assertEqual(obj.display_type, "SOLID")
            self.fills._draw()
            self.builder.assert_not_called()
            callback(None)
            self.assertEqual(obj.display_type, "SOLID")
            self.assertEqual(obj.instance_type, "VERTS")
            self.assertFalse(obj.show_instancer_for_viewport)

    def test_install_remove_are_idempotent_and_restore_previous_display(self) -> None:
        obj: MagicMock = self.object("Fill", display="SOLID")
        self.fills.install()
        self.bpy.types.SpaceView3D.draw_handler_add.assert_called_once()
        self.assertEqual(len(self.handlers.depsgraph_update_post), 1)
        self.fills.remove()
        self.fills.remove()
        self.assertEqual(obj.display_type, "SOLID")
        self.assertEqual(obj.instance_type, "NONE")
        self.assertTrue(obj.show_instancer_for_viewport)
        self.assertFalse(self.fills._FILLS)
        self.assertFalse(self.handlers.depsgraph_update_post)
        self.bpy.types.SpaceView3D.draw_handler_remove.assert_called_once()
        self.fills.prepare(obj)
        self.assertEqual(obj.display_type, "SOLID")

    def test_history_discards_cached_batches_and_accepts_restored_hidden_mesh(self) -> None:
        obj: MagicMock = self.object("Fill")
        callback: Callable[..., None]
        for callback in (self.handlers.undo_post[0], self.handlers.redo_post[0], self.handlers.load_post[0]):
            self.track_draw(obj)
            callback(None)
            self.assertEqual(obj.display_type, "TEXTURED")
            self.assertEqual(obj.instance_type, "VERTS")
            self.assertFalse(obj.show_instancer_for_viewport)
            self.assertIsNone(self.fills._FILLS[obj.as_pointer()].batch)
        self.fills._FILLS.clear()
        self.handlers.undo_post[0](None)
        self.fills.remove()
        self.assertEqual(obj.display_type, "TEXTURED")
        self.assertEqual(obj.instance_type, "NONE")
        self.assertTrue(obj.show_instancer_for_viewport)

    def test_original_instancer_flags_are_restored(self) -> None:
        obj: MagicMock = self.object("Fill", instance="FACES", show_self=False)
        self.assertEqual(obj.instance_type, "VERTS")
        self.fills.remove()
        self.assertEqual(obj.instance_type, "FACES")
        self.assertFalse(obj.show_instancer_for_viewport)

    def test_deleted_datablocks_are_discarded_without_stopping_other_fills(self) -> None:
        deleted: MagicMock = self.object("Deleted")
        remaining: MagicMock = self.object("Remaining")
        self.track_draw(remaining)
        del self.objects[deleted.name]
        with patch.object(type(deleted), "data", new_callable=PropertyMock, create=True) as data:
            data.side_effect = ReferenceError("Removed Blender object")
            self.update()
        self.assertNotIn(deleted.as_pointer(), self.fills._FILLS)
        self.fills._draw()
        self.assertEqual(self.drawn, ["Remaining"])

    def test_geometry_changes_invalidate_only_affected_batches(self) -> None:
        changed: MagicMock = self.object("Changed")
        other: MagicMock = self.object("Other")
        self.track_draw(changed)
        self.track_draw(other)
        self.update(changed, geometry=False)
        self.assertIsNotNone(self.fills._FILLS[changed.as_pointer()].batch)
        self.update(changed)
        self.assertIsNone(self.fills._FILLS[changed.as_pointer()].batch)
        self.assertIsNotNone(self.fills._FILLS[other.as_pointer()].batch)
        self.track_draw(changed)
        self.update(changed.data)
        self.assertIsNone(self.fills._FILLS[changed.as_pointer()].batch)
        self.assertIsNotNone(self.fills._FILLS[other.as_pointer()].batch)

    def test_cached_geometry_is_reused_for_view_and_object_movement(self) -> None:
        obj: MagicMock = self.object("Fill")
        self.fills._draw()
        obj.matrix_world.offset = -8.0
        self.update(obj, geometry=False)
        self.fills._draw()
        self.builder.assert_called_once()
        obj.data.calc_loop_triangles.assert_called_once()
        self.assertEqual(self.gpu.matrix.multiply_matrix.call_count, 2)
        self.update(obj.data)
        self.fills._draw()
        self.assertEqual(self.builder.call_count, 2)

    def test_sort_uses_transformed_bounds_instead_of_object_origins(self) -> None:
        near: MagicMock = self.object("Near", depth=-2.0)
        far: MagicMock = self.object("Far", depth=-8.0)
        self.track_draw(near)
        self.track_draw(far)
        self.fills._draw()
        self.assertEqual(self.drawn, ["Far", "Near"])
        self.drawn.clear()
        near.matrix_world.offset = -10.0
        self.fills._draw()
        self.assertEqual(self.drawn, ["Near", "Far"])

    def test_hidden_collections_and_other_view_layers_do_not_draw(self) -> None:
        hidden: MagicMock = self.object("Hidden")
        excluded: MagicMock = self.object("Excluded")
        visible: MagicMock = self.object("Visible")
        hidden.visible_get.return_value = False
        self.bpy.context.view_layer.objects = {hidden.name: hidden, visible.name: visible}
        obj: MagicMock
        for obj in (hidden, excluded, visible):
            self.track_draw(obj)
        self.fills._draw()
        self.assertEqual(self.drawn, ["Visible"])
        hidden.visible_get.assert_called_once_with(view_layer=self.bpy.context.view_layer)

    def test_gpu_state_is_restored_after_draw_error(self) -> None:
        obj: MagicMock = self.object("Fill")
        self.fills._FILLS[obj.as_pointer()].batch = Batch(str(obj.name), self.drawn, fail=True)
        with patch("builtins.print"):
            self.fills._draw()
        self.gpu.state.depth_test_set.assert_any_call("LESS_EQUAL")
        self.gpu.state.depth_mask_set.assert_any_call(False)
        self.gpu.state.blend_set.assert_any_call("ALPHA")
        self.gpu.state.depth_test_set.assert_called_with("NONE")
        self.gpu.state.depth_mask_set.assert_called_with(True)
        self.gpu.state.blend_set.assert_called_with("NONE")

    def test_selection_exposes_only_visible_nonzero_fill_faces(self) -> None:
        visible: MagicMock = self.object("Visible")
        hidden: MagicMock = self.object("Hidden")
        zero: MagicMock = self.object("Zero", alpha=0.0)
        hidden.visible_get.return_value = False
        has_fills: bool
        with self.fills.selection_surfaces() as has_fills:
            self.assertTrue(has_fills)
            self.assertTrue(visible.show_instancer_for_viewport)
            self.assertFalse(hidden.show_instancer_for_viewport)
            self.assertFalse(zero.show_instancer_for_viewport)
            self.update()
            self.assertTrue(visible.show_instancer_for_viewport)
            self.fills._draw()
            self.builder.assert_not_called()
        self.assertFalse(visible.show_instancer_for_viewport)
        self.assertFalse(self.fills._SELECTING)
        self.assertEqual(self.bpy.context.view_layer.update.call_count, 2)

    def test_failed_selection_restores_native_suppression(self) -> None:
        obj: MagicMock = self.object("Visible")
        with self.assertRaisesRegex(RuntimeError, "Selection failed"):
            with self.fills.selection_surfaces():
                raise RuntimeError("Selection failed")
        self.assertFalse(obj.show_instancer_for_viewport)
        self.assertFalse(self.fills._SELECTING)
        obj.visible_get.return_value = False
        has_fills: bool
        with self.fills.selection_surfaces() as has_fills:
            self.assertFalse(has_fills)


if __name__ == "__main__":
    unittest.main()
