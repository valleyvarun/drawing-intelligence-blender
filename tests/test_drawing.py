"""Isolated engine checks for geometry, layers, pressure, cancellation, and native Grease Pencil."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import drawing_app.viewport as viewport
from drawing_app.core.blender import Vector, bpy
from drawing_app.core.state import Pencil, Tool, Vec3, get_state, parse_hex
from drawing_app.tools import canvas, layers, pencil
from drawing_app.tools.canvas import Hit
from drawing_app.tools.drawing import DrawingController, write_mesh
from drawing_app.tools.geometry import PaintCache, Sample, paint_mesh, pipe_mesh, resample
from drawing_app.tools.surface import PaintSurface

report: dict[str, Any] = {}
context: Any = bpy.context
area: Any = None
candidate: Any
for candidate in context.screen.areas:
    if candidate.type == "VIEW_3D":
        area = candidate
        break
assert area is not None
region: Any = None
for candidate in area.regions:
    if candidate.type == "WINDOW":
        region = candidate
        break
assert region is not None
layers.ensure_scene(context)
group: str = layers.create_group(context, "Smoke Group")
child: str = layers.create_group(context, "Smoke Child", group)
row: layers.LayerRow
assert any(row.key == child and row.depth == 1 for row in layers.layer_rows(context))
report["nested_collections"] = True
plane: Any = layers.create_plane(context)
assert plane.get("drawing_plane") and layers.selected_plane(context) == plane
plane.location = (0.0, 0.0, 2.0)
context.view_layer.update()
equation: Any = layers.plane_equation(context)
assert abs(equation[0].z - 2.0) < 1e-8
report["drawing_plane_transform"] = True
get_state().drawing_plane = False


def fake_hit(_context: Any, _event: Any, _area: Any, _region: Any, placing: bool = False) -> Hit:
    return Hit((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), "ground")


original_hit: Any = canvas.hit
canvas.hit = fake_hit
controller: DrawingController = DrawingController()
event: Any = SimpleNamespace(type="LEFTMOUSE", value="PRESS", pressure=0.5, is_tablet=True, mouse_x=100, mouse_y=100)
kind: Tool
shape: str
for kind in ("pipe", "paint"):
    for shape in ("circle", "square", "triangle"):
        get_state().tool = kind
        get_state().brush.shape = shape
        get_state().brush.radius = 0.04
        get_state().brush.pressure_size = True
        get_state().brush.opacity = 0.7
        get_state().brush.hardness = 0.4
        assert controller.begin(context, event, area, region)
        assert controller.is_drawing and controller._samples[0].radius == 0.02
        finish: Sample = Sample((0.16, 0.03, 0.0), 0.035, 0.5, 0.6)
        controller._samples.extend(resample(controller._samples[-1], finish))
        obj: Any = controller._object
        controller._finish(context)
        assert not obj.select_get()
        assert obj.get("drawing_kind") == kind and not obj.get("drawing_preview")
        assert len(obj.get("drawing_samples")) > 9
        assert len(obj.data.vertices) > 3 and len(obj.data.polygons) > 0
        values: list[float] = []
        item: Any
        for item in obj.data.color_attributes["Ink"].data:
            values.append(float(item.color[3]))
        assert min(values) >= 0.0 and max(values) <= 0.70001
        report[f"{kind}_{shape}"] = True
count: int = len(bpy.data.objects)
assert controller.begin(context, event, area, region)
assert len(bpy.data.objects) == count + 1
controller.cancel()
assert not controller.is_drawing and len(bpy.data.objects) == count
report["cancel_removes_working_stroke"] = True
assert controller.begin(context, event, area, region)
release: Any = SimpleNamespace(type="LEFTMOUSE", value="RELEASE")
assert controller.handle(context, release, None, None)
assert not controller.is_drawing
report["release_outside_original_viewport"] = True
layers.set_locked(context, child, True)
assert not layers.can_draw(context)
locked_object: Any = context.view_layer.objects.active
layers.select(context, f"object:{locked_object.name}")
assert not locked_object.select_get() and layers.object_locked(context, locked_object)
protected_count: int = len(bpy.data.objects)
layers.delete(context, group)
assert len(bpy.data.objects) == protected_count and layers._collection(group) is not None
report["inherited_locks_protect_selection_and_parent_delete"] = True
layers.set_locked(context, child, False)
layers.set_visible(context, group, False)
assert not layers.can_draw(context)
layers.set_visible(context, group, True)
layers.set_visible(context, child, False)
assert layers._collection(child).hide_viewport
layers.set_visible(context, child, True)
layers.rename(context, child, "Renamed Drawing")
assert get_state().active_group == "Renamed Drawing"
layers.ungroup(context, "collection:Renamed Drawing")
assert get_state().active_group == "Smoke Group"
report["group_lock_visibility_rename_ungroup"] = True
sample: Sample = Sample((0.0, 0.0, 0.0), 0.05, 0.0, 0.6)
single: Any = paint_mesh([sample], "circle")
overlap: Any = paint_mesh([sample, sample], "circle")
assert single == overlap
value: float
assert any(0.0 < value < 0.5 for value in single[2])
report["paint_union_does_not_accumulate_alpha"] = True
cap_points: list[tuple[float, float, float]]
cap_faces: list[tuple[int, ...]]
cap_alpha: list[float]
cap_points, cap_faces, cap_alpha = pipe_mesh(
    [Sample((0.0, 0.0, 0.0), 0.1, 1.0, 1.0), Sample((0.0, 0.0, 1.0), 0.1, 1.0, 1.0)], "circle"
)
point: tuple[float, float, float]
assert math.isclose(min(point[2] for point in cap_points), -0.1)
assert math.isclose(max(point[2] for point in cap_points), 1.1)
edges: dict[tuple[int, int], int] = {}
face: tuple[int, ...]
index: int
for face in cap_faces:
    for index in range(len(face)):
        first: int = face[index]
        second: int = face[(index + 1) % len(face)]
        edge: tuple[int, int] = (min(first, second), max(first, second))
        edges[edge] = edges.get(edge, 0) + 1
assert set(edges.values()) == {2}
cap_points, cap_faces, cap_alpha = pipe_mesh([Sample((0.0, 0.0, 0.0), 0.1, 1.0, 1.0)], "circle")
for point in cap_points:
    assert math.isclose(math.sqrt(point[0] ** 2 + point[1] ** 2 + point[2] ** 2), 0.1)
report["round_pipe_closed_domes_and_single_click_sphere"] = True
long_samples: list[Sample] = []
index: int
for index in range(500):
    long_samples.append(Sample((index * 0.004, 0.02 * math.sin(index * 0.02), 0.0), 0.02, 0.5, 0.7))
cache: PaintCache = PaintCache(0.0025)
paint_mesh(long_samples[:480], "circle", cache)
started: float = time.perf_counter()
cached_geometry: Any = paint_mesh(long_samples, "circle", cache)
report["paint_500_sample_cached_update_ms"] = (time.perf_counter() - started) * 1000.0
full_geometry: Any = paint_mesh(long_samples, "circle")
cached_vertices: dict[tuple[float, float, float], float] = dict(zip(cached_geometry[0], cached_geometry[2]))
full_vertices: dict[tuple[float, float, float], float] = dict(zip(full_geometry[0], full_geometry[2]))
assert cached_vertices == full_vertices and len(cached_geometry[1]) == len(full_geometry[1])
report["incremental_paint_matches_full_union"] = True
assert controller.begin(context, event, area, region)
undo_name: str = controller._object.name
controller._finish(context)
if bpy.ops.ed.undo.poll():
    bpy.ops.ed.undo()
    assert bpy.data.objects.get(undo_name) is None
    report["native_stroke_undo"] = True
else:
    raise AssertionError("Native undo is unavailable in this Blender context")
canvas.hit = original_hit
controller.destroy()
assert controller._preview._handler is None
report["preview_handler_cleanup"] = True

# Native Blender ray casting; only the screen-coordinate ray input is substituted below.
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(20.0, 0.0, 1.0))
cube: Any = context.view_layer.objects.active
context.view_layer.update()
object_hit: Hit | None = canvas.object_hit(context, Vector((20.0, 0.0, 5.0)), Vector((0.0, 0.0, -1.0)))
assert object_hit is not None and object_hit.source == cube.name
assert math.isclose(object_hit.position[2], 1.5, abs_tol=1e-6)
assert (
    canvas.intersect(
        Vector((0.0, 0.0, 5.0)), Vector((1.0, 0.0, 0.0)), Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 1.0)), "ground"
    )
    is None
)
report["native_scene_raycast_and_parallel_plane"] = True
wrap_samples: list[Sample] = []
for index in range(41):
    sample = Sample((20.1 + index * 0.01, 0.0, 1.5), 0.04, 0.35, 0.6, (0.0, 0.0, 1.0))
    wrap_samples.extend(resample(wrap_samples[-1], sample) if wrap_samples else [sample])
for index in range(41):
    sample = Sample((20.5, 0.0, 1.5 - index * 0.01), 0.04, 0.35, 0.6, (1.0, 0.0, 0.0))
    wrap_samples.extend(resample(wrap_samples[-1], sample))
wrap_cache: PaintCache = PaintCache(0.005)
wrap_surface: PaintSurface = PaintSurface(context, cube, 0.04, wrap_cache.cell)
wrap_mesh: Any = bpy.data.meshes.new("Wrapped paint check")
wrapped: Any = bpy.data.objects.new("Wrapped paint check", wrap_mesh)
layers.link_stroke(wrapped, context)
get_state().tool = "paint"
get_state().brush.shape = "circle"
write_mesh(wrapped, wrap_samples, get_state().brush, "paint", wrap_cache, wrap_surface)
assert wrap_surface.patch_count >= 2
top_area: float = 0.0
side_area: float = 0.0
polygon: Any
for polygon in wrap_mesh.polygons:
    if polygon.normal.z > 0.9:
        top_area += float(polygon.area)
    if polygon.normal.x > 0.9:
        side_area += float(polygon.area)
assert top_area > 0.015 and side_area > 0.015, (top_area, side_area)
vertex: Any
surface_tolerance: float = wrap_cache.cell * 0.21
for vertex in wrap_mesh.vertices:
    assert 19.5 - surface_tolerance <= vertex.co.x <= 20.5 + surface_tolerance
    assert 0.5 - surface_tolerance <= vertex.co.z <= 1.5 + surface_tolerance
    assert min(abs(vertex.co.z - 1.5), abs(vertex.co.x - 20.5)) < surface_tolerance
wrap_alpha: list[float] = []
for item in wrap_mesh.color_attributes["Ink"].data:
    wrap_alpha.append(float(item.color[3]))
assert any(0.0 < value < 0.59 for value in wrap_alpha) and max(wrap_alpha) <= 0.60001
report["native_cube_wrapped_paint_preserves_both_face_areas"] = {"top": top_area, "side": side_area}
original_ray: Any = canvas.ray
ray_origin: tuple[float, float, float] = (20.0, 0.0, 5.0)


def fixed_ray(_event: Any, _area: Any, _region: Any) -> tuple[Any, Any]:
    return Vector(ray_origin), Vector((0.0, 0.0, -1.0))


canvas.ray = fixed_ray
get_state().cursor_active = False
get_state().drawing_plane = True
get_state().on_object = True
active_plane: Any = layers.selected_plane(context)
active_plane.location.z = 2.0
context.view_layer.update()
hit: Hit | None = canvas.hit(context, event, area, region)
assert hit is not None and hit.source == "plane" and math.isclose(hit.position[2], 2.0, abs_tol=1e-6)
active_plane.location.z = 1.0
context.view_layer.update()
hit = canvas.hit(context, event, area, region)
assert hit is not None and hit.source == cube.name
# A stroke keeps its starting canvas even when the pointer crosses a nearer mesh.
get_state().tool = "pipe"
get_state().axes_spokes = False
ray_origin = (22.0, 0.0, 5.0)
plane_controller: DrawingController = DrawingController()
assert plane_controller.begin(context, event, area, region)
assert plane_controller.is_drawing and plane_controller._source == "plane"
ray_origin = (20.0, 0.0, 5.0)
motion: Any = SimpleNamespace(type="MOUSEMOVE", value="NOTHING", pressure=1.0, is_tablet=False)
assert plane_controller.handle(context, motion, area, region)
assert math.isclose(plane_controller._samples[-1].position[2], 1.0, abs_tol=1e-6)
plane_controller.cancel()
get_state().cursor_active = get_state().cursor_placing = True
escape: Any = SimpleNamespace(type="ESC", value="PRESS")
assert plane_controller.handle(context, escape, area, region)
assert not get_state().cursor_placing and get_state().cursor_active
plane_controller.destroy()
report["stroke_canvas_stays_fixed_and_cursor_escape_cancels"] = True
get_state().cursor_active = get_state().cursor_placed = True
get_state().cursor_axis = 2
context.scene.cursor.location = (20.0, 0.0, 3.0)
hit = canvas.hit(context, event, area, region)
assert hit is not None and hit.source == "cursor" and hit.position[2] == 3.0
hit = canvas.hit(context, event, area, region, placing=True)
assert hit is not None and hit.source == cube.name
report["nearest_plane_object_and_cursor_precedence"] = True
get_state().drawing_plane = False
get_state().cursor_placed = False
get_state().ruler = True
ray_origin = (40.14, 0.26, 5.0)
assert canvas.hit(context, event, area, region) is None
hit = canvas.hit(context, event, area, region, placing=True)
assert hit is not None and hit.source == "ground"
original_spacing: Any = viewport.grid_spacing


def fixed_spacing(_context: Any, _area: Any, _region: Any) -> float:
    return 0.1


viewport.grid_spacing = fixed_spacing
assert canvas.place_cursor(context, event, area, region)
assert math.isclose(context.scene.cursor.location[0], 40.1, abs_tol=1e-5)
assert math.isclose(context.scene.cursor.location[1], 0.3, abs_tol=1e-6)
assert context.scene.cursor.location[2] == 0.0
report["cursor_ground_snap_on_object_miss"] = True
viewport.grid_spacing = original_spacing
canvas.ray = original_ray

# Native Grease Pencil: Blender's Draw mode, brush assets, tools, materials, and placement.
get_state().cursor_active = get_state().cursor_placed = get_state().cursor_placing = False
get_state().on_object = get_state().drawing_plane = False
get_state().pencil = Pencil()
get_state().brushes["grease"].color = (0.0, 0.0, 0.0)
get_state().tool = "grease"
settings: Any = context.scene.tool_settings
with context.temp_override(area=area, region=region):
    assert pencil.enter(context)
    gp_object: Any = context.view_layer.objects.active
    assert pencil.is_pencil(gp_object) and gp_object.mode == pencil.MODE and pencil.active(context)
    assert gp_object.name in layers.active_collection(context, strokes=True).objects
    assert gp_object.data.layers.active.name == pencil.LAYER
    assert pencil.active_tool(context) == "builtin.brush"
    assert settings.gpencil_paint.brush.name == "Pencil" and settings.gpencil_paint.color_mode == "MATERIAL"
    assert (settings.gpencil_stroke_placement_view3d, settings.gpencil_sculpt.lock_axis) == ("ORIGIN", "VIEW")
    assert gp_object.active_material.get(pencil.MARKER) == "Black"
    assert gp_object.active_material.grease_pencil.is_stroke_visible
    assert not gp_object.active_material.grease_pencil.is_fill_visible
    assert context.space_data.show_region_asset_shelf
    report["native_grease_pencil_draw_mode_defaults"] = True
    pencil.command(context, "brush", "Ink Pen")
    assert settings.gpencil_paint.brush.name == "Ink Pen"
    pencil.command(context, "tool", "builtin_brush.Erase")
    assert pencil.active_tool(context) == "builtin_brush.Erase"
    assert settings.gpencil_paint.brush.name == "Eraser Hard"
    pencil.command(context, "material", "Red")
    pencil.command(context, "tool", "builtin_brush.Fill")
    fill: Any = gp_object.active_material
    assert fill.get(pencil.MARKER) == "Red Fill" and fill.grease_pencil.is_fill_visible
    assert not fill.grease_pencil.is_stroke_visible
    pencil.command(context, "tool", "builtin.brush")
    assert settings.gpencil_paint.brush.name == "Ink Pen" and gp_object.active_material.get(pencil.MARKER) == "Red"
    report["native_tools_brush_assets_and_materials"] = True
    # Any picked colour gets its own stroke and fill materials.
    orange: Vec3 | None = parse_hex("FF8000")
    assert orange is not None
    pencil.command(context, "color", orange)
    picked: Any = gp_object.active_material
    assert picked.get(pencil.MARKER) == "#FF8000" and picked.grease_pencil.is_stroke_visible
    assert (Vector(picked.grease_pencil.color[:3]) - Vector(orange)).length < 1e-6
    assert get_state().brushes["grease"].color == orange and get_state().pencil.material == "#FF8000"
    pencil.command(context, "tool", "builtin_brush.Fill")
    assert gp_object.active_material.get(pencil.MARKER) == "#FF8000 Fill"
    pencil.command(context, "tool", "builtin.brush")
    assert gp_object.active_material == picked
    pencil.command(context, "material", "Red")
    assert gp_object.active_material.get(pencil.MARKER) == "Red"
    report["picked_colors_make_grease_pencil_materials"] = True
    gp_brush: Any = settings.gpencil_paint.brush
    pencil.command(context, "strength", 0.4)
    pencil.command(context, "hardness", 0.25)
    pencil.command(context, "pressure_strength", False)
    pencil.command(context, "stabilize", True)
    pencil.command(context, "size", 0.2 if gp_brush.use_locked_size == "SCENE" else 30.0)
    assert math.isclose(gp_brush.strength, 0.4, abs_tol=1e-6) and not gp_brush.use_pressure_strength
    assert math.isclose(gp_brush.gpencil_settings.hardness, 0.25, abs_tol=1e-6) and gp_brush.use_smooth_stroke
    if gp_brush.use_locked_size == "SCENE":
        assert math.isclose(gp_brush.unprojected_size, 0.2, abs_tol=1e-6)
    else:
        assert gp_brush.size == 30
    gp_view: pencil.PencilView | None = pencil.view(context)
    assert gp_view is not None and gp_view.brush == "Ink Pen" and gp_view.material == "Red"
    report["native_brush_settings_and_tool_box_view"] = True
    get_state().cursor_active = get_state().cursor_placed = True
    get_state().cursor_axis = 0
    pencil.refresh(context)
    assert (settings.gpencil_stroke_placement_view3d, settings.gpencil_sculpt.lock_axis) == ("CURSOR", "AXIS_X")
    get_state().cursor_active = get_state().cursor_placed = False
    get_state().on_object = True
    pencil.refresh(context)
    assert (settings.gpencil_stroke_placement_view3d, settings.gpencil_sculpt.lock_axis) == ("SURFACE", "VIEW")
    get_state().on_object = False
    pencil.command(context, "placement", "STROKE")
    pencil.command(context, "plane", "AXIS_Y")
    assert (settings.gpencil_stroke_placement_view3d, settings.gpencil_sculpt.lock_axis) == ("STROKE", "AXIS_Y")
    pencil.command(context, "placement", "ORIGIN")
    drawing_plane: Any = layers.create_plane(context)
    drawing_plane.location = (1.0, 2.0, 3.0)
    drawing_plane.rotation_euler = (0.3, 0.0, 0.2)
    context.view_layer.update()
    pencil.refresh(context)
    plane_layer: Any = gp_object.data.layers.active
    assert plane_layer.parent == drawing_plane and context.view_layer.objects.active == gp_object
    assert (Vector(plane_layer.translation) - drawing_plane.matrix_world.translation).length < 1e-6
    assert (settings.gpencil_stroke_placement_view3d, settings.gpencil_sculpt.lock_axis) == ("ORIGIN", "AXIS_Z")
    get_state().drawing_plane = False
    pencil.refresh(context)
    assert gp_object.data.layers.active.name == pencil.LAYER
    report["canvas_options_choose_native_placement_and_plane_layers"] = True
    locked_group: str = "collection:" + get_state().active_group
    layers.set_locked(context, locked_group, True)
    assert not pencil.enter(context) and gp_object.mode == "OBJECT"
    layers.set_locked(context, locked_group, False)
    assert pencil.enter(context)
    pencil.leave(context)
    assert gp_object.mode == "OBJECT"
    report["locked_group_blocks_draw_mode"] = True
    duplicate: Any = gp_object.copy()
    duplicate.data = gp_object.data.copy()
    layers.link_stroke(duplicate, context)
    gp_object.select_set(False)
    duplicate.select_set(True)
    context.view_layer.objects.active = duplicate
    assert pencil.enter(context) and context.view_layer.objects.active == duplicate
    pencil.leave(context)
    assert gp_object.mode == duplicate.mode == "OBJECT"
    report["selected_grease_pencil_duplicate_remains_editable"] = True
    sampled_material: Any = bpy.data.materials.new("Sampled ink")
    bpy.data.materials.create_gpencil_data(sampled_material)
    sampled_material.grease_pencil.color = (0.2, 0.3, 0.4, 1.0)
    duplicate.data.materials.append(sampled_material)
    duplicate.active_material_index = len(duplicate.material_slots) - 1
    pencil.command(context, "tool", "builtin.brush")
    assert duplicate.active_material == sampled_material
    pencil.command(context, "plane", "AXIS_Z")
    assert duplicate.active_material == sampled_material
    gp_view = pencil.view(context)
    assert gp_view is not None and gp_view.material == sampled_material.name
    pencil.command(context, "material", "Blue")
    assert duplicate.active_material.get(pencil.MARKER) == "Blue"
    pencil.leave(context)
    report["sampled_native_material_survives_tool_and_canvas_changes"] = True
with context.temp_override(area=area, region=region):
    selection_data: Any = bpy.data.grease_pencils.new("Stroke Selection Probe")
    selection_layer: Any = selection_data.layers.new("Strokes", set_active=True)
    selection_drawing: Any = selection_layer.frames.new(int(context.scene.frame_current)).drawing
    selection_drawing.add_strokes([3, 3, 3])
    stroke_index: int
    native_stroke: Any
    for stroke_index, native_stroke in enumerate(selection_drawing.strokes):
        point_index: int
        native_point: Any
        for point_index, native_point in enumerate(native_stroke.points):
            native_point.position = (float(point_index), float(stroke_index), 0.0)
    selection_object: Any = bpy.data.objects.new("Stroke Selection Probe", selection_data)
    layers.link_stroke(selection_object, context)
    context.view_layer.objects.active = selection_object
    selection_object.select_set(True)
    get_state().tool = "select"
    assert pencil.enter_selection(context)
    assert settings.gpencil_selectmode_edit == "STROKE"
    assert not any(native_stroke.select for native_stroke in selection_drawing.strokes)
    stroke_rows: list[layers.LayerRow] = [
        row for row in layers.layer_rows(context) if row.key.startswith(f"stroke:{selection_object.name}:")
    ]
    assert [row.name for row in stroke_rows] == ["Stroke 1", "Stroke 2", "Stroke 3"]
    pencil.leave(context)
    assert pencil.select_row(context, stroke_rows[0].key)
    assert [native_stroke.select for native_stroke in selection_drawing.strokes] == [True, False, False]
    bpy.ops.transform.translate(value=(1.0, 0.0, 0.0))
    assert abs(selection_drawing.strokes[0].points[0].position.x - 1.0) < 1e-6
    assert abs(selection_drawing.strokes[1].points[0].position.x) < 1e-6
    assert pencil.select_row(context, stroke_rows[2].key, extend=True)
    assert [native_stroke.select for native_stroke in selection_drawing.strokes] == [True, False, True]
    assert pencil.selection_command(context, "duplicate")
    assert len(selection_drawing.strokes) == 5
    assert len(pencil.stroke_rows(selection_object, 0, True, False)) == 5
    assert pencil.selection_command(context, "delete")
    assert len(selection_drawing.strokes) == 3
    assert len(pencil.stroke_rows(selection_object, 0, True, False)) == 3
    assert context.view_layer.objects.active == selection_object and selection_object.data == selection_data
    pencil.leave(context)
    report["individual_and_multiple_strokes_stay_in_same_object"] = True
get_state().tool = "select"
report["passed"] = True
output: Path = Path(__file__).resolve().parents[1] / ".checks/drawing-report.json"
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
