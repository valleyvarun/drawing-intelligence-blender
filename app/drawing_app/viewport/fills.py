"""Draw translucent pipe fills after Grease Pencil without writing opaque depth."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeAlias

from ..core.blender import Vector, batch_for_shader, bpy, gpu
from ..core.state import Vec3

Color: TypeAlias = tuple[float, float, float, float]


@dataclass
class Fill:
    obj: Any
    instance: str
    show_self: bool
    batch: Any = None


_FILLS: dict[int, Fill] = {}
_HANDLER: Any = None
_SHADER: Any = None
_SAVING: bool = False
_SELECTING: bool = False
_LAST_ERROR: str = ""


def color(obj: Any) -> Color:
    material: Any = obj.active_material
    value: Any = material.diffuse_color if material is not None else obj.color
    return (float(value[0]), float(value[1]), float(value[2]), max(0.0, min(1.0, float(value[3]))))


def prepare(obj: Any) -> None:
    if _HANDLER is None or _SAVING or _SELECTING:
        return
    key: int = int(obj.as_pointer())
    fill: Fill | None = _FILLS.get(key)
    if obj.type != "MESH" or obj.get("drawing_kind") != "fill" or color(obj)[3] >= 1.0:
        if fill is not None:
            obj.instance_type = fill.instance
            obj.show_instancer_for_viewport = fill.show_self
            del _FILLS[key]
        return
    if fill is None:
        instance: str = str(obj.instance_type)
        show_self: bool = bool(obj.show_instancer_for_viewport)
        # Undo may restore a mesh hidden by this pass without its Python cache.
        if instance == "VERTS" and not show_self:
            instance, show_self = "NONE", True
        _FILLS[key] = Fill(obj, instance, show_self)
    # Empty instancing suppresses native faces and bounds while preserving ray casts.
    if obj.instance_type != "VERTS":
        obj.instance_type = "VERTS"
    if obj.show_instancer_for_viewport:
        obj.show_instancer_for_viewport = False


def _refresh(scene: Any) -> None:
    obj: Any
    for obj in scene.objects:
        if obj.type == "MESH":
            prepare(obj)


def _restore() -> None:
    fill: Fill
    for fill in _FILLS.values():
        try:
            fill.obj.instance_type = fill.instance
            fill.obj.show_instancer_for_viewport = fill.show_self
        except ReferenceError:
            pass


@bpy.app.handlers.persistent
def _update(scene: Any, depsgraph: Any) -> None:
    if _SAVING or _SELECTING:
        return
    changed: set[int] = set()
    update: Any
    for update in depsgraph.updates:
        if update.is_updated_geometry:
            changed.add(int(update.id.original.as_pointer()))
    key: int
    fill: Fill
    for key, fill in tuple(_FILLS.items()):
        try:
            if key in changed or int(fill.obj.data.as_pointer()) in changed:
                fill.batch = None
        except ReferenceError:
            del _FILLS[key]
    _refresh(scene)


@bpy.app.handlers.persistent
def _reset(_unused: Any) -> None:
    _restore()
    _FILLS.clear()
    _refresh(bpy.context.scene)


@bpy.app.handlers.persistent
def _save_pre(_unused: Any) -> None:
    global _SAVING
    _SAVING = True
    _restore()


@bpy.app.handlers.persistent
def _save_post(_unused: Any) -> None:
    global _SAVING
    _SAVING = False
    _refresh(bpy.context.scene)


def _batch(fill: Fill) -> Any:
    if fill.batch is None:
        mesh: Any = fill.obj.data
        mesh.calc_loop_triangles()
        points: list[Vec3] = []
        vertex: Any
        for vertex in mesh.vertices:
            points.append((float(vertex.co.x), float(vertex.co.y), float(vertex.co.z)))
        triangles: list[tuple[int, int, int]] = []
        triangle: Any
        for triangle in mesh.loop_triangles:
            triangles.append((int(triangle.vertices[0]), int(triangle.vertices[1]), int(triangle.vertices[2])))
        fill.batch = batch_for_shader(_SHADER, "TRIS", {"pos": points}, indices=triangles)
    return fill.batch


def _distance(item: tuple[float, Fill]) -> float:
    return item[0]


def _visible(obj: Any, view_layer: Any) -> bool:
    try:
        return obj.name in view_layer.objects and bool(obj.visible_get(view_layer=view_layer))
    except ReferenceError:
        return False


@contextmanager
def selection_surfaces(*, include_zero_opacity: bool = False) -> Iterator[bool]:
    """Expose fill faces only while Blender captures selection or surface depth."""
    global _SELECTING
    previous: bool = _SELECTING
    _SELECTING = True
    exposed: list[tuple[Any, bool]] = []
    view_layer: Any = bpy.context.view_layer
    fill: Fill
    obj: Any
    show_self: bool
    try:
        for fill in tuple(_FILLS.values()):
            obj = fill.obj
            if _visible(obj, view_layer) and (include_zero_opacity or color(obj)[3] > 0.0):
                exposed.append((obj, bool(obj.show_instancer_for_viewport)))
                obj.show_instancer_for_viewport = True
        if exposed:
            view_layer.update()
        yield bool(exposed)
    finally:
        try:
            for obj, show_self in exposed:
                try:
                    obj.show_instancer_for_viewport = show_self
                except ReferenceError:
                    pass
            if exposed:
                view_layer.update()
        finally:
            _SELECTING = previous


def _draw() -> None:
    global _SHADER, _LAST_ERROR
    context: Any = bpy.context
    space: Any = context.space_data
    if _SAVING or _SELECTING or space is None or space.type != "VIEW_3D" or space.shading.type != "SOLID":
        return
    depth: str = str(gpu.state.depth_test_get())
    mask: bool = bool(gpu.state.depth_mask_get())
    blend: str = str(gpu.state.blend_get())
    error: ReferenceError | RuntimeError | ValueError | TypeError | AttributeError
    try:
        if _SHADER is None:
            _SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
        view: Any = gpu.matrix.get_model_view_matrix()
        queue: list[tuple[float, Fill]] = []
        fill: Fill
        for fill in tuple(_FILLS.values()):
            obj: Any = fill.obj
            if not _visible(obj, context.view_layer):
                continue
            if not 0.0 < color(obj)[3] < 1.0:
                continue
            center: Any = (Vector(obj.bound_box[0]) + Vector(obj.bound_box[6])) * 0.5
            queue.append((float((view @ obj.matrix_world @ center).z), fill))
        queue.sort(key=_distance)
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set("ALPHA")
        _view_depth: float
        for _view_depth, fill in queue:
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(fill.obj.matrix_world)
                _SHADER.bind()
                _SHADER.uniform_float("color", color(fill.obj))
                _batch(fill).draw(_SHADER)
    except (ReferenceError, RuntimeError, ValueError, TypeError, AttributeError) as error:
        message: str = f"Translucent fill: {error}"
        if message != _LAST_ERROR:
            _LAST_ERROR = message
            print(message)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(mask)
        gpu.state.blend_set(blend)


def _handlers() -> tuple[tuple[list[Any], Callable[..., None]], ...]:
    return (
        (bpy.app.handlers.depsgraph_update_post, _update),
        (bpy.app.handlers.undo_post, _reset),
        (bpy.app.handlers.redo_post, _reset),
        (bpy.app.handlers.load_post, _reset),
        (bpy.app.handlers.save_pre, _save_pre),
        (bpy.app.handlers.save_post, _save_post),
        (bpy.app.handlers.save_post_fail, _save_post),
    )


def install() -> None:
    global _HANDLER
    if _HANDLER is None:
        _HANDLER = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")
    handlers: list[Any]
    callback: Callable[..., None]
    for handlers, callback in _handlers():
        if callback not in handlers:
            handlers.append(callback)
    _refresh(bpy.context.scene)


def remove() -> None:
    global _HANDLER, _SHADER, _SAVING, _SELECTING
    handlers: list[Any]
    callback: Callable[..., None]
    for handlers, callback in _handlers():
        if callback in handlers:
            handlers.remove(callback)
    if _HANDLER is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLER, "WINDOW")
    _HANDLER = _SHADER = None
    _SAVING = _SELECTING = False
    _restore()
    _FILLS.clear()
