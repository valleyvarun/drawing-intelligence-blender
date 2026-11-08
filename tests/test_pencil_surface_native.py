"""Check translucent-fill visibility in a small native offscreen depth buffer."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from drawing_app.core import state as state_module
from drawing_app.core.blender import Matrix, Quaternion, Vector, bpy, gpu
from drawing_app.core.state import AppState, Vec3, get_state
from drawing_app.tools import layers, pencil
from drawing_app.viewport import fills


def run() -> dict[str, object]:
    window: Any = bpy.context.window
    area: Any = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region: Any = next(region for region in area.regions if region.type == "WINDOW")
    space: Any = area.spaces.active
    view: Any = space.region_3d
    original_scene: Any = window.scene
    original_active: Any = bpy.context.view_layer.objects.active
    original_mode: str = original_active.mode if original_active is not None else "OBJECT"
    original_brush: Any = original_scene.tool_settings.gpencil_paint.brush
    original_native_tool: Any = bpy.context.workspace.tools.from_space_view3d_mode(pencil.MODE, create=False)
    original_tool_id: str = str(original_native_tool.idname) if original_native_tool is not None else ""
    original_state: AppState = get_state()
    original_view: tuple[Any, Any, float, str] = (
        view.view_rotation.copy(),
        view.view_location.copy(),
        float(view.view_distance),
        str(view.view_perspective),
    )
    original_shading: str = str(space.shading.type)
    original_color_type: str = str(space.shading.color_type)
    original_overlays: bool = bool(space.overlay.show_overlays)
    original_shelf: bool = bool(space.show_region_asset_shelf)
    original_data: dict[str, set[str]] = {}
    key: str
    for key in ("objects", "meshes", "materials", "grease_pencils", "collections", "scenes"):
        original_data[key] = set(getattr(bpy.data, key).keys())
    report: dict[str, object] = {"passed": False}
    offscreen: Any = None
    view_matrix: Any = Matrix.Translation((0.0, 0.0, -8.0))
    projection: Any = Matrix(
        ((0.5, 0.0, 0.0, 0.0), (0.0, 0.5, 0.0, 0.0), (0.0, 0.0, -2.0 / 99.9, -100.1 / 99.9), (0.0, 0.0, 0.0, 1.0))
    )
    inverse_projection: Any = (projection @ view_matrix).inverted()

    def surface(name: str, depth: float, translucent: bool) -> Any:
        mesh: Any = bpy.data.meshes.new(name)
        mesh.from_pydata(((-1.4, -1.4, 0.0), (1.4, -1.4, 0.0), (1.4, 1.4, 0.0), (-1.4, 1.4, 0.0)), (), ((0, 1, 2, 3),))
        obj: Any = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.location.z = depth
        material: Any = bpy.data.materials.new(name)
        material.diffuse_color = (0.3, 0.5, 0.8, 0.4 if translucent else 1.0)
        mesh.materials.append(material)
        obj.color = material.diffuse_color
        if translucent:
            obj["drawing_kind"] = "fill"
            obj.rotation_euler = (0.12, 0.22, 0.0)
        bpy.context.view_layer.update()
        return obj

    def project_depth(target: Any) -> list[Vec3]:
        # Render native faces only; the custom translucent pass never writes depth.
        offscreen.draw_view3d(
            bpy.context.scene, bpy.context.view_layer, space, region, view_matrix, projection, do_color_management=False
        )
        with offscreen.bind():
            buffer: Any = gpu.state.active_framebuffer_get().read_depth(0, 0, 256, 256)
            buffer.dimensions = 256 * 256
            depths: list[float] = list(buffer)
        positions: list[Vec3] = []
        x: float
        for x in (-0.6, 0.0, 0.6):
            world: Any = target.matrix_world @ Vector((x, 0.2, 0.0))
            clip: Any = projection @ view_matrix @ Vector((*world, 1.0))
            px: int = int((float(clip.x / clip.w) + 1.0) * 128.0)
            py: int = int((float(clip.y / clip.w) + 1.0) * 128.0)
            depth: float = depths[py * 256 + px]
            if depth >= 0.99999:
                continue
            position: Any = inverse_projection @ Vector(
                ((px + 0.5) / 128.0 - 1.0, (py + 0.5) / 128.0 - 1.0, depth * 2.0 - 1.0, 1.0)
            )
            positions.append(
                (float(position.x / position.w), float(position.y / position.w), float(position.z / position.w))
            )
        return positions

    def maximum_distance(points: list[Vec3], target: Any) -> float:
        inverse: Any = target.matrix_world.inverted()
        maximum: float = 0.0
        position: Vec3
        for position in points:
            maximum = max(maximum, abs(float((inverse @ Vector(position)).z)))
        return maximum

    try:
        with (
            bpy.context.temp_override(window=window, area=area, region=region),
            patch.object(pencil, "_undo_push"),
            patch.object(layers, "ensure_scene"),
        ):
            if original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            window.scene = bpy.data.scenes.new("Grease Pencil surface validation")
            state_module._STATE = deepcopy(original_state)
            state: AppState = get_state()
            state.tool = "grease"
            state.on_object = True
            state.drawing_plane = False
            state.cursor_active = False
            state.cursor_placing = False
            state.pencil.tool = "builtin.brush"
            state.pencil.brush = "Pencil"
            state.pencil.plane = "VIEW"
            group: Any = bpy.data.collections.new("Surface validation group")
            group[layers.GROUP] = True
            window.scene.collection.children.link(group)
            state.active_group = group.name
            space.shading.type = "SOLID"
            space.shading.color_type = "MATERIAL"
            space.overlay.show_overlays = False
            view.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
            view.view_location = (0.0, 0.0, 0.0)
            view.view_distance = 8.0
            view.view_perspective = "ORTHO"
            view.update()
            fill: Any = surface("Translucent fill surface", 2.0, True)
            rgba: tuple[float, ...] = tuple(fill.active_material.diffuse_color)
            fills.install()
            fills.prepare(fill)
            assert pencil.enter(bpy.context)
            settings: Any = bpy.context.scene.tool_settings
            settings.gpencil_surface_offset = 0.0
            assert settings.gpencil_stroke_placement_view3d == "SURFACE"
            bpy.context.view_layer.update()
            offscreen = gpu.types.GPUOffScreen(256, 256)
            baseline: list[Vec3] = project_depth(fill)
            assert not baseline, baseline
            with fills.selection_surfaces(include_zero_opacity=True) as exposed:
                assert exposed
                projected: list[Vec3] = project_depth(fill)
            assert len(projected) == 3, projected
            projected_distance: float = maximum_distance(projected, fill)
            assert projected_distance < 0.01, projected_distance
            assert not fill.show_instancer_for_viewport
            assert tuple(fill.active_material.diffuse_color) == rgba
            fill.active_material.diffuse_color[3] = 0.0
            fills.prepare(fill)
            with fills.selection_surfaces(include_zero_opacity=True) as exposed:
                assert exposed
                invisible: list[Vec3] = project_depth(fill)
            assert len(invisible) == 3, invisible
            invisible_distance: float = maximum_distance(invisible, fill)
            assert invisible_distance < 0.01, invisible_distance
            assert not fill.show_instancer_for_viewport
            fill.active_material.diffuse_color = rgba
            nearest: Any = surface("Ordinary foreground mesh", 3.0, False)
            with fills.selection_surfaces(include_zero_opacity=True) as exposed:
                assert exposed
                foreground: list[Vec3] = project_depth(nearest)
            assert len(foreground) == 3, foreground
            foreground_distance: float = maximum_distance(foreground, nearest)
            assert foreground_distance < 0.01, foreground_distance
            assert not fill.show_instancer_for_viewport
            report.update(
                passed=True,
                hidden_fill_has_no_native_depth=True,
                fill_surface_distance=projected_distance,
                nearest_mesh_distance=foreground_distance,
                translucent_display_restored=True,
                fill_rgba_unchanged=True,
                zero_opacity_surface_distance=invisible_distance,
                method="256x256 native offscreen depth projection",
            )
    finally:
        with bpy.context.temp_override(window=window, area=area, region=region):
            active: Any = bpy.context.view_layer.objects.active
            if active is not None and active.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            fills.remove()
            if offscreen is not None:
                offscreen.free()
            window.scene = original_scene
            bpy.context.view_layer.objects.active = original_active
            view.view_rotation, view.view_location, view.view_distance, view.view_perspective = original_view
            view.update()
            space.shading.type = original_shading
            space.shading.color_type = original_color_type
            space.overlay.show_overlays = original_overlays
            if original_active is not None and original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=original_mode)
                if original_mode == pencil.MODE and original_tool_id:
                    bpy.ops.wm.tool_set_by_id(name=original_tool_id, space_type="VIEW_3D")
            space.show_region_asset_shelf = original_shelf
            state_module._STATE = original_state
            data: Any
            for key in ("objects", "meshes", "materials", "grease_pencils", "collections", "scenes"):
                for data in tuple(getattr(bpy.data, key)):
                    if data.name not in original_data[key]:
                        getattr(bpy.data, key).remove(data, do_unlink=True)
            area.tag_redraw()
            assert original_scene.tool_settings.gpencil_paint.brush == original_brush
        output: Path = Path(__file__).resolve().parents[1] / ".checks/pencil-surface-report.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    return report


run()
