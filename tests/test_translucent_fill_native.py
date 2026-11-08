"""Check fill compositing with native Grease Pencil in an isolated viewport scene."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from drawing_app.core.blender import Matrix, Quaternion, Vector, bpy, gpu, view3d_utils
from drawing_app.viewport import fills


def difference(left: list[float], right: list[float]) -> float:
    maximum: float = 0.0
    a: float
    b: float
    for a, b in zip(left, right):
        maximum = max(maximum, abs(a - b))
    return maximum


def quad(
    name: str, bounds: tuple[float, float, float, float], depth: float, color: tuple[float, float, float, float]
) -> Any:
    left: float
    right: float
    bottom: float
    top: float
    left, right, bottom, top = bounds
    mesh: Any = bpy.data.meshes.new(name)
    mesh.from_pydata(
        ((left, bottom, depth), (right, bottom, depth), (right, top, depth), (left, top, depth)),
        (),
        ((0, 1, 2), (0, 2, 3)),
    )
    obj: Any = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    material: Any = bpy.data.materials.new(name)
    material.diffuse_color = color
    obj.data.materials.append(material)
    obj.color = color
    return obj


def run() -> None:
    window: Any = bpy.context.window
    area: Any = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region: Any = next(region for region in area.regions if region.type == "WINDOW")
    space: Any = area.spaces.active
    view: Any = space.region_3d
    original_scene: Any = window.scene
    original_active: Any = bpy.context.view_layer.objects.active
    original_mode: str = original_active.mode if original_active is not None else "OBJECT"
    original_view: tuple[Any, Any, float, str] = (
        view.view_rotation.copy(),
        view.view_location.copy(),
        view.view_distance,
        view.view_perspective,
    )
    shading_names: tuple[str, ...] = (
        "type",
        "light",
        "color_type",
        "show_shadows",
        "show_cavity",
        "show_object_outline",
        "background_type",
        "background_color",
    )
    original_shading: dict[str, Any] = {}
    key: str
    for key in shading_names:
        value: Any = getattr(space.shading, key)
        original_shading[key] = tuple(value) if key == "background_color" else value
    overlay_names: tuple[str, ...] = (
        "show_overlays",
        "show_extras",
        "show_floor",
        "show_axis_x",
        "show_axis_y",
        "show_axis_z",
        "show_cursor",
    )
    original_overlay: dict[str, bool] = {key: bool(getattr(space.overlay, key)) for key in overlay_names}
    originals: dict[str, set[str]] = {}
    for key in ("objects", "meshes", "materials", "grease_pencils", "scenes", "images"):
        originals[key] = set(getattr(bpy.data, key).keys())
    visible: bool
    output: Any = None
    report: dict[str, object] = {}
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            if original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            scene: Any = bpy.data.scenes.new("Translucent fill validation")
            window.scene = scene
            view.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
            view.view_location = (0.0, 0.0, 0.0)
            view.view_distance = 5.0
            view.view_perspective = "ORTHO"
            view.update()
            space.shading.type = "SOLID"
            space.shading.light = "FLAT"
            space.shading.color_type = "MATERIAL"
            space.shading.show_shadows = False
            space.shading.show_cavity = False
            space.shading.show_object_outline = False
            space.shading.background_type = "VIEWPORT"
            space.shading.background_color = (0.8, 0.8, 0.8)
            space.overlay.show_overlays = False
            green: tuple[float, float, float, float] = (0.03, 0.6, 0.05, 1.0)
            pencil_data: Any = bpy.data.grease_pencils.new("Transparency probe strokes")
            pencil_data.stroke_depth_order = "3D"
            pencil_obj: Any = bpy.data.objects.new(pencil_data.name, pencil_data)
            scene.collection.objects.link(pencil_obj)
            ink: Any = bpy.data.materials.new("Transparency probe ink")
            bpy.data.materials.create_gpencil_data(ink)
            ink.grease_pencil.color = green
            ink.grease_pencil.show_stroke = True
            ink.grease_pencil.show_fill = False
            pencil_data.materials.append(ink)
            layer: Any = pencil_data.layers.new("Probe", set_active=True)
            drawing: Any = layer.frames.new(1).drawing
            drawing.add_strokes([2, 2])
            index: int
            point: Any
            for index in range(2):
                stroke: Any = drawing.strokes[index]
                for point in stroke.points:
                    point.radius = 0.065
                    point.opacity = 1.0
                stroke.points[0].position = ((-0.8, 0.6)[index], -1.4, (-0.3, 0.3)[index])
                stroke.points[1].position = ((-0.8, 0.6)[index], 1.4, (-0.3, 0.3)[index])
                stroke.material_index = 0
            drawing.tag_positions_changed()
            quad("Probe pipe", (-0.16, -0.04, -1.4, 1.4), -0.3, green)
            fill: Any = quad("Probe fill", (-1.3, 1.3, -1.0, 1.0), 0.0, (0.35, 0.35, 0.35, 0.5))
            fill["drawing_kind"] = "fill"
            quad("Probe opaque blocker", (-1.3, 1.3, -0.7, -0.4), 0.6, (0.7, 0.03, 0.03, 1.0))
            bpy.context.view_layer.update()
            output = gpu.types.GPUOffScreen(256, 256)
            view_matrix: Any = Matrix.Translation((0.0, 0.0, -5.0))
            projection: Any = Matrix(
                (
                    (0.5, 0.0, 0.0, 0.0),
                    (0.0, 0.5, 0.0, 0.0),
                    (0.0, 0.0, -2.0 / 99.9, -100.1 / 99.9),
                    (0.0, 0.0, 0.0, 1.0),
                )
            )

            def capture(name: str, custom: bool) -> dict[str, list[float]]:
                output.draw_view3d(
                    scene, bpy.context.view_layer, space, region, view_matrix, projection, do_color_management=False
                )
                with output.bind():
                    if custom:
                        with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                            gpu.matrix.load_matrix(view_matrix)
                            gpu.matrix.load_projection_matrix(projection)
                            fills._draw()
                    buffer: Any = gpu.state.active_framebuffer_get().read_color(0, 0, 256, 256, 4, 0, "FLOAT")
                    buffer.dimensions = 256 * 256 * 4
                    pixels: list[float] = list(buffer)
                picture: Any = bpy.data.images.new(name, 256, 256, alpha=True, float_buffer=True)
                picture.pixels.foreach_set(pixels)
                picture.filepath_raw = str(Path(__file__).resolve().parents[1] / ".checks" / f"{name}.png")
                picture.file_format = "PNG"
                picture.save()
                samples: dict[str, list[float]] = {}
                label: str
                location: tuple[float, float]
                for label, location in {
                    "gp_behind": (-0.8, 0.0),
                    "pipe_behind": (-0.1, 0.0),
                    "gp_front": (0.6, 0.0),
                    "fill": (1.1, 0.0),
                    "blocked_gp": (-0.8, -0.55),
                    "bare_gp": (-0.8, 1.2),
                }.items():
                    offset: int = (round(128 + location[1] * 64) * 256 + round(128 + location[0] * 64)) * 4
                    samples[label] = pixels[offset : offset + 4]
                return samples

            native: dict[str, list[float]] = capture("fill-native-before", False)
            report["native"] = native
            assert abs(native["gp_behind"][1] - native["gp_behind"][0]) < 0.01
            assert native["pipe_behind"][1] - native["pipe_behind"][0] > 0.2
            fills.install()
            fills.prepare(fill)
            bpy.context.view_layer.update()
            fixed: dict[str, list[float]] = capture("fill-fixed-after", True)
            report["fixed"] = fixed
            assert fixed["gp_behind"][1] - fixed["gp_behind"][0] > 0.2
            assert difference(fixed["gp_behind"], fixed["pipe_behind"]) < 0.01
            assert difference(fixed["gp_front"], fixed["bare_gp"]) < 0.01
            assert fixed["blocked_gp"][0] - fixed["blocked_gp"][1] > 0.5
            assert difference(fixed["fill"], native["fill"]) < 0.01
            assert scene.ray_cast(bpy.context.evaluated_depsgraph_get(), (1.1, 0.0, 5.0), (0.0, 0.0, -1.0))[4] == fill
            # A rectangle wholly inside the face must remain selectable.
            corner_a: Any = view3d_utils.location_3d_to_region_2d(region, view, Vector((0.9, 0.1, 0.0)))
            corner_b: Any = view3d_utils.location_3d_to_region_2d(region, view, Vector((1.15, 0.3, 0.0)))
            with fills.selection_surfaces() as exposed:
                assert exposed
                bpy.ops.view3d.select_box(
                    xmin=round(corner_a.x),
                    xmax=round(corner_b.x),
                    ymin=round(corner_a.y),
                    ymax=round(corner_b.y),
                    mode="SET",
                    wait_for_input=False,
                )
                assert fill.select_get(), "A box inside the fill face did not select it"
            fill.select_set(False)
            assert not fill.show_instancer_for_viewport
            report["fill_interior_selection_passed"] = True
            fill.active_material.diffuse_color[3] = 1.0
            fills.prepare(fill)
            bpy.context.view_layer.update()
            opaque: dict[str, list[float]] = capture("fill-opaque", True)
            assert difference(opaque["gp_behind"], opaque["fill"]) < 0.01
            assert difference(opaque["pipe_behind"], opaque["fill"]) < 0.01
            fill.active_material.diffuse_color[3] = 0.0
            fills.prepare(fill)
            bpy.context.view_layer.update()
            invisible: dict[str, list[float]] = capture("fill-invisible", True)
            assert difference(invisible["gp_behind"], invisible["bare_gp"]) < 0.01
            report["opacity_zero_and_one_passed"] = True
            report["passed"] = True
            print(json.dumps(report, indent=2))
            (Path(__file__).resolve().parents[1] / ".checks/fill-transparency-report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
    finally:
        fills.remove()
        if output is not None:
            output.free()
        window.scene = original_scene
        bpy.context.view_layer.objects.active = original_active
        view.view_rotation, view.view_location, view.view_distance, view.view_perspective = original_view
        view.update()
        for key, value in original_shading.items():
            setattr(space.shading, key, value)
        for key, visible in original_overlay.items():
            setattr(space.overlay, key, visible)
        data: Any
        for key in ("objects", "meshes", "materials", "grease_pencils", "scenes", "images"):
            for data in tuple(getattr(bpy.data, key)):
                if data.name not in originals[key]:
                    getattr(bpy.data, key).remove(data, do_unlink=True)
        if original_mode != "OBJECT":
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.object.mode_set(mode=original_mode)
        area.tag_redraw()


run()
