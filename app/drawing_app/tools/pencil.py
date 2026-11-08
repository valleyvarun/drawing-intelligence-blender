"""Blender's native Grease Pencil Draw mode in the drawing application.

Strokes are made by Blender's own brush engine and tools (Draw, Erase, Fill, primitives, Trim...),
using Blender's brush assets, Grease Pencil materials, stroke placement, and drawing plane.
Each drawing group owns one Grease Pencil object, so group visibility and locks still apply.
Placement follows Blender's own settings (Origin on the View plane by default) unless a Canvas
option is on: the 3D cursor, object surfaces, or a drawing plane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..core.blender import bpy, view3d_utils
from ..core.state import AppState, Pencil, Vec3, get_state
from ..viewport import fills
from . import layers

MODE: str = "PAINT_GREASE_PENCIL"
MARKER: str = "drawing_pencil"
LAYER: str = "Layer"
LIBRARY: str = "brushes/essentials_brushes-gp_draw.blend/Brush/"
DRAWING_TOOLS: tuple[tuple[str, str], ...] = (
    ("builtin.brush", "Draw"),
    ("builtin_brush.Fill", "Fill"),
    ("builtin.polyline", "Polyline"),
    ("builtin.curve", "Curve"),
    ("builtin.box", "Box"),
    ("builtin.circle", "Circle"),
)
ERASING_TOOLS: tuple[tuple[str, str], ...] = (
    ("builtin.trim", "Trim"),
    ("builtin_brush.Erase", "Erase"),
)
TOOLS: tuple[tuple[str, str], ...] = DRAWING_TOOLS + ERASING_TOOLS
DRAW_BRUSHES: tuple[str, ...] = (
    "Pencil",
    "Pencil Soft",
    "Pen",
    "Ink Pen",
    "Ink Pen Rough",
    "Marker Bold",
    "Marker Chisel",
    "Airbrush",
    "Tint",
)
ERASE_BRUSHES: tuple[str, ...] = ("Eraser Hard", "Eraser Soft", "Eraser Stroke")
# Tools that draw with the active Draw brush; Blender's primitives use its radius and material.
DRAW_TOOLS: tuple[str, ...] = (
    "builtin.brush",
    "builtin.line",
    "builtin.polyline",
    "builtin.arc",
    "builtin.curve",
    "builtin.box",
    "builtin.circle",
)
PLACEMENTS: tuple[tuple[str, str], ...] = (
    ("ORIGIN", "Origin"),
    ("CURSOR", "3D Cursor"),
    ("SURFACE", "Surface"),
    ("STROKE", "Stroke"),
)
PLANES: tuple[tuple[str, str], ...] = (
    ("VIEW", "View"),
    ("AXIS_Y", "Front (XZ)"),
    ("AXIS_X", "Side (YZ)"),
    ("AXIS_Z", "Top (XY)"),
    ("CURSOR", "3D Cursor"),
)
# Grease Pencil materials offered as the Tool Box colors.
COLORS: tuple[tuple[str, Vec3], ...] = (
    ("Black", (0.0, 0.0, 0.0)),
    ("White", (0.8, 0.8, 0.8)),
    ("Red", (0.65, 0.06, 0.04)),
    ("Blue", (0.05, 0.32, 0.7)),
    ("Green", (0.06, 0.45, 0.13)),
    ("Orange", (0.65, 0.36, 0.015)),
)
AXES: tuple[str, str, str] = ("AXIS_X", "AXIS_Y", "AXIS_Z")


@dataclass(frozen=True)
class PencilView:
    """Native Draw-mode settings shown in the Tool Box."""

    tool: str
    brush: str
    brushes: tuple[str, ...]
    size: float
    size_scene: bool
    size_range: tuple[float, float]
    strength: float
    hardness: float
    pressure_size: bool
    pressure_strength: bool
    stabilize: bool
    material: str
    placement: str
    plane: str
    canvas: str


def is_pencil(obj: Any) -> bool:
    return obj is not None and obj.type == "GREASEPENCIL" and bool(obj.get(MARKER))


def active(context: Any) -> bool:
    """True while an application Grease Pencil object is in native Draw mode."""
    obj: Any = context.view_layer.objects.active
    return is_pencil(obj) and obj.mode == MODE


def editing(context: Any) -> bool:
    """Native stroke selection is separate from selecting the containing object."""
    obj: Any = context.view_layer.objects.active
    return obj is not None and obj.type == "GREASEPENCIL" and obj.mode == "EDIT"


def enter_selection(context: Any, obj: Any = None) -> bool:
    """Edit existing strokes without splitting them into separate objects."""
    if obj is None:
        obj = context.view_layer.objects.active
    if obj is None or obj.type != "GREASEPENCIL":
        obj = pencil_object(context, create=False)
    if obj is None:
        get_state().status = "Select a Grease Pencil object or draw strokes first"
        return False
    if layers.object_locked(context, obj) or not obj.visible_get():
        get_state().status = "Unlock and show the Grease Pencil object to select its strokes"
        return False
    entered: bool = not editing(context) or context.view_layer.objects.active != obj
    if entered:
        current: Any = context.view_layer.objects.active
        if current is not None and current.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        selected: Any
        for selected in tuple(context.selected_objects):
            if selected != obj:
                selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.grease_pencil.set_selection_mode(mode="STROKE")
    context.scene.tool_settings.use_proportional_edit = False
    context.scene.tool_settings.use_grease_pencil_multi_frame_editing = False
    if entered:
        bpy.ops.grease_pencil.select_all(action="DESELECT")
        _undo_push("Grease Pencil stroke selection")
    bpy.ops.wm.tool_set_by_id(name="builtin.select_box", space_type="VIEW_3D")
    sync_shelf(context.area)
    get_state().status = "Select strokes: click one, Shift-click several, or drag a selection box"
    return True


def stroke_rows(obj: Any, depth: int, visible: bool, locked: bool) -> list[layers.LayerRow]:
    """Expose native strokes at the same level as paint and pipe strokes."""
    rows: list[layers.LayerRow] = []
    layer_index: int
    layer: Any
    for layer_index, layer in enumerate(obj.data.layers):
        frame: Any = layer.current_frame()
        if frame is None:
            continue
        stroke_index: int
        stroke: Any
        for stroke_index, stroke in enumerate(frame.drawing.strokes):
            rows.append(
                layers.LayerRow(
                    f"stroke:{obj.name}:{layer_index}:{frame.frame_number}:{stroke_index}",
                    f"Stroke {len(rows) + 1}",
                    "Stroke",
                    depth,
                    visible and not layer.hide,
                    locked or bool(layer.lock),
                    False,
                    obj.mode == "EDIT" and bool(stroke.select),
                )
            )
    return rows


def select_row(context: Any, key: str, extend: bool = False) -> bool:
    """A Clayers child selects a stroke, never its containing object."""
    parts: list[str] = key.removeprefix("stroke:").rsplit(":", 3)
    if len(parts) != 4:
        return False
    obj: Any = bpy.data.objects.get(parts[0])
    layer_index: int = int(parts[1])
    stroke_index: int = int(parts[3])
    if obj is None or obj.type != "GREASEPENCIL" or not 0 <= layer_index < len(obj.data.layers):
        return False
    layer: Any = obj.data.layers[layer_index]
    frame: Any = layer.current_frame()
    if frame is None or frame.frame_number != int(parts[2]) or not 0 <= stroke_index < len(frame.drawing.strokes):
        return False
    if layer.lock or layer.hide:
        get_state().status = "Unlock and show the Grease Pencil layer to select its strokes"
        return False
    if not enter_selection(context, obj):
        return False
    if not extend:
        bpy.ops.grease_pencil.select_all(action="DESELECT")
    stroke: Any = frame.drawing.strokes[stroke_index]
    stroke.select = not bool(stroke.select) if extend else True
    obj.data.layers.active = layer
    obj.data.update_tag()
    get_state().tool = "select"
    return True


def _has_selected_stroke(obj: Any) -> bool:
    layer: Any
    stroke: Any
    for layer in obj.data.layers:
        frame: Any = layer.current_frame()
        if frame is not None and not layer.hide and not layer.lock:
            for stroke in frame.drawing.strokes:
                if stroke.select:
                    return True
    return False


def _select_fill_at(context: Any, location: tuple[int, int], extend: bool) -> bool:
    """Keep translucent fill interiors pickable when their native viewport faces are hidden."""
    region: Any = context.region
    view: Any = context.region_data
    if region is None or view is None:
        return False
    origin: Any = view3d_utils.region_2d_to_origin_3d(region, view, location)
    direction: Any = view3d_utils.region_2d_to_vector_3d(region, view, location)
    hit: Any = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
    if not hit[0]:
        return False
    obj: Any = hit[4]
    if obj is None or obj.type != "MESH" or obj.get("drawing_kind") != "fill":
        return False
    material: Any = obj.active_material
    rgba: Any = material.diffuse_color if material is not None else obj.color
    if not 0.0 < float(rgba[3]) < 1.0 or layers.object_locked(context, obj) or not obj.visible_get():
        return False
    selected: bool = bool(obj.select_get())
    leave(context)
    if not extend:
        other: Any
        for other in tuple(context.selected_objects):
            other.select_set(False)
    obj.select_set(not selected if extend else True)
    context.view_layer.objects.active = obj
    return True


def select_at(context: Any, location: tuple[int, int], extend: bool = False) -> None:
    """Normal viewport clicks pick Grease Pencil strokes instead of their container."""
    result: set[str]
    if editing(context):
        if not extend:
            selected: Any
            for selected in tuple(context.selected_objects):
                if selected.type != "GREASEPENCIL":
                    selected.select_set(False)
        result = bpy.ops.view3d.select(location=location, toggle=extend, deselect_all=not extend)
        if extend or ("FINISHED" in result and _has_selected_stroke(context.view_layer.objects.active)):
            if "FINISHED" not in result:
                _select_fill_at(context, location, extend)
            return
        # An empty stroke hit can still be a mesh or another Grease Pencil object.
        leave(context)
    result = bpy.ops.view3d.select(location=location, toggle=extend, deselect_all=not extend)
    obj: Any = context.view_layer.objects.active
    if (
        "FINISHED" in result
        and obj is not None
        and obj.type == "GREASEPENCIL"
        and (obj.select_get() or extend)
        and enter_selection(context, obj)
    ):
        result = bpy.ops.view3d.select(location=location, toggle=extend, deselect_all=not extend)
        tool: str = get_state().tool
        if tool in ("move", "rotate", "scale"):
            bpy.ops.wm.tool_set_by_id(name="builtin." + tool, space_type="VIEW_3D")
        if "FINISHED" in result and _has_selected_stroke(obj):
            return
    elif "FINISHED" in result and obj is not None and obj.get("drawing_kind") == "fill":
        return
    _select_fill_at(context, location, extend)


def select_box(context: Any, start: tuple[int, int], end: tuple[int, int], extend: bool = False) -> None:
    """Refine selected containers into native strokes, regardless of the active drawing tool."""
    saved: list[tuple[Any, list[bool]]] = []
    layer: Any
    stroke: Any
    obj: Any = context.view_layer.objects.active
    if editing(context) and extend:
        for layer in obj.data.layers:
            frame: Any = layer.current_frame()
            if frame is not None:
                saved.append((frame.drawing, [bool(stroke.select) for stroke in frame.drawing.strokes]))
    leave(context)
    bounds: dict[str, int] = {
        "xmin": min(start[0], end[0]),
        "xmax": max(start[0], end[0]),
        "ymin": min(start[1], end[1]),
        "ymax": max(start[1], end[1]),
    }
    bpy.ops.view3d.select_box(**bounds, mode="ADD" if extend else "SET", wait_for_input=False)
    has_fills: bool
    with fills.selection_surfaces() as has_fills:
        if has_fills:
            bpy.ops.view3d.select_box(**bounds, mode="ADD", wait_for_input=False)
    objects: list[Any] = list(context.selected_objects)
    selected: list[Any] = []
    drawing: Any
    values: list[bool]
    index: int
    for obj in objects:
        if obj.type != "GREASEPENCIL":
            selected.append(obj)
            continue
        if not enter_selection(context, obj):
            continue
        for drawing, values in saved:
            for index, stroke in enumerate(drawing.strokes):
                stroke.select = values[index]
        bpy.ops.view3d.select_box(**bounds, mode="ADD" if extend else "SET", wait_for_input=False)
        if _has_selected_stroke(obj):
            selected.append(obj)
        for layer in obj.data.layers:
            frame = layer.current_frame()
            if frame is not None:
                saved.append((frame.drawing, [bool(stroke.select) for stroke in frame.drawing.strokes]))
        leave(context)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected:
        obj.select_set(True)
        context.view_layer.objects.active = obj
    pencils: list[Any] = [obj for obj in selected if obj.type == "GREASEPENCIL"]
    if pencils:
        context.view_layer.objects.active = pencils[0]
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.grease_pencil.set_selection_mode(mode="STROKE")
        context.scene.tool_settings.use_proportional_edit = False
        context.scene.tool_settings.use_grease_pencil_multi_frame_editing = False
        for drawing, values in saved:
            for index, stroke in enumerate(drawing.strokes):
                stroke.select = values[index]
        for obj in pencils:
            obj.data.update_tag()


def selection_command(context: Any, command: str) -> bool:
    """Keep edit actions scoped to selected strokes, including delete and duplicate."""
    if not editing(context):
        return False
    obj: Any = context.view_layer.objects.active
    if layers.object_locked(context, obj) or not obj.visible_get():
        get_state().status = "Unlock and show the Grease Pencil object to edit its strokes"
        return True
    if command in ("select_all", "deselect_all"):
        bpy.ops.grease_pencil.select_all(action="SELECT" if command == "select_all" else "DESELECT")
    elif command == "delete":
        bpy.ops.grease_pencil.delete(mode="ALL")
        _undo_push("Delete selected strokes")
    elif command == "duplicate":
        bpy.ops.grease_pencil.duplicate()
        _undo_push("Duplicate selected strokes")
    else:
        return False
    return True


def _color(name: str) -> Vec3:
    label: str
    color: Vec3
    for label, color in COLORS:
        if label == name:
            return color
    return COLORS[0][1]


def material_slot(obj: Any, name: str, fill: bool) -> int:
    """The object's slot for a palette material, added like Blender's own stroke and fill materials."""
    label: str = f"{name} Fill" if fill else name
    opacity: float = get_state().pencil.fill_opacity if fill else 1.0
    index: int
    slot: Any
    for index, slot in enumerate(obj.material_slots):
        if slot.material is not None and slot.material.get(MARKER) == label:
            if not fill or abs(float(slot.material.grease_pencil.fill_color[3]) - opacity) < 1e-6:
                return index
    color: Vec3 = _color(name)
    material: Any = bpy.data.materials.new(label)
    bpy.data.materials.create_gpencil_data(material)
    style: Any = material.grease_pencil
    # Blender shows a material's stroke or fill when that color is opaque.
    style.color = (*color, 0.0 if fill else 1.0)
    style.fill_color = (*color, opacity if fill else 0.0)
    material[MARKER] = label
    obj.data.materials.append(material)
    return len(obj.material_slots) - 1


def _keyframe(layer: Any, frame: int) -> None:
    if not len(layer.frames):
        layer.frames.new(frame)


def pencil_object(context: Any, create: bool = True) -> Any:
    """The active group's Grease Pencil object; a new one matches Blender's Blank object."""
    strokes: Any = layers.active_collection(context, strokes=True)
    selected: Any = context.view_layer.objects.active
    if is_pencil(selected) and selected.name in strokes.objects:
        return selected
    obj: Any
    for obj in strokes.objects:
        if is_pencil(obj):
            return obj
    if not create:
        return None
    data: Any = bpy.data.grease_pencils.new("Grease Pencil")
    layer: Any = data.layers.new(LAYER, set_active=True)
    _keyframe(layer, int(context.scene.frame_current))
    obj = bpy.data.objects.new("Grease Pencil", data)
    layers.link_stroke(obj, context)
    obj[MARKER] = True
    obj["drawing_kind"] = "grease"
    obj.active_material_index = material_slot(obj, COLORS[0][0], False)
    return obj


def plane_layer(data: Any, plane: Any, frame: int) -> Any:
    """A layer whose space is the drawing plane; its strokes follow the plane afterwards."""
    layer: Any
    for layer in data.layers:
        if layer.parent == plane:
            return layer
    layer = data.layers.new(f"Plane {plane.name}", set_active=False)
    _keyframe(layer, frame)
    matrix: Any = plane.matrix_world.copy()
    # Parenting stores the plane's inverse; the local transform restores plane space.
    layer.parent = plane
    layer.translation = matrix.to_translation()
    layer.rotation = matrix.to_euler("XYZ")
    layer.scale = matrix.to_scale()
    return layer


def _label(items: tuple[tuple[str, str], ...], code: str) -> str:
    key: str
    label: str
    for key, label in items:
        if key == code:
            return label
    return code.title()


def _has_choice(items: tuple[tuple[str, str | Vec3], ...], code: str) -> bool:
    key: str
    _value: str | Vec3
    for key, _value in items:
        if key == code:
            return True
    return False


def _palette_name(material: Any) -> str | None:
    if material is None:
        return None
    label: object = material.get(MARKER)
    if not isinstance(label, str):
        return None
    name: str = label.removesuffix(" Fill")
    return name if _has_choice(COLORS, name) else None


def canvas_label(state: AppState) -> str:
    """The Canvas option that overrides Blender's placement, or an empty string."""
    if state.cursor_active and state.cursor_placed:
        return "3D cursor, " + "XYZ"[state.cursor_axis] + " normal"
    if state.on_object:
        return "Object surfaces"
    if state.drawing_plane:
        return "Ground plane" if state.active_plane == "Ground Plane" else "Drawing plane"
    return ""


def placement(state: AppState) -> tuple[str, str]:
    """Blender stroke placement and drawing plane after Canvas options."""
    pencil: Pencil = state.pencil
    if state.cursor_active and state.cursor_placed:
        return "CURSOR", AXES[state.cursor_axis]
    if state.on_object:
        return "SURFACE", pencil.plane
    if state.drawing_plane:
        return "ORIGIN", "AXIS_Z"
    return pencil.placement, pencil.plane


def apply_placement(context: Any, obj: Any) -> None:
    state: AppState = get_state()
    settings: Any = context.scene.tool_settings
    data: Any = obj.data
    frame: int = int(context.scene.frame_current)
    layer: Any = data.layers.get(LAYER) or data.layers.new(LAYER, set_active=False)
    _keyframe(layer, frame)
    settings.use_gpencil_project_only_selected = False
    mode: str
    axis: str
    mode, axis = placement(state)
    plane: Any = layers.selected_plane(context) if state.drawing_plane and mode == "ORIGIN" else None
    if plane is not None:
        layer = plane_layer(data, plane, frame)
    settings.gpencil_stroke_placement_view3d = mode
    settings.gpencil_sculpt.lock_axis = axis
    data.layers.active = layer


def _paint() -> Any:
    return bpy.context.scene.tool_settings.gpencil_paint


def invoke_surface_draw(context: Any, event: Any) -> bool:
    """Capture native fill surfaces before starting Blender's modal Draw brush."""
    if (
        event.type != "LEFTMOUSE"
        or event.value != "PRESS"
        or event.alt
        or not active(context)
        or placement(get_state())[0] != "SURFACE"
        or active_tool(context) != "builtin.brush"
    ):
        return False
    brush: Any = _paint().brush
    if brush is None or brush.gpencil_brush_type != "DRAW":
        return False
    with fills.selection_surfaces(include_zero_opacity=True) as exposed:
        if not exposed:
            return False
        drawing: Any = context.view_layer.objects.active.data.layers.active.current_frame().drawing
        count: int = len(drawing.strokes)
        result: set[str] = bpy.ops.grease_pencil.brush_stroke(
            "INVOKE_DEFAULT",
            mode="INVERT" if event.ctrl else "NORMAL",
            brush_toggle="SMOOTH" if event.shift else "None",
        )
    if "RUNNING_MODAL" in result and len(drawing.strokes) == count:
        # Stabilization can delay the initial depth capture until the pen moves.
        bpy.ops.drawing.surface_capture("INVOKE_DEFAULT", stroke_count=count)
    return bool(result.intersection(("RUNNING_MODAL", "FINISHED")))


def active_tool(context: Any) -> str:
    tool: Any = context.workspace.tools.from_space_view3d_mode(MODE, create=False)
    return str(tool.idname) if tool is not None else ""


def _activate_brush(name: str) -> None:
    library: tuple[str, ...] = ERASE_BRUSHES if name in ERASE_BRUSHES else DRAW_BRUSHES
    if name not in library:
        return
    brush: Any = _paint().brush
    if brush is not None and brush.name == name:
        return
    bpy.ops.brush.asset_activate(asset_library_type="ESSENTIALS", relative_asset_identifier=LIBRARY + name)


def enter(context: Any, material: str | None = None) -> bool:
    """Enter native Draw mode on the active group's object, with the current settings."""
    state: AppState = get_state()
    if not layers.can_draw(context):
        leave(context)
        state.status = "The active drawing group is hidden or locked"
        return False
    created: bool = pencil_object(context, create=False) is None
    obj: Any = pencil_object(context)
    if layers.object_locked(context, obj) or not obj.visible_get():
        leave(context)
        state.status = f"Unlock and show {obj.name} to draw with Grease Pencil"
        return False
    current: Any = context.view_layer.objects.active
    if current is not None and current != obj and current.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    selected: Any
    for selected in tuple(context.selected_objects):
        if selected != obj:
            selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    apply_placement(context, obj)
    entered: bool = obj.mode != MODE
    if entered:
        bpy.ops.object.mode_set(mode=MODE)
    _paint().color_mode = "MATERIAL"
    pencil: Pencil = state.pencil
    tool: str = pencil.tool if _has_choice(TOOLS, pencil.tool) else "builtin.brush"
    bpy.ops.wm.tool_set_by_id(name=tool, space_type="VIEW_3D")
    if tool == "builtin.brush":
        _activate_brush(pencil.brush)
    elif tool == "builtin_brush.Erase":
        _activate_brush(pencil.eraser)
    sync_shelf(context.area)
    color: str | None = material
    if color is None:
        color = pencil.material if created or obj.active_material is None else _palette_name(obj.active_material)
    if color is not None:
        obj.active_material_index = material_slot(obj, color, tool == "builtin_brush.Fill")
        pencil.material = color
    if created or entered:
        # As in Blender, entering Draw mode is its own undo step; undoing a stroke keeps the object.
        _undo_push("Grease Pencil Draw Mode")
    return True


def _undo_push(message: str) -> None:
    from ..core.project import persist_scene

    if not bpy.app.background and bpy.ops.ed.undo_push.poll():
        persist_scene()
        bpy.ops.ed.undo_push(message=message)


def sync_shelf(area: Any) -> None:
    """Show the native brush thumbnails for Draw and retain shelf selections."""
    if area is None or area.type != "VIEW_3D":
        return
    space: Any = area.spaces.active
    tool: str = active_tool(bpy.context) if get_state().tool == "grease" and active(bpy.context) else ""
    visible: bool = tool == "builtin.brush"
    if tool:
        get_state().pencil.tool = tool
        brush: Any = _paint().brush
        if brush is not None:
            if tool == "builtin.brush":
                get_state().pencil.brush = str(brush.name)
            elif tool == "builtin_brush.Erase":
                get_state().pencil.eraser = str(brush.name)
    try:
        if space.show_region_asset_shelf != visible:
            space.show_region_asset_shelf = visible
        if visible:
            bpy.context.preferences.view.use_filter_brushes_by_tool = True
    except (AttributeError, TypeError):
        # Read-only until the shelf exists in this mode; retry on the next tick.
        return


def leave(context: Any) -> None:
    obj: Any = context.view_layer.objects.active
    if obj is not None and obj.mode != "OBJECT":
        was_drawing: bool = obj.type == "GREASEPENCIL" and obj.mode == MODE
        bpy.ops.object.mode_set(mode="OBJECT")
        if was_drawing:
            obj.select_set(False)
            context.view_layer.objects.active = None
        if is_pencil(obj):
            _undo_push("Object Mode")
    sync_shelf(context.area)


def refresh(context: Any) -> None:
    """Follow canvas and group changes while Draw mode is active."""
    if get_state().tool == "grease":
        enter(context)


def command(context: Any, field: str, value: object) -> None:
    """Tool Box changes to Blender's native Grease Pencil tools, brushes, and placement."""
    pencil: Pencil = get_state().pencil
    material: str | None = None
    if field == "tool" and isinstance(value, str) and _has_choice(TOOLS, value):
        get_state().tool = "grease"
        get_state().status = "Grease Pencil: " + _label(TOOLS, value)
        pencil.tool = value
    elif field == "brush" and isinstance(value, str) and value in ERASE_BRUSHES:
        pencil.eraser = value
    elif field == "brush" and isinstance(value, str) and value in DRAW_BRUSHES:
        pencil.brush = value
    elif field == "material" and isinstance(value, str) and _has_choice(COLORS, value):
        pencil.material = value
        material = value
    elif field == "fill_opacity" and isinstance(value, (int, float)) and math.isfinite(value):
        pencil.fill_opacity = max(0.0, min(1.0, float(value)))
    elif field == "placement" and isinstance(value, str) and _has_choice(PLACEMENTS, value):
        pencil.placement = value
    elif field == "plane" and isinstance(value, str) and _has_choice(PLANES, value):
        pencil.plane = value
    else:
        _brush_setting(field, value)
        return
    enter(context, material)


def _brush_setting(field: str, value: object) -> None:
    brush: Any = _paint().brush
    if brush is None:
        return
    if field == "size" and isinstance(value, (int, float)) and math.isfinite(value):
        if brush.use_locked_size == "SCENE":
            brush.unprojected_size = max(1e-5, float(value))
        else:
            brush.size = max(1, round(float(value)))
    elif field == "strength" and isinstance(value, (int, float)) and math.isfinite(value):
        brush.strength = max(0.0, min(1.0, float(value)))
    elif field == "hardness" and isinstance(value, (int, float)) and math.isfinite(value):
        brush.gpencil_settings.hardness = max(0.0, min(1.0, float(value)))
    elif field == "pressure_size":
        brush.use_pressure_size = bool(value)
    elif field == "pressure_strength":
        brush.use_pressure_strength = bool(value)
    elif field == "stabilize":
        brush.use_smooth_stroke = bool(value)


def view(context: Any) -> PencilView | None:
    """Current native settings, or None outside Draw mode."""
    obj: Any = context.view_layer.objects.active
    if not is_pencil(obj) or obj.mode != MODE:
        return None
    state: AppState = get_state()
    tool: str = active_tool(context)
    brush: Any = _paint().brush
    mode: str
    axis: str
    mode, axis = placement(state)
    canvas: str = canvas_label(state)
    brushes: tuple[str, ...] = ()
    if tool == "builtin.brush":
        brushes = DRAW_BRUSHES
    elif tool == "builtin_brush.Erase":
        brushes = ERASE_BRUSHES
    if brush is None or tool in ("builtin.trim", "builtin.eyedropper"):
        return PencilView(tool, "", (), 0.0, False, (0.0, 1.0), 0.0, 0.0, False, False, False, "", mode, axis, canvas)
    scene_size: bool = brush.use_locked_size == "SCENE"
    name: str = "unprojected_size" if scene_size else "size"
    rna: Any = brush.bl_rna.properties[name]
    material: Any = obj.active_material
    material_name: str = (_palette_name(material) or str(material.name)) if material is not None else ""
    return PencilView(
        tool,
        str(brush.name),
        brushes,
        float(getattr(brush, name)),
        scene_size,
        (float(rna.soft_min), float(rna.soft_max)),
        float(brush.strength),
        float(brush.gpencil_settings.hardness),
        bool(brush.use_pressure_size),
        bool(brush.use_pressure_strength),
        bool(brush.use_smooth_stroke),
        material_name,
        mode,
        axis,
        canvas,
    )


def placement_text(code: str) -> str:
    return _label(PLACEMENTS, code)


def plane_text(code: str) -> str:
    return _label(PLANES, code)
