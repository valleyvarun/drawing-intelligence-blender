"""Scene tools: inspect the drawing, and add, transform, select, or delete objects."""

from __future__ import annotations

import math
from typing import Any, Literal

from ..core.blender import Vector, bmesh, bpy
from ..core.state import DRAWING_TOOLS, AppState, get_state
from .common import (
    find_object,
    group_of,
    hex_color,
    kind,
    meters,
    number,
    position,
    rounded,
    to_units,
    unit_scale,
    vector,
)
from .registry import JsonObject, ToolError, tool

# Larger scenes are summarized; object_info gives the details of any one object.
SCENE_LIMIT: int = 300


def _locked(obj: Any) -> bool:
    from ..tools.layers import object_locked

    return bool(object_locked(bpy.context, obj))


def _grease_strokes(obj: Any) -> int:
    count: int = 0
    layer: Any
    for layer in obj.data.layers:
        frame: Any = layer.current_frame()
        if frame is not None:
            count += len(frame.drawing.strokes)
    return count


def _summary(obj: Any) -> JsonObject:
    entry: JsonObject = {
        "name": str(obj.name),
        "kind": kind(obj),
        "group": group_of(obj),
        "location": meters(obj.matrix_world.translation),
        "dimensions": meters(obj.dimensions),
        "visible": bool(obj.visible_get()),
        "locked": _locked(obj),
        "selected": bool(obj.select_get()),
    }
    if obj.get("drawing_stroke") and obj.type == "MESH":
        entry["color"] = hex_color(obj.color)
    if obj.type == "GREASEPENCIL":
        entry["strokes"] = _grease_strokes(obj)
    return entry


@tool(read_only=True)
def scene_info() -> JsonObject:
    """Summarize the drawing: groups, objects, selection, active tool, colours, units, and canvas options.

    Positions and sizes are in metres. Strokes are paint/pipe meshes or a group's Grease Pencil object.
    """
    state: AppState = get_state()
    scene: Any = bpy.context.scene
    # World matrices follow edits only after an evaluation.
    bpy.context.view_layer.update()
    objects: list[JsonObject] = []
    selection: list[str] = []
    obj: Any
    for obj in scene.objects:
        if obj.select_get():
            selection.append(str(obj.name))
        if len(objects) < SCENE_LIMIT:
            objects.append(_summary(obj))
    groups: list[JsonObject] = []
    collection: Any
    for collection in bpy.data.collections:
        if collection.get("drawing_group"):
            count: int = len(collection.all_objects)
            groups.append(
                {
                    "name": str(collection.name),
                    "visible": not collection.hide_viewport,
                    "locked": bool(collection.hide_select),
                    "active": collection.name == state.active_group,
                    "objects": count,
                }
            )
    colors: JsonObject = {"toolbox": hex_color(state.color)}
    name: str
    for name in DRAWING_TOOLS:
        colors[name] = hex_color(state.brushes[name].color)
    active: Any = bpy.context.view_layer.objects.active
    return {
        "file": bpy.data.filepath or "(unsaved)",
        "units": {"display": state.units, "metres_per_blender_unit": unit_scale()},
        "tool": state.tool,
        "colors": colors,
        "active_group": state.active_group,
        "groups": groups,
        "objects": objects,
        "object_count": len(scene.objects),
        "selection": selection,
        "active_object": str(active.name) if active is not None else None,
        "canvas": {
            "drawing_plane": state.drawing_plane,
            "active_plane": state.active_plane,
            "draw_on_objects": state.on_object,
            "cursor": state.cursor_active,
            "cursor_location": meters(scene.cursor.location),
        },
    }


@tool(read_only=True)
def object_info(name: str) -> JsonObject:
    """Describe one object: transform, size, world bounds, colour, geometry, and stroke data.

    Args:
        name: Object name, as listed by scene_info.
    """
    obj: Any = find_object(name)
    bpy.context.view_layer.update()
    info: JsonObject = _summary(obj)
    low: list[float] = [math.inf, math.inf, math.inf]
    high: list[float] = [-math.inf, -math.inf, -math.inf]
    corner: Any
    axis: int
    for corner in obj.bound_box:
        point: Any = obj.matrix_world @ Vector(corner)
        for axis in range(3):
            low[axis] = min(low[axis], float(point[axis]))
            high[axis] = max(high[axis], float(point[axis]))
    angles: list[float] = []
    value: float
    for value in obj.rotation_euler:
        angles.append(math.degrees(value))
    children: list[str] = []
    child: Any
    for child in obj.children:
        children.append(str(child.name))
    info.update(
        {
            "type": str(obj.type),
            "rotation_degrees": rounded(angles, 3),
            "scale": rounded(obj.scale),
            "bounds": {"min": meters(low), "max": meters(high)},
            "parent": str(obj.parent.name) if obj.parent is not None else None,
            "children": children,
        }
    )
    if obj.type == "MESH":
        info["mesh"] = {"vertices": len(obj.data.vertices), "faces": len(obj.data.polygons)}
        material: Any = obj.active_material
        if material is not None and not obj.get("drawing_stroke"):
            info["color"] = hex_color(material.diffuse_color)
    samples: Any = obj.get("drawing_samples")
    if samples is not None and len(samples) >= 9:
        total: float = 0.0
        index: int
        for index in range(3, len(samples), 9):
            total += float(samples[index])
        count: int = len(samples) // 9
        info["stroke"] = {
            "samples": count,
            "shape": str(obj.get("drawing_shape", "circle")),
            "opacity": round(float(obj.color[3]), 4),
            "average_radius": round(total / count * unit_scale() * max(obj.scale), 5),
        }
    if obj.type == "GREASEPENCIL":
        layers: list[JsonObject] = []
        layer: Any
        for layer in obj.data.layers:
            frame: Any = layer.current_frame()
            layers.append({"name": str(layer.name), "strokes": len(frame.drawing.strokes) if frame else 0})
        info["grease_pencil"] = {"layers": layers}
    return info


@tool(undo="Add shape")
def add_primitive(
    shape: Literal["cube", "sphere", "cylinder", "cone", "plane"],
    location: list[float] | None = None,
    size: float = 1.0,
    name: str = "",
) -> JsonObject:
    """Add a basic shape to the active drawing group, styled like shapes from the Create menu.

    Args:
        shape: "cube", "sphere", "cylinder", "cone", or "plane".
        location: Centre as [x, y, z] in metres; defaults to the 3D cursor when it is on, else the origin.
        size: Overall size (edge length or diameter) in metres.
        name: Object name; defaults to the shape's name.
    """
    from ..core import controller
    from ..tools import layers

    edge: float = number(size, "size")
    if edge <= 0.0:
        raise ToolError("size must be a positive number of metres")
    extent: float = to_units(edge)
    mesh: Any = bpy.data.meshes.new(name.strip() or shape.title())
    shapes: Any = bmesh.new()
    try:
        if shape == "cube":
            bmesh.ops.create_cube(shapes, size=extent)
        elif shape == "sphere":
            bmesh.ops.create_uvsphere(shapes, u_segments=24, v_segments=12, radius=extent / 2.0)
        elif shape in ("cylinder", "cone"):
            bmesh.ops.create_cone(
                shapes,
                cap_ends=True,
                cap_tris=False,
                segments=24,
                radius1=extent / 2.0,
                radius2=extent / 2.0 if shape == "cylinder" else 0.0,
                depth=extent,
            )
        elif shape == "plane":
            bmesh.ops.create_grid(shapes, x_segments=1, y_segments=1, size=extent / 2.0)
        else:
            raise ToolError("shape must be cube, sphere, cylinder, cone, or plane")
        shapes.to_mesh(mesh)
    finally:
        shapes.free()
    controller.leave_pencil()
    obj: Any = bpy.data.objects.new(mesh.name, mesh)
    layers.active_collection(bpy.context).objects.link(obj)
    if location is not None:
        obj.location = position(location, "location")
    elif get_state().cursor_active:
        obj.location = bpy.context.scene.cursor.location
    obj.color = (0.55, 0.55, 0.55, 1.0)
    obj.show_wire = True
    obj.show_all_edges = True
    bpy.context.view_layer.update()
    return _summary(obj)


@tool(undo="Transform object")
def transform_object(
    name: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> JsonObject:
    """Move, rotate, or scale an object. Values replace the current ones; omitted ones are kept.

    Args:
        name: Object name.
        location: New position as [x, y, z] in metres.
        rotation: New rotation as [x, y, z] Euler angles in degrees.
        scale: New scale factors as [x, y, z].
    """
    obj: Any = find_object(name)
    if _locked(obj):
        raise ToolError(f"{name} is locked; unlock it or its group first")
    if location is not None:
        obj.location = position(location, "location")
    if rotation is not None:
        angles: tuple[float, float, float] = vector(rotation, "rotation")
        obj.rotation_euler = (math.radians(angles[0]), math.radians(angles[1]), math.radians(angles[2]))
    if scale is not None:
        obj.scale = vector(scale, "scale")
    bpy.context.view_layer.update()
    return _summary(obj)


@tool(undo="Delete objects")
def delete_objects(names: list[str]) -> JsonObject:
    """Delete objects by name. Locked objects are kept.

    Args:
        names: Names of the objects to delete.
    """
    from ..core import controller

    controller.leave_pencil()
    deleted: list[str] = []
    locked: list[str] = []
    missing: list[str] = []
    name: str
    for name in names:
        obj: Any = bpy.data.objects.get(name)
        if obj is None:
            missing.append(name)
        elif _locked(obj):
            locked.append(name)
        else:
            bpy.data.objects.remove(obj, do_unlink=True)
            deleted.append(name)
    return {"deleted": deleted, "locked": locked, "missing": missing}


@tool(undo="Select objects")
def select_objects(names: list[str]) -> JsonObject:
    """Replace the selection with the named objects; an empty list clears it. The first becomes active.

    Args:
        names: Names of the objects to select.
    """
    from ..core import controller

    controller.leave_pencil()
    view_layer: Any = bpy.context.view_layer
    obj: Any
    for obj in view_layer.objects:
        obj.select_set(False)
    selected: list[str] = []
    skipped: list[str] = []
    name: str
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None or _locked(obj) or not obj.visible_get():
            skipped.append(name)
            continue
        obj.select_set(True)
        if not selected:
            view_layer.objects.active = obj
        selected.append(name)
    return {"selected": selected, "skipped": skipped}
