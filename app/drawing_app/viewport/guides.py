"""Native solid viewport configuration and depth-aware ruler drawing."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, TypedDict, cast

from ..core.blender import Vector, batch_for_shader, blf, bpy, gpu, view3d_utils
from ..core.state import Vec3, get_state
from .navigation import AXIS_COLORS, GIZMO_CENTER, AxisHandle, axis_handles

Vec2: TypeAlias = tuple[float, float]
Color: TypeAlias = tuple[float, float, float, float]
Rect: TypeAlias = tuple[float, float, float, float]


class FontMetrics(TypedDict):
    characters: str
    advances: dict[str, float]
    cell_width: int
    cell_height: int
    font_size: int


@dataclass(frozen=True)
class Label:
    position: Vec3
    text: str
    rectangle: Rect


_ASSETS: Path = Path(__file__).parent / "assets"
_FONT: FontMetrics = cast(FontMetrics, json.loads((_ASSETS / "ruler_font.json").read_text(encoding="utf-8-sig")))
_HANDLERS: list[Any] = []
_CONFIGURED: dict[int, str] = {}
_LINE_SHADER: Any = None
_TEXT_SHADER: Any = None
_TEXTURE: Any = None
_IMAGE: Any = None
_SHAPE_SHADER: Any = None
_LAST_ERROR: str = ""


def update_clipping(area: Any) -> None:
    """Keep local surface depth precise while retaining the visible ground."""
    if area is None or area.type != "VIEW_3D":
        return
    space: Any = area.spaces.active
    view: Any = space.region_3d
    distance: float = max(float(view.view_distance), 1e-5)
    eye: Any = view.view_matrix.inverted().translation
    near: float = max(1e-6, min(0.05, distance * 0.002))
    far: float = max(2.0, distance * 12.0, abs(float(eye.z)) * 2.0)
    if abs(float(space.clip_start) - near) > near * 1e-5:
        space.clip_start = near
    if abs(float(space.clip_end) - far) > far * 1e-5:
        space.clip_end = far


_SCENE_KEY: int = 0
_TEXT_SIZE: float = 15.0
_LABEL_PIXELS: float = 64.0


def configure_viewport(area: Any) -> None:
    """Keep Blender's native Workbench, floor and selection overlays."""
    if area is None or area.type != "VIEW_3D":
        return
    identifier: int = int(area.as_pointer())
    theme: str = get_state().theme
    first: bool = identifier not in _CONFIGURED
    space: Any = area.spaces.active
    space.overlay.show_cursor = get_state().cursor_active
    if not first and _CONFIGURED[identifier] == theme:
        return
    _CONFIGURED[identifier] = theme
    space.show_region_ui = False
    space.show_region_toolbar = False
    space.show_gizmo_navigate = False
    space.show_gizmo = True
    update_clipping(area)
    space.shading.type = "SOLID"
    space.shading.light = "STUDIO"
    space.shading.color_type = "MATERIAL"
    space.shading.show_shadows = False
    space.shading.show_cavity = True
    space.shading.cavity_type = "SCREEN"
    space.shading.curvature_ridge_factor = 1.0
    space.shading.curvature_valley_factor = 1.0
    space.shading.show_specular_highlight = False
    space.shading.show_object_outline = False
    space.shading.object_outline_color = (0.02, 0.02, 0.02) if theme == "light" else (0.9, 0.9, 0.9)
    space.shading.background_type = "VIEWPORT"
    space.shading.background_color = (0.32, 0.32, 0.32) if theme == "light" else (0.035, 0.035, 0.04)
    space.overlay.show_floor = True
    space.overlay.show_axis_x = True
    space.overlay.show_axis_y = True
    space.overlay.show_axis_z = False
    space.overlay.show_text = False
    space.overlay.show_stats = False
    space.overlay.show_extras = False
    space.overlay.show_cursor = get_state().cursor_active
    space.overlay.show_relationship_lines = False
    space.overlay.show_wireframes = False
    space.overlay.wireframe_threshold = 1.0
    bpy.context.preferences.themes[0].view_3d.wire = (0.015, 0.015, 0.015) if theme == "light" else (0.9, 0.9, 0.9)
    theme_ui: Any = bpy.context.preferences.themes[0].user_interface
    theme_ui.axis_x, theme_ui.axis_y, theme_ui.axis_z = AXIS_COLORS
    if first:
        rv3d: Any = space.region_3d
        rv3d.view_perspective = "PERSP"
        rv3d.view_location = Vector((0.0, 0.0, 0.0))
        rv3d.view_distance = 12.0
        rv3d.view_rotation = Vector((8.0, -10.0, 7.0)).to_track_quat("Z", "Y")
        if not bpy.app.background:
            rv3d.update()
    area.tag_redraw()


def _update_mesh_outlines(scene: Any, depsgraph: Any) -> None:
    """Native wire overlays apply to geometry, excluding drawing strokes and guides."""
    update: Any
    updates: list[Any] = list(depsgraph.updates)
    for update in updates:
        item: Any = update.id
        if isinstance(item, bpy.types.Object) and item.type == "MESH":
            enabled: bool = not bool(
                item.get("drawing_stroke") or item.get("drawing_preview") or item.get("drawing_helper")
            )
            if item.show_wire != enabled:
                item.show_wire = enabled
            if item.show_all_edges != enabled:
                item.show_all_edges = enabled


def grid_spacing(context: Any, area: Any, region: Any) -> float:
    """Shared minor-grid size in Blender units, stable throughout an orbit."""
    rv3d: Any = area.spaces.active.region_3d
    distance: float = float(rv3d.view_distance) if rv3d.is_perspective else 1.0
    half_height: float = distance / max(abs(float(rv3d.window_matrix[1][1])), 1e-12)
    meters: float = max(float(context.scene.unit_settings.scale_length), 1e-12)
    to_display: float = meters * (1000.0 if get_state().units == "mm" else 1.0)
    exponent: int = math.floor(math.log10(max(half_height * to_display, 1e-6) * 0.15) + 1e-10)
    return math.pow(10.0, exponent) / to_display


def ruler_origin(context: Any, area: Any, region: Any) -> Vec3:
    """Follow the view on ground, capped to 20 metres of horizontal travel."""
    if get_state().cursor_active and get_state().cursor_placed:
        cursor: Any = context.scene.cursor.location
        return float(cursor.x), float(cursor.y), float(cursor.z)
    rv3d: Any = area.spaces.active.region_3d
    camera: Any = rv3d.view_matrix.inverted().translation
    direction: Any = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
    radius: float = 20.0 / max(float(context.scene.unit_settings.scale_length), 1e-12)
    height: float = float(camera.z)
    if abs(height) <= radius * 1e-10 and abs(float(direction.z)) <= 1e-10:
        delta: Any = rv3d.view_location - camera
    elif abs(float(direction.z)) > 1e-10:
        delta = direction * abs(height / float(direction.z))
    else:
        delta = Vector((direction.x, direction.y, 0.0)).normalized() * radius
    delta.z = 0.0
    if delta.length > radius:
        delta *= radius / delta.length
    return float(camera.x + delta.x), float(camera.y + delta.y), 0.0


def label_interval(matrix: Any, origin: Vec3, axis: int, step: float, width: float, height: float) -> float | None:
    """Choose world-aligned label intervals from the projected axis scale."""
    clip: Any = matrix @ Vector((*origin, 1.0))
    if clip.w <= 1e-8:
        return None
    vector: list[float] = [0.0, 0.0, 0.0, 0.0]
    vector[axis] = 1.0
    derivative: Any = matrix @ Vector(vector)
    horizontal: float = float(derivative.x * clip.w - clip.x * derivative.w) / float(clip.w * clip.w)
    vertical: float = float(derivative.y * clip.w - clip.y * derivative.w) / float(clip.w * clip.w)
    pixels: float = math.hypot(horizontal * width * 0.5, vertical * height * 0.5)
    if pixels < 1e-8:
        return None
    requested: float = max(step, _LABEL_PIXELS / pixels)
    decade: float = math.pow(10.0, math.floor(math.log10(requested)))
    multiplier: float
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        if decade * multiplier >= requested * (1.0 - 1e-8):
            return decade * multiplier
    return decade * 10.0


def _labels(context: Any, area: Any, region: Any, origin: Vec3, step: float) -> list[Label]:
    rv3d: Any = area.spaces.active.region_3d
    matrix: Any = rv3d.perspective_matrix
    to_display: float = float(context.scene.unit_settings.scale_length) * (1000.0 if get_state().units == "mm" else 1.0)
    candidates: list[tuple[float, int, Label]] = []
    axis: int
    index: int
    for axis in range(3):
        direction: Any = Vector((float(axis == 0), float(axis == 1), float(axis == 2)))
        direction = rv3d.view_rotation.inverted() @ direction
        if math.hypot(float(direction.x), float(direction.y)) < 0.02:
            continue
        interval: float | None = label_interval(matrix, origin, axis, step, float(region.width), float(region.height))
        if interval is None:
            continue
        major: float = interval
        decimals: int = max(0, -int(math.floor(math.log10(max(major * to_display, 1e-12)))))
        middle: int = round(origin[axis] / major)
        for index in range(middle - 48, middle + 49):
            if index == 0:
                continue
            position: list[float] = list(origin)
            position[axis] = index * major
            if abs(position[axis] - origin[axis]) <= major * 1e-9:
                continue
            clip: Any = matrix @ Vector((position[0], position[1], position[2], 1.0))
            if clip.w <= 1e-8 or abs(clip.z) > clip.w:
                continue
            x: float = (float(clip.x / clip.w) + 1.0) * region.width * 0.5 + 4.0
            y: float = (float(clip.y / clip.w) + 1.0) * region.height * 0.5 + 4.0
            text: str = "0" if index == 0 else f"{index * major * to_display:.{decimals}f}"
            width: float = 0.0
            character: str
            for character in text:
                width += _FONT["advances"].get(character, 0.0) * _TEXT_SIZE / _FONT["font_size"]
            bounds: Rect = (x, y, x + width, y + _FONT["cell_height"] * _TEXT_SIZE / _FONT["font_size"])
            if x < 2.0 or y < 2.0 or bounds[2] > region.width - 2 or bounds[3] > region.height - 38:
                continue
            distance: float = (x - region.width * 0.5) ** 2 + (y - region.height * 0.5) ** 2
            candidates.append((distance, len(candidates), Label((position[0], position[1], position[2]), text, bounds)))
    candidates.sort(key=_candidate_distance)
    selected: list[Label] = []
    candidate: tuple[float, int, Label]
    for candidate in candidates:
        label: Label = candidate[2]
        overlap: bool = False
        other: Label
        for other in selected:
            a: Rect = label.rectangle
            b: Rect = other.rectangle
            if a[0] < b[2] + 6.0 and a[2] + 6.0 > b[0] and a[1] < b[3] + 6.0 and a[3] + 6.0 > b[1]:
                overlap = True
                break
        if not overlap:
            selected.append(label)
    return selected


def _candidate_distance(candidate: tuple[float, int, Label]) -> float:
    return candidate[0]


@bpy.app.handlers.persistent
def _invalidate_text_resources(_unused: Any = None) -> None:
    """Undo can replace the atlas Image ID without removing draw callbacks."""
    global _TEXT_SHADER, _TEXTURE, _IMAGE
    _TEXTURE = _TEXT_SHADER = _IMAGE = None


def _text_resources() -> None:
    global _TEXT_SHADER, _TEXTURE, _IMAGE
    if _TEXT_SHADER is not None and _TEXTURE is not None and _IMAGE is not None:
        try:
            if _IMAGE.as_pointer():
                return
        except ReferenceError:
            pass
    _invalidate_text_resources()
    interface: Any = gpu.types.GPUStageInterfaceInfo("drawing_ruler_text_interface")
    interface.smooth("VEC2", "uv")
    info: Any = gpu.types.GPUShaderCreateInfo()
    info.push_constant("MAT4", "viewProjection")
    info.push_constant("VEC2", "viewportSize")
    info.push_constant("VEC4", "textColor")
    info.sampler(0, "FLOAT_2D", "atlas")
    info.vertex_in(0, "VEC3", "position")
    info.vertex_in(1, "VEC2", "offset")
    info.vertex_in(2, "VEC2", "texCoord")
    info.vertex_out(interface)
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source("""void main() {
    gl_Position = viewProjection * vec4(position, 1.0);
    gl_Position.xy += offset * 2.0 / viewportSize * gl_Position.w;
    gl_Position.z -= gl_Position.w * 0.000001;
    uv = texCoord;
}
""")
    info.fragment_source("""void main() {
    float alpha = texture(atlas, uv).a * textColor.a;
    if (alpha < 0.005) discard;
    fragColor = vec4(textColor.rgb, alpha);
}
""")
    _TEXT_SHADER = gpu.shader.create_from_info(info)
    _IMAGE = bpy.data.images.load(str(_ASSETS / "ruler_font.png"), check_existing=True)
    _IMAGE.colorspace_settings.name = "Non-Color"
    _TEXTURE = gpu.texture.from_image(_IMAGE)


def _draw_labels(labels: list[Label], region: Any, rv3d: Any, opacity: float) -> None:
    if not labels:
        return
    _text_resources()
    positions: list[Vec3] = []
    offsets: list[Vec2] = []
    uvs: list[Vec2] = []
    triangles: list[tuple[int, int, int]] = []
    label: Label
    for label in labels:
        left: float = 4.0
        character: str
        for character in label.text:
            index: int = _FONT["characters"].find(character)
            if index < 0:
                continue
            first: int = len(positions)
            low: float = index / len(_FONT["characters"])
            high: float = (index + 1) / len(_FONT["characters"])
            width: float = _FONT["cell_width"] * _TEXT_SIZE / _FONT["font_size"]
            height: float = _FONT["cell_height"] * _TEXT_SIZE / _FONT["font_size"]
            positions.extend([label.position] * 4)
            offsets.extend(((left, 4.0), (left + width, 4.0), (left + width, 4.0 + height), (left, 4.0 + height)))
            uvs.extend(((low, 0.0), (high, 0.0), (high, 1.0), (low, 1.0)))
            triangles.extend(((first, first + 1, first + 2), (first, first + 2, first + 3)))
            left += _FONT["advances"][character] * _TEXT_SIZE / _FONT["font_size"]
    batch: Any = batch_for_shader(
        _TEXT_SHADER, "TRIS", {"position": positions, "offset": offsets, "texCoord": uvs}, indices=triangles
    )
    _TEXT_SHADER.bind()
    _TEXT_SHADER.uniform_float("viewProjection", rv3d.perspective_matrix)
    _TEXT_SHADER.uniform_float("viewportSize", (float(region.width), float(region.height)))
    _TEXT_SHADER.uniform_float("textColor", (1.0, 0.85, 0.1, opacity))
    _TEXT_SHADER.uniform_sampler("atlas", _TEXTURE)
    batch.draw(_TEXT_SHADER)


def _line(points: list[Vec3], color: Color, width: float, region: Any) -> None:
    global _LINE_SHADER
    if _LINE_SHADER is None:
        _LINE_SHADER = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    batch: Any = batch_for_shader(_LINE_SHADER, "LINES", {"pos": points})
    _LINE_SHADER.bind()
    _LINE_SHADER.uniform_float("viewportSize", (float(region.width), float(region.height)))
    _LINE_SHADER.uniform_float("lineWidth", width)
    _LINE_SHADER.uniform_float("color", color)
    batch.draw(_LINE_SHADER)


def _draw_world() -> None:
    global _LAST_ERROR
    context: Any = bpy.context
    area: Any = context.area
    region: Any = context.region
    if area is None or area.type != "VIEW_3D" or region is None or region.type != "WINDOW":
        return
    if not get_state().ruler:
        return
    rv3d: Any = area.spaces.active.region_3d
    depth: str = str(gpu.state.depth_test_get())
    mask: bool = bool(gpu.state.depth_mask_get())
    blend: str = str(gpu.state.blend_get())
    error: RuntimeError | ValueError | TypeError | AttributeError
    try:
        origin: Vec3 = ruler_origin(context, area, region)
        step: float = grid_spacing(context, area, region)
        labels: list[Label] = _labels(context, area, region, origin, step)
        extent: float = min(float(area.spaces.active.clip_end), max(step * 400.0, float(rv3d.view_distance) * 100.0))
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_mask_set(False)
        mode: str
        opacity: float
        for mode, opacity in (("GREATER", 0.16), ("LESS_EQUAL", 1.0)):
            gpu.state.depth_test_set(mode)
            axis: int
            for axis in range(3):
                start: list[float] = list(origin)
                end: list[float] = list(origin)
                start[axis] -= extent
                end[axis] += extent
                _line(
                    [(start[0], start[1], start[2]), (end[0], end[1], end[2])],
                    (*AXIS_COLORS[axis], opacity),
                    1.25,
                    region,
                )
            _draw_labels(labels, region, rv3d, opacity)
        # A small ground marker uses the same depth and ghost treatment as the ruler.
        marker: Vec3 = (origin[0], origin[1], 0.0)
        projected: Any = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(marker))
        if projected is not None:
            near: Any = view3d_utils.region_2d_to_location_3d(
                region, rv3d, (projected.x + 4.0, projected.y), Vector(marker)
            )
            size: float = (near - Vector(marker)).length if near is not None else step * 0.04
            marker_points: list[Vec3] = []
            i: int
            for i in range(24):
                a: float = i * math.tau / 24.0
                b: float = (i + 1) * math.tau / 24.0
                marker_points.extend(
                    (
                        (marker[0] + math.cos(a) * size, marker[1] + math.sin(a) * size, 0.0),
                        (marker[0] + math.cos(b) * size, marker[1] + math.sin(b) * size, 0.0),
                    )
                )
            for mode, opacity in (("GREATER", 0.16), ("LESS_EQUAL", 1.0)):
                gpu.state.depth_test_set(mode)
                _line(marker_points, (1.0, 0.85, 0.1, opacity), 1.5, region)
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        message: str = f"Viewport guide: {error}"
        if message != _LAST_ERROR:
            _LAST_ERROR = message
            print(message)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(mask)
        gpu.state.blend_set(blend)


def _circle(x: float, y: float, radius: float, color: Color) -> None:
    global _SHAPE_SHADER
    if _SHAPE_SHADER is None:
        _SHAPE_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
    points: list[Vec2] = [(x, y)]
    index: int
    for index in range(33):
        angle: float = index * math.tau / 32.0
        points.append((x + math.cos(angle) * radius, y + math.sin(angle) * radius))
    triangles: list[tuple[int, int, int]] = []
    for index in range(1, 33):
        triangles.append((0, index, index + 1))
    batch: Any = batch_for_shader(_SHAPE_SHADER, "TRIS", {"pos": points}, indices=triangles)
    _SHAPE_SHADER.bind()
    _SHAPE_SHADER.uniform_float("color", color)
    batch.draw(_SHAPE_SHADER)


def _draw_corner() -> None:
    context: Any = bpy.context
    area: Any = context.area
    region: Any = context.region
    if area is None or area.type != "VIEW_3D" or region is None or region.type != "WINDOW":
        return
    rv3d: Any = area.spaces.active.region_3d
    blend: str = str(gpu.state.blend_get())
    gpu.state.blend_set("ALPHA")
    try:
        handles: list[AxisHandle] = axis_handles(rv3d)
        handle: AxisHandle
        for handle in handles:
            color: Color = (*AXIS_COLORS[handle.axis], 0.9)
            _line([(GIZMO_CENTER[0], GIZMO_CENTER[1], 0.0), (handle.x, handle.y, 0.0)], color, 1.5, region)
        for handle in handles:
            color = (*AXIS_COLORS[handle.axis], 1.0)
            _circle(handle.x, handle.y, 11.5, color)
            if handle.sign < 0:
                _circle(handle.x, handle.y, 9.5, (0.10, 0.11, 0.12, 1.0))
            text: str = ("-" if handle.sign < 0 else "") + "XYZ"[handle.axis]
            blf.size(0, 11 if handle.sign < 0 else 13)
            dimensions: Any = blf.dimensions(0, text)
            blf.position(0, handle.x - dimensions[0] * 0.5, handle.y - dimensions[1] * 0.5, 0.0)
            blf.color(0, *(color if handle.sign < 0 else (0.05, 0.05, 0.05, 1.0)))
            blf.draw(0, text)
        if get_state().hud:
            blf.size(0, 12)
            blf.color(0, 0.9, 0.9, 0.9, 1.0)
            blf.position(0, 12.0, region.height - 58.0, 0.0)
            blf.draw(0, f"Objects: {len(context.scene.objects)}  Selected: {len(context.selected_objects)}")
    finally:
        gpu.state.blend_set(blend)


def install_guides() -> None:
    global _SCENE_KEY
    scene_key: int = int(bpy.context.scene.as_pointer())
    if scene_key != _SCENE_KEY:
        # File loads replace image data while Python draw callbacks remain installed.
        _SCENE_KEY = scene_key
        _invalidate_text_resources()
    if not _HANDLERS:
        _HANDLERS.append(bpy.types.SpaceView3D.draw_handler_add(_draw_world, (), "WINDOW", "POST_VIEW"))
        _HANDLERS.append(bpy.types.SpaceView3D.draw_handler_add(_draw_corner, (), "WINDOW", "POST_PIXEL"))
    if _update_mesh_outlines not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_update_mesh_outlines)
    handlers: list[Any]
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _invalidate_text_resources not in handlers:
            handlers.append(_invalidate_text_resources)
    item: Any
    for item in bpy.context.scene.objects:
        if item.type == "MESH":
            enabled: bool = not bool(
                item.get("drawing_stroke") or item.get("drawing_preview") or item.get("drawing_helper")
            )
            item.show_wire = enabled
            item.show_all_edges = enabled


def remove_guides() -> None:
    global _LINE_SHADER, _TEXT_SHADER, _TEXTURE, _IMAGE, _SHAPE_SHADER, _SCENE_KEY
    handler: Any
    for handler in _HANDLERS:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
        except (ValueError, ReferenceError):
            pass
    _HANDLERS.clear()
    _CONFIGURED.clear()
    if _update_mesh_outlines in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_update_mesh_outlines)
    handlers: list[Any]
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _invalidate_text_resources in handlers:
            handlers.remove(_invalidate_text_resources)
    _LINE_SHADER = _TEXT_SHADER = _TEXTURE = _SHAPE_SHADER = None
    try:
        if _IMAGE is not None and _IMAGE.users == 0:
            bpy.data.images.remove(_IMAGE)
    except ReferenceError:
        pass
    _IMAGE = None
    _SCENE_KEY = 0
