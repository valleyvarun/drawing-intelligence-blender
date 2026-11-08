"""Drawing groups, planes, and stroke ownership backed by Blender collections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.blender import Vector, bpy
from ..core.state import get_state

GROUP: str = "drawing_group"
STROKES: str = "drawing_strokes"
PLANE: str = "drawing_plane"


@dataclass(frozen=True)
class LayerRow:
    key: str
    name: str
    kind: str
    depth: int
    visible: bool
    locked: bool
    active: bool
    selected: bool = False


def _context(context: Any = None) -> Any:
    return context if context is not None else bpy.context


def _collection(key: str) -> Any:
    return bpy.data.collections.get(key.removeprefix("collection:"))


def _object(key: str) -> Any:
    return bpy.data.objects.get(key.removeprefix("object:"))


def ensure_scene(context: Any = None) -> None:
    ctx: Any = _context(context)
    collection: Any = bpy.data.collections.get("Default Drawing")
    if collection is None:
        collection = bpy.data.collections.new("Default Drawing")
        collection[GROUP] = True
    if collection.name not in ctx.scene.collection.children:
        ctx.scene.collection.children.link(collection)
    if bpy.data.collections.get(get_state().active_group) is None:
        get_state().active_group = collection.name
    stroke_collection(collection)


def stroke_collection(group: Any) -> Any:
    child: Any
    for child in group.children:
        if child.get(STROKES):
            return child
    collection: Any = bpy.data.collections.new(f"{group.name} · Strokes")
    collection[STROKES] = True
    group.children.link(collection)
    return collection


def active_collection(context: Any = None, strokes: bool = False) -> Any:
    ensure_scene(context)
    group: Any = bpy.data.collections.get(get_state().active_group)
    return stroke_collection(group) if strokes else group


def link_stroke(obj: Any, context: Any = None) -> None:
    active_collection(context, strokes=True).objects.link(obj)
    obj["drawing_stroke"] = True


def can_draw(context: Any = None) -> bool:
    ctx: Any = _context(context)
    ensure_scene(ctx)
    active: str = get_state().active_group
    visited: set[str] = set()

    def search(collection: Any, blocked: bool) -> bool:
        if collection.name in visited:
            return False
        visited.add(collection.name)
        blocked = blocked or bool(collection.hide_select) or bool(collection.hide_viewport)
        if collection.name == active:
            strokes: Any = stroke_collection(collection)
            return not blocked and not strokes.hide_select and not strokes.hide_viewport
        child: Any
        for child in collection.children:
            if search(child, blocked):
                return True
        return False

    child: Any
    for child in ctx.scene.collection.children:
        if search(child, False):
            return True
    return False


def selected_plane(context: Any = None) -> Any:
    obj: Any = bpy.data.objects.get(get_state().active_plane)
    return obj if obj is not None and obj.get(PLANE) else None


def create_plane(context: Any) -> Any:
    mesh: Any = bpy.data.meshes.new("Drawing Plane")
    mesh.from_pydata([(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)], [], [(0, 1, 2, 3)])
    obj: Any = bpy.data.objects.new("Drawing Plane", mesh)
    active_collection(context).objects.link(obj)
    obj[PLANE] = True
    obj.color = (0.6, 0.2, 0.9, 0.28)
    obj.show_transparent = True
    obj.show_wire = True
    if get_state().cursor_active and get_state().cursor_placed:
        obj.location = context.scene.cursor.location
    material: Any = bpy.data.materials.new("Drawing Plane Purple")
    material.diffuse_color = obj.color
    shader: Any = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = obj.color
    shader.inputs["Alpha"].default_value = 0.28
    material.surface_render_method = "DITHERED"
    mesh.materials.append(material)
    get_state().active_plane = obj.name
    get_state().drawing_plane = True
    select(context, f"object:{obj.name}")
    return obj


def plane_equation(context: Any = None) -> tuple[Any, Any]:
    obj: Any = selected_plane(context)
    if obj is None:
        return Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 1.0))
    return (
        obj.matrix_world.translation.copy(),
        (obj.matrix_world.to_3x3().inverted().transposed() @ Vector((0.0, 0.0, 1.0))).normalized(),
    )


def _objects(collection: Any) -> list[Any]:
    return list(collection.all_objects)


def collection_locked(context: Any, target: Any) -> bool:
    """Honor collection restrictions inherited through the scene hierarchy."""

    def visit(collection: Any, inherited: bool) -> bool:
        blocked: bool = inherited or bool(collection.hide_select)
        if collection == target:
            return blocked
        child: Any
        for child in collection.children:
            if visit(child, blocked):
                return True
        return False

    return visit(_context(context).scene.collection, False)


def object_locked(context: Any, obj: Any) -> bool:
    if obj is None or obj.hide_select:
        return True
    collection: Any
    for collection in obj.users_collection:
        if collection_locked(context, collection):
            return True
    return False


def layer_rows(context: Any = None) -> list[LayerRow]:
    from . import pencil

    ctx: Any = _context(context)
    ensure_scene(ctx)
    rows: list[LayerRow] = [
        LayerRow("ground", "Ground Plane", "Plane", 0, True, False, get_state().active_plane == "Ground Plane")
    ]
    visited: set[str] = set()

    def emit(collection: Any, depth: int, inherited_lock: bool = False, inherited_hidden: bool = False) -> None:
        if collection.name in visited:
            return
        visited.add(collection.name)
        locked: bool = inherited_lock or bool(collection.hide_select)
        hidden: bool = inherited_hidden or bool(collection.hide_viewport)
        kind: str = "Stroke Layer" if collection.get(STROKES) else "Group"
        rows.append(
            LayerRow(
                f"collection:{collection.name}",
                "Strokes" if kind == "Stroke Layer" else collection.name,
                kind,
                depth,
                not hidden,
                locked,
                collection.name == get_state().active_group,
            )
        )
        child: Any
        for child in collection.children:
            emit(child, depth + 1, locked, hidden)
        obj: Any
        for obj in collection.objects:
            visible: bool = not hidden and not obj.hide_get()
            object_lock: bool = locked or bool(obj.hide_select)
            if obj.type == "GREASEPENCIL":
                rows.extend(pencil.stroke_rows(obj, depth + 1, visible, object_lock))
                continue
            label: str = "Plane" if obj.get(PLANE) else "Stroke" if obj.get("drawing_stroke") else obj.type.title()
            rows.append(
                LayerRow(
                    f"object:{obj.name}",
                    obj.name,
                    label,
                    depth + 1,
                    visible,
                    object_lock,
                    bool(obj.get(PLANE)) and obj.name == get_state().active_plane,
                    bool(obj.select_get()),
                )
            )

    collection: Any
    for collection in ctx.scene.collection.children:
        if collection.get(GROUP):
            emit(collection, 0)
    return rows


def create_group(context: Any, name: str = "Group", parent: str | None = None, selected: bool = False) -> str:
    collection: Any = bpy.data.collections.new(name.strip() or "Group")
    collection[GROUP] = True
    owner: Any = _collection(parent) if parent else None
    (owner if owner is not None else context.scene.collection).children.link(collection)
    stroke_collection(collection)
    if selected:
        obj: Any
        for obj in tuple(context.selected_objects):
            if object_locked(context, obj):
                continue
            previous: Any
            for previous in tuple(obj.users_collection):
                previous.objects.unlink(obj)
            (stroke_collection(collection) if obj.get("drawing_stroke") else collection).objects.link(obj)
    get_state().active_group = collection.name
    return f"collection:{collection.name}"


def ungroup(context: Any, key: str) -> None:
    collection: Any = _collection(key)
    if collection is None or not collection.get(GROUP) or collection.name == "Default Drawing":
        return
    if collection_locked(context, collection):
        return
    parent: Any = context.scene.collection
    candidate: Any
    for candidate in bpy.data.collections:
        if collection.name in candidate.children:
            parent = candidate
            break
    destination_group: Any = parent if parent.get(GROUP) else bpy.data.collections.get("Default Drawing")
    obj: Any
    for obj in tuple(collection.objects):
        if obj.name not in destination_group.objects:
            destination_group.objects.link(obj)
    child: Any
    for child in tuple(collection.children):
        if child.get(STROKES):
            destination: Any = stroke_collection(destination_group)
            for obj in tuple(child.objects):
                if obj.name not in destination.objects:
                    destination.objects.link(obj)
            bpy.data.collections.remove(child)
        elif child.name not in parent.children:
            parent.children.link(child)
    bpy.data.collections.remove(collection)
    get_state().active_group = parent.name if parent.get(GROUP) else "Default Drawing"


def rename(context: Any, key: str, name: str) -> None:
    item: Any = _collection(key) if key.startswith("collection:") else _object(key)
    if item is None or not name.strip():
        return
    old: str = item.name
    item.name = name.strip()
    if get_state().active_group == old:
        get_state().active_group = item.name
    if get_state().active_plane == old:
        get_state().active_plane = item.name


def set_visible(context: Any, key: str, visible: bool) -> None:
    if key.startswith("collection:"):
        collection: Any = _collection(key)
        if collection is not None:
            collection.hide_viewport = not visible
            collection.hide_render = not visible
    else:
        obj: Any = _object(key)
        if obj is not None:
            obj.hide_set(not visible)
            obj.hide_render = not visible


def set_locked(context: Any, key: str, locked: bool) -> None:
    collection: Any = _collection(key) if key.startswith("collection:") else None
    objects: list[Any] = _objects(collection) if collection is not None else [_object(key)]
    if collection is not None:
        collection.hide_select = locked
    obj: Any
    for obj in objects:
        if obj is not None:
            if collection is None:
                obj.hide_select = locked
            if locked:
                obj.select_set(False)


def activate(context: Any, key: str) -> None:
    if key == "ground":
        get_state().active_plane = "Ground Plane"
    elif key.startswith("collection:"):
        collection: Any = _collection(key)
        if collection is not None and collection.get(GROUP):
            get_state().active_group = collection.name
    else:
        obj: Any = _object(key)
        if obj is not None and obj.get(PLANE):
            get_state().active_plane = obj.name
            get_state().drawing_plane = True


def select(context: Any, key: str) -> None:
    collection: Any = _collection(key) if key.startswith("collection:") else None
    objects: list[Any] = _objects(collection) if collection is not None else [_object(key)]
    obj: Any
    for obj in tuple(context.selected_objects):
        obj.select_set(False)
    for obj in objects:
        if obj is not None and not object_locked(context, obj) and obj.visible_get():
            obj.select_set(True)
            context.view_layer.objects.active = obj


def delete(context: Any, key: str) -> None:
    row: LayerRow
    for row in layer_rows(context):
        if row.key == key and row.locked:
            return
    collection: Any = _collection(key) if key.startswith("collection:") else None
    objects: list[Any] = _objects(collection) if collection is not None else [_object(key)]
    obj: Any
    if collection is not None:
        for obj in objects:
            if object_locked(context, obj):
                get_state().status = "Unlock the group's contents before deleting it"
                return
    for obj in objects:
        if obj is not None and not object_locked(context, obj):
            bpy.data.objects.remove(obj, do_unlink=True)
    if collection is not None and collection.name != "Default Drawing" and not collection.hide_select:
        bpy.data.collections.remove(collection)
    ensure_scene(context)
