"""Pipe fill/trim adapters in an isolated temporary scene in an existing Blender UI."""

from __future__ import annotations

import json
import math
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from drawing_app.core import controller
from drawing_app.core import state as state_module
from drawing_app.core.blender import Matrix, Vector, bpy, view3d_utils
from drawing_app.core.state import Brush, Tool, Vec3, get_state
from drawing_app.tools import canvas, erase, layers, pencil, pipe_edit, pipe_fill
from drawing_app.tools import drawing as drawing_module
from drawing_app.tools.drawing import DrawingController, write_mesh
from drawing_app.tools.geometry import Sample
from drawing_app.ui import overlay


def run() -> dict[str, object]:
    registered: list[Any] = []
    cls: Any
    try:
        for cls in (*erase.CLASSES, controller.DRAWING_OT_select, controller.DRAWING_OT_session):
            if not cls.is_registered:
                bpy.utils.register_class(cls)
                registered.append(cls)
    finally:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
    window: Any = bpy.context.window
    original_scene: Any = window.scene
    original_active: Any = bpy.context.view_layer.objects.active
    original_mode: str = original_active.mode if original_active is not None else "OBJECT"
    original_group: str = get_state().active_group
    original_objects: set[str] = set(bpy.data.objects.keys())
    original_meshes: set[str] = set(bpy.data.meshes.keys())
    original_materials: set[str] = set(bpy.data.materials.keys())
    original_pencils: set[str] = set(bpy.data.grease_pencils.keys())
    original_collections: set[str] = set(bpy.data.collections.keys())
    original_status: str = get_state().status
    original_tool: str = get_state().pencil.tool
    original_app_tool: Tool = get_state().tool
    scene: Any = bpy.data.scenes.new("Physical pipe editing probe")
    report: dict[str, object] = {"passed": False, "startup_operator_registration": True}
    area: Any = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region: Any = next(region for region in area.regions if region.type == "WINDOW")
    view: Any = area.spaces.active.region_3d
    original_view: tuple[Any, Any, float, str] = (
        view.view_rotation.copy(),
        view.view_location.copy(),
        float(view.view_distance),
        str(view.view_perspective),
    )
    original_native_tool: str = pencil.active_tool(bpy.context)
    original_shelf: bool = bool(area.spaces.active.show_region_asset_shelf)
    start_times: list[float] = []
    tick_times: list[float] = []
    completion_times: list[float] = []

    def complete_fill() -> None:
        started: float = time.perf_counter()
        deadline: float = started + 10.0
        with patch.object(pipe_edit, "network", side_effect=AssertionError("Fill must not inspect pipe meshes")):
            while pipe_fill.busy() and time.perf_counter() < deadline:
                tick_started: float = time.perf_counter()
                pipe_fill.tick()
                tick_times.append(time.perf_counter() - tick_started)
                if pipe_fill.busy():
                    time.sleep(0.005)
        completion_times.append(time.perf_counter() - started)
        assert not pipe_fill.busy(), "Fill worker did not complete within 10 seconds"
        assert not pipe_fill.message()

    def fill_at(origin: Any, direction: Any) -> None:
        started: float = time.perf_counter()
        with (
            patch.object(canvas, "ray", return_value=(origin, direction)),
            patch.object(pipe_edit, "network", side_effect=AssertionError("Fill must not inspect pipe meshes")),
        ):
            assert pipe_edit.fill(bpy.context, SimpleNamespace(), area, region)
        start_times.append(time.perf_counter() - started)
        complete_fill()

    def pipe(name: str, points: list[Vec3], shape: str = "circle") -> Any:
        mesh: Any = bpy.data.meshes.new(name)
        obj: Any = bpy.data.objects.new(name, mesh)
        layers.link_stroke(obj, bpy.context)
        obj["drawing_kind"] = "pipe"
        obj["drawing_shape"] = shape
        obj.color = (0.1, 0.2, 0.3, 1.0)
        samples: list[Sample] = []
        flattened: list[float] = []
        position: Vec3
        for position in points:
            sample: Sample = Sample(position, 0.04, 0.8, 1.0)
            samples.append(sample)
            flattened.extend((*position, sample.radius, sample.hardness, sample.opacity, *sample.normal))
        write_mesh(obj, samples, Brush(color=(0.1, 0.2, 0.3), shape=shape), "pipe")
        obj["drawing_samples"] = flattened
        return obj

    try:
        with (
            bpy.context.temp_override(window=window, area=area, region=region),
            patch.object(pencil, "_undo_push"),
            patch.object(pipe_fill, "_native_fill", return_value=False),
            patch.object(state_module, "_STATE", deepcopy(get_state())),
        ):
            if original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            window.scene = scene
            group: Any = bpy.data.collections.new("Pipe editing validation")
            group[layers.GROUP] = True
            scene.collection.children.link(group)
            get_state().active_group = group.name
            boundary: Any = pipe("Offset open boundary", [(-1, -1, 0), (1, -1, 0), (1, 1, 0)])
            boundary_samples: tuple[float, ...] = tuple(boundary["drawing_samples"])
            boundary.matrix_world = Matrix.Translation((4.0, 2.0, 5.0)) @ Matrix.Rotation(0.6, 4, "Y")
            bpy.context.view_layer.update()
            ray_origin: Any = boundary.matrix_world @ Vector((0.5, -0.5, 3.0))
            ray_direction: Any = boundary.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))
            session: Any = SimpleNamespace(
                closed=False,
                drawing=SimpleNamespace(is_drawing=False, clear_hover=MagicMock()),
                navigation=SimpleNamespace(active=False, handle=MagicMock(return_value=False)),
            )
            click: SimpleNamespace = SimpleNamespace(
                type="LEFTMOUSE",
                value="PRESS",
                mouse_x=region.x + region.width // 2,
                mouse_y=region.y + region.height // 2,
                ctrl=False,
                alt=False,
                shift=False,
                oskey=False,
            )
            get_state().tool, get_state().pencil.tool = "grease", "builtin_brush.Fill"
            get_state().pencil.fill_opacity = 0.35
            with (
                patch.object(canvas, "ray", return_value=(ray_origin, ray_direction)),
                patch.object(overlay, "is_capturing", return_value=False),
                patch.object(overlay, "handle_event", return_value=False),
                patch.object(pipe_edit, "network", side_effect=AssertionError("Fill must not inspect pipe meshes")),
            ):
                started: float = time.perf_counter()
                assert controller.DRAWING_OT_session.modal(session, bpy.context, click) == {"RUNNING_MODAL"}
                start_times.append(time.perf_counter() - started)
                assert pipe_fill.busy()
                assert not any(obj.get("drawing_kind") == "fill" for obj in scene.objects)
                assert controller.DRAWING_OT_session.modal(session, bpy.context, click) == {"RUNNING_MODAL"}
                later: float = time.monotonic() + 0.3
                with patch.object(pipe_fill.time, "monotonic", return_value=later):
                    assert pipe_fill.message() == "Fill processing ..."
            get_state().pencil.fill_opacity = 0.65
            complete_fill()
            fill: Any = next(obj for obj in scene.objects if obj.get("drawing_kind") == "fill")
            assert abs(fill.color[3] - 0.35) < 1e-6
            assert abs(fill.active_material.diffuse_color[3] - 0.35) < 1e-6
            fill_shader: Any
            for fill_shader in fill.active_material.node_tree.nodes:
                if fill_shader.type == "BSDF_PRINCIPLED":
                    assert abs(fill_shader.inputs["Alpha"].default_value - 0.35) < 1e-6
                    break
            else:
                raise AssertionError("Fill material has no surface shader")
            assert fill.active_material.surface_render_method == "DITHERED"
            report["pipe_fill_captures_opacity_on_click_for_viewport_and_shader"] = True
            inverse: Any = boundary.matrix_world.inverted()
            vertex: Any
            for vertex in fill.data.vertices:
                local: Any = inverse @ vertex.co
                assert abs(local.z) < 1e-5, vertex.co
                assert local.y <= local.x + 1e-5
            polygon: Any
            fill_area: float = 0.0
            for polygon in fill.data.polygons:
                fill_area += polygon.area
            assert abs(fill_area - 2.0) < 1e-5
            assert tuple(boundary["drawing_samples"]) == boundary_samples
            assert not fill.select_get()
            report["fill_follows_translated_rotated_pipe_plane"] = True
            report["open_curve_closes_with_straight_diagonal_without_changing_stroke"] = True
            report["fill_tool_input_reaches_pipe_geometry"] = True
            report["fill_returns_before_computation_and_duplicate_click_does_not_queue"] = True
            report["long_fill_has_processing_message_and_completion_clears_it"] = True
            assert len([obj for obj in scene.objects if obj.get("drawing_kind") == "fill"]) == 1
            ray_origin = boundary.matrix_world @ Vector((4.0, 4.0, 3.0))
            fill_at(ray_origin, ray_direction)
            assert len([obj for obj in scene.objects if obj.get("drawing_kind") == "fill"]) == 1
            report["outside_region_does_not_create_origin_plane_fill"] = True
            boundary.hide_set(True)
            target: Any = pipe("Trim target", [(-3, 0, 8), (0, 0, 8), (3, 0, 8)])
            first: Any = pipe("Left junction", [(-1, -1, 8), (-1, 1, 8)])
            second: Any = pipe("Right junction", [(1, -1, 8), (1, 1, 8)])
            assert erase.trim_at(bpy.context, area, region, target, [(0.0, 0.0, 8.0)])
            remaining: list[Any] = [
                obj
                for obj in scene.objects
                if obj.get("drawing_kind") == "pipe" and obj not in (boundary, first, second)
            ]
            assert len(remaining) == 2
            obj: Any
            for obj in remaining:
                sample: Sample
                for sample in pipe_edit.samples(obj):
                    assert abs(sample.position[0]) >= 1.0 - 1e-5
                assert len(obj.data.polygons) > 0 and not obj.select_get()
            report["trim_rebuilds_two_capped_pipe_parts_at_real_junctions"] = True
            detached: Any = pipe("Detached target", [(-3, 4, 8), (3, 4, 8)])
            overpass: Any = pipe("Depth separated crossing", [(0, 3, 9), (0, 5, 9)])
            assert not pipe_edit.network([detached, overpass]).junctions
            # Meeting no other stroke, the whole pipe is trimmed, as Grease Pencil's Trim does; the pipe above stays.
            assert erase.trim_at(bpy.context, area, region, detached, [(0.0, 4.0, 8.0)])
            assert "Detached target" not in bpy.data.objects
            assert overpass.name in bpy.data.objects
            report["projected_crossing_at_different_depth_does_not_trim"] = True
            scaled: Any = pipe("Scaled target", [(-1, 0, 0), (1, 0, 0)])
            scaled.matrix_world = Matrix.Translation((0, 6, 8)) @ Matrix.Diagonal((3.0, 0.1, 0.1, 1.0))
            scaled_overpass: Any = pipe("Scaled overpass", [(0, 5, 8.06), (0, 7, 8.06)])
            bpy.context.view_layer.update()
            assert not pipe_edit.network([scaled, scaled_overpass]).junctions
            report["nonuniform_scale_uses_actual_mesh_contact"] = True
            square: Any = pipe("Square target", [(-2, 14, 8), (2, 14, 8)], "square")
            cutter: Any = pipe("Square overpass", [(0, 13, 8.09), (0, 15, 8.09)], "square")
            assert not pipe_edit.network([square, cutter]).junctions
            cutter.location.z = -0.02
            bpy.context.view_layer.update()
            assert len(pipe_edit.network([square, cutter]).junctions) == 1
            assert erase.trim_at(bpy.context, area, region, square, [(1, 14, 8)])
            assert abs(pipe_edit.samples(square)[0].radius - 0.04 * 2**0.5) < 1e-6
            assert max(sample.position[0] for sample in pipe_edit.samples(square)) <= 1e-5
            report["square_profile_requires_actual_surface_contact_and_keeps_width"] = True
            invoke_trim: MagicMock = MagicMock()
            invoke_erase: MagicMock = MagicMock()
            get_state().pencil.tool = "builtin.trim"
            with (
                patch.object(overlay, "is_capturing", return_value=False),
                patch.object(overlay, "handle_event", return_value=False),
                patch.object(
                    controller,
                    "bpy",
                    SimpleNamespace(ops=SimpleNamespace(drawing=SimpleNamespace(trim=invoke_trim, erase=invoke_erase))),
                ),
            ):
                assert controller.DRAWING_OT_session.modal(session, bpy.context, click) == {"RUNNING_MODAL"}
                invoke_trim.assert_called_once_with("INVOKE_DEFAULT")
                get_state().pencil.tool = "builtin_brush.Erase"
                assert controller.DRAWING_OT_session.modal(session, bpy.context, click) == {"RUNNING_MODAL"}
                invoke_erase.assert_called_once_with("INVOKE_DEFAULT")
                get_state().pencil.tool = "builtin.trim"
            report["trim_tool_input_starts_pipe_gesture"] = True
            report["erase_tool_input_starts_eraser"] = True
            gesture_target: Any = pipe("Gesture target", [(-3, 10, 8), (3, 10, 8)])
            pipe("Gesture left junction", [(-1, 9, 8), (-1, 11, 8)])
            pipe("Gesture right junction", [(1, 9, 8), (1, 11, 8)])
            bpy.context.view_layer.update()
            gesture: Any = SimpleNamespace()

            def draw() -> None:
                erase.DRAWING_OT_trim.draw_gesture(gesture)

            def cancel(context: Any) -> None:
                erase.DRAWING_OT_trim.cancel(gesture, context)

            def add_handler(operator: object) -> None:
                assert operator is gesture

            gesture.draw_gesture, gesture.cancel = draw, cancel
            fake_context: Any = SimpleNamespace(
                area=area, region=region, window_manager=SimpleNamespace(modal_handler_add=add_handler)
            )
            event: SimpleNamespace = SimpleNamespace(type="LEFTMOUSE", value="PRESS", mouse_x=100, mouse_y=100)
            assert erase.DRAWING_OT_trim.invoke(gesture, fake_context, event) == {"RUNNING_MODAL"}
            event.value = "RELEASE"
            # The gesture's rays look straight down onto the pipe's middle.
            with (
                patch.object(erase.View, "ray", return_value=(Vector((0, 10, 12)), Vector((0, 0, -1)))),
                patch.object(erase.Eraser, "_meshes_near", return_value=True),
            ):
                assert erase.DRAWING_OT_trim.modal(gesture, bpy.context, event) == {"FINISHED"}
            assert len(pipe_edit.samples(gesture_target)) == 2
            assert pipe_edit.samples(gesture_target)[-1].position[0] <= -1.0 + 1e-5
            report["trim_gesture_hits_actual_pipe_and_removes_interval"] = True
            pipe("Bottom boundary", [(-1, 19, 5), (1, 19, 5)])
            pipe("Right boundary", [(1, 19, 5), (1, 21, 5)])
            pipe("Top boundary", [(1, 21, 5), (-1, 21, 5)])
            pipe("Left boundary", [(-1, 21, 5), (-1, 19, 5)])
            pipe("Interior hole", [(-0.3, 19.7, 5), (0.3, 19.7, 5), (0.3, 20.3, 5), (-0.3, 20.3, 5), (-0.3, 19.7, 5)])
            bpy.context.view_layer.update()
            get_state().pencil.tool = "builtin_brush.Fill"
            before_fills: set[str] = {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"}
            fill_at(Vector((0.7, 20, 9)), Vector((0, 0, -1)))
            joined_fill: Any = next(
                obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
            )
            assert abs(sum(polygon.area for polygon in joined_fill.data.polygons) - 3.64) < 1e-5
            report["separate_touching_pipes_fill_one_region_and_preserve_holes"] = True

            curve_only: Any = pipe(
                "Curve-only boundary", [(9, 19, 5), (11, 19, 5), (11, 21, 5), (9, 21, 5), (9, 19, 5)]
            )
            curve_only.data.clear_geometry()
            raw: list[float] = list(curve_only["drawing_samples"])
            index: int
            for index in range(3, len(raw), 9):
                raw[index] = 100.0
            curve_only["drawing_samples"] = raw
            curve_only["drawing_shape"] = "triangle"
            bpy.context.view_layer.update()
            before_fills = {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"}
            fill_at(Vector((10, 20, 9)), Vector((0, 0, -1)))
            thick_fill: Any = next(
                obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
            )
            thick_points: list[Vec3] = [canvas.vector_tuple(vertex.co) for vertex in thick_fill.data.vertices]
            for index in range(3, len(raw), 9):
                raw[index] = 0.000001
            curve_only["drawing_samples"] = raw
            curve_only["drawing_shape"] = "circle"
            before_fills.add(thick_fill.name)
            fill_at(Vector((10, 20, 9)), Vector((0, 0, -1)))
            thin_fill: Any = next(
                obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
            )
            assert [canvas.vector_tuple(vertex.co) for vertex in thin_fill.data.vertices] == thick_points
            assert abs(sum(polygon.area for polygon in thin_fill.data.polygons) - 4.0) < 1e-5
            report["fill_ignores_tube_mesh_shape_and_thickness"] = True
            open_boundary: Any = pipe(
                "Open centerline", [(14, 19, 5), (16, 19, 5), (16, 21, 5), (14, 21, 5), (14, 19.03, 5)]
            )
            open_samples: tuple[float, ...] = tuple(open_boundary["drawing_samples"])
            before_fills = {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"}
            fill_at(Vector((15, 20, 9)), Vector((0, 0, -1)))
            closed_fill: Any = next(
                obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
            )
            fill_area = 0.0
            for polygon in closed_fill.data.polygons:
                fill_area += polygon.area
            assert abs(fill_area - 4.0) < 1e-5
            assert tuple(open_boundary["drawing_samples"]) == open_samples
            before_fills.add(closed_fill.name)
            report["open_centerline_gap_fills_without_changing_stroke"] = True

            with patch.object(canvas, "ray", return_value=(Vector((10, 20, 9)), Vector((0, 0, -1)))):
                assert pipe_edit.fill(bpy.context, SimpleNamespace(), area, region)
            get_state().tool = "select"
            pipe_fill.tick()
            assert not pipe_fill.busy()
            assert {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"} == before_fills
            get_state().tool = "grease"
            report["switching_tools_cancels_pending_fill"] = True

            with patch.object(canvas, "ray", return_value=(Vector((10, 20, 9)), Vector((0, 0, -1)))):
                assert pipe_edit.fill(bpy.context, SimpleNamespace(), area, region)
            window.scene = original_scene
            pipe_fill.tick()
            assert not pipe_fill.busy()
            window.scene = scene
            assert {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"} == before_fills
            report["changing_scene_discards_pending_fill"] = True

            with patch.object(canvas, "ray", return_value=(Vector((10, 20, 9)), Vector((0, 0, -1)))):
                assert pipe_edit.fill(bpy.context, SimpleNamespace(), area, region)
            with patch.object(pipe_fill.FillProcess, "poll", return_value=None):
                _iteration: int
                for _iteration in range(20):
                    pipe_fill.tick()
            curve_only.location.x = 0.1
            bpy.context.view_layer.update()
            complete_fill()
            assert {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"} == before_fills
            curve_only.location.x = 0.0
            bpy.context.view_layer.update()
            report["changed_source_transform_discards_computed_fill"] = True

            with patch.object(canvas, "ray", return_value=(Vector((10, 20, 9)), Vector((0, 0, -1)))):
                assert pipe_edit.fill(bpy.context, SimpleNamespace(), area, region)
            with patch.object(pipe_fill.FillProcess, "poll", return_value=None):
                for _iteration in range(20):
                    pipe_fill.tick()
            changed: list[float] = list(curve_only["drawing_samples"])
            changed[0] += 0.1
            curve_only["drawing_samples"] = changed
            complete_fill()
            assert {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"} == before_fills
            curve_only["drawing_samples"] = raw
            report["changed_source_coordinates_discard_computed_fill"] = True

            # Draw through screen rays, then activate Fill in native Draw mode.
            transform: Any = Matrix.Translation((4.0, -7.0, 5.0)) @ Matrix.Rotation(0.6, 4, "Y")
            view.view_rotation = transform.to_quaternion()
            view.view_location = transform.translation
            view.view_distance = 8.0
            view.view_perspective = "PERSP"
            view.update()
            get_state().tool, get_state().axes_spokes = "pipe", False
            get_state().brush.radius = 0.04
            get_state().cursor_active = get_state().cursor_placing = False
            get_state().on_object = False
            get_state().drawing_plane = True

            def at(point: Vec3, kind: str = "MOUSEMOVE", value: str = "NOTHING") -> SimpleNamespace:
                screen: Any = view3d_utils.location_3d_to_region_2d(region, view, transform @ Vector(point))
                assert screen is not None
                return SimpleNamespace(
                    type=kind,
                    value=value,
                    mouse_x=screen.x + region.x,
                    mouse_y=screen.y + region.y,
                    ctrl=False,
                    shift=False,
                    alt=False,
                    oskey=False,
                    is_tablet=False,
                )

            drawer: DrawingController = DrawingController()
            try:
                with patch.object(
                    drawing_module,
                    "bpy",
                    SimpleNamespace(
                        data=bpy.data, ops=SimpleNamespace(ed=SimpleNamespace(undo_push=MagicMock(poll=lambda: False)))
                    ),
                ):
                    with patch.object(
                        canvas,
                        "hit",
                        return_value=canvas.Hit(
                            canvas.vector_tuple(transform @ Vector((1.0, 0.0, 0.0))),
                            canvas.vector_tuple(transform.to_3x3() @ Vector((0.0, 0.0, 1.0))),
                            "plane",
                        ),
                    ):
                        assert drawer.handle(bpy.context, at((1.0, 0.0, 0.0), "LEFTMOUSE", "PRESS"), area, region)
                    index: int
                    for index in range(1, 81):
                        angle: float = math.tau * index / 80
                        point: Vec3 = (math.cos(angle), math.sin(angle), 0.0)
                        if index == 80:
                            point = (0.97, 0.0, 0.0)
                        assert drawer.handle(bpy.context, at(point), area, region)
                    assert drawer.handle(bpy.context, at((0.97, 0.0, 0.0), "LEFTMOUSE", "RELEASE"), area, region)
            finally:
                drawer.destroy()
            get_state().tool, get_state().pencil.tool = "grease", "builtin_brush.Fill"
            assert pencil.enter(bpy.context)
            pencil.sync_shelf(area)
            assert get_state().pencil.tool == "builtin_brush.Fill"
            native_pencil: Any = bpy.context.active_object
            translucent: Any = native_pencil.active_material
            translucent_index: int = native_pencil.active_material_index
            assert abs(translucent.grease_pencil.fill_color[3] - 0.65) < 1e-6
            fill_color: Vec3 = get_state().brushes["grease"].color
            assert pencil.material_slot(native_pencil, fill_color, True) == translucent_index
            pencil.command(bpy.context, "fill_opacity", 0.4)
            assert native_pencil.active_material != translucent
            assert abs(native_pencil.active_material.grease_pencil.fill_color[3] - 0.4) < 1e-6
            assert abs(translucent.grease_pencil.fill_color[3] - 0.65) < 1e-6
            pencil.command(bpy.context, "fill_opacity", 0.65)
            assert native_pencil.active_material == translucent
            report["grease_fill_opacity_reuses_materials_without_changing_previous_fills"] = True
            native_drawing: Any = native_pencil.data.layers.active.current_frame().drawing
            native_drawing.add_strokes([2])
            native_drawing.strokes[0].points[0].position = (20, 20, 0)
            native_drawing.strokes[0].points[1].position = (21, 21, 0)
            native_drawing.tag_positions_changed()
            bpy.context.view_layer.update()
            before_fills = {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"}
            with (
                patch.object(overlay, "is_capturing", return_value=False),
                patch.object(overlay, "handle_event", return_value=False),
            ):
                assert controller.DRAWING_OT_session.modal(
                    session, bpy.context, at((0, 0, 0), "LEFTMOUSE", "PRESS")
                ) == {"RUNNING_MODAL"}
            complete_fill()
            drawn_fill: Any = next(
                obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
            )
            inverse = transform.inverted()
            for vertex in drawn_fill.data.vertices:
                assert abs((inverse @ vertex.co).z) < 1e-5, vertex.co
            assert 3.0 < sum(polygon.area for polygon in drawn_fill.data.polygons) < 3.2
            assert not drawn_fill.select_get()
            assert abs(drawn_fill.active_material.diffuse_color[3] - 0.65) < 1e-6
            assert abs(fill.active_material.diffuse_color[3] - 0.35) < 1e-6
            assert len(native_drawing.strokes) == 1
            report["drawn_imperfect_loop_fills_in_native_fill_mode_with_mixed_strokes"] = True
            report["real_viewport_ray_places_fill_on_offset_tilted_stroke_plane"] = True
            alpha: float
            for alpha in (0.0, 1.0):
                pencil.command(bpy.context, "fill_opacity", alpha)
                assert abs(native_pencil.active_material.grease_pencil.fill_color[3] - alpha) < 1e-6
                before_fills = {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"}
                assert pipe_fill.start(bpy.context, at((0, 0, 0)), area, region)
                complete_fill()
                endpoint_fill: Any = next(
                    obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
                )
                assert abs(endpoint_fill.active_material.diffuse_color[3] - alpha) < 1e-6
            report["fill_opacity_supports_fully_transparent_and_opaque"] = True
            uneven_boundary: Any = pipe(
                "Uneven offset boundary",
                [(-1, -1, 0.06), (1, -1, -0.06), (1, 1, 0.06), (-1, 1, -0.06)],
            )
            uneven_samples: tuple[float, ...] = tuple(uneven_boundary["drawing_samples"])
            uneven_transform: Any = Matrix.Translation((30.0, -10.0, 7.0)) @ Matrix.Rotation(0.6, 4, "Y")
            uneven_boundary.matrix_world = uneven_transform
            bpy.context.view_layer.update()
            before_fills = {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"}
            fill_at(
                uneven_transform @ Vector((0.0, 0.0, 3.0)),
                uneven_transform.to_3x3() @ Vector((0.0, 0.0, -1.0)),
            )
            uneven_fill: Any = next(
                obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
            )
            uneven_inverse: Any = uneven_transform.inverted()
            fitted_face: Any = uneven_fill.data.polygons[0]
            fitted_origin: Any = uneven_fill.data.vertices[fitted_face.vertices[0]].co.copy()
            fitted_normal: Any = fitted_face.normal.copy().normalized()
            assert fitted_normal.length > 0.99
            for vertex in uneven_fill.data.vertices:
                assert abs((uneven_inverse @ vertex.co).z) < 0.02, vertex.co
                assert abs((vertex.co - fitted_origin).dot(fitted_normal)) < 1e-5, vertex.co
            uneven_area: float = 0.0
            for polygon in uneven_fill.data.polygons:
                uneven_area += float(polygon.area)
            assert abs(uneven_area - 4.0) < 0.001, uneven_area
            assert tuple(uneven_boundary["drawing_samples"]) == uneven_samples
            assert not uneven_fill.select_get()
            report["uneven_boundary_fills_on_one_offset_tilted_plane_without_changing_strokes"] = True
            connector_paths: list[list[Vec3]] = [
                [(0.02, 2, 0.001), (0, 2, 0.001)],
                [(0, 0, 0), (2, 0, 0)],
                [(2, 0, 0.03), (2, 2, 0.03)],
                [(2, 2, 0), (0.02, 2, 0)],
                [(0, 2, 0), (0, 0, 0)],
            ]
            connector_transform: Any = Matrix.Translation((40.0, -10.0, 8.0)) @ Matrix.Rotation(0.6, 4, "Y")
            connector_sources: list[tuple[Any, tuple[float, ...]]] = []
            path: list[Vec3]
            for path in connector_paths:
                edge: Any = pipe("Unequal boundary edge", path)
                edge.matrix_world = connector_transform
                connector_sources.append((edge, tuple(edge["drawing_samples"])))
            bpy.context.view_layer.update()
            before_fills = {obj.name for obj in scene.objects if obj.get("drawing_kind") == "fill"}
            fill_at(
                connector_transform @ Vector((1.0, 1.0, 3.0)),
                connector_transform.to_3x3() @ Vector((0.0, 0.0, -1.0)),
            )
            connector_fill: Any = next(
                obj for obj in scene.objects if obj.get("drawing_kind") == "fill" and obj.name not in before_fills
            )
            connector_area: float = 0.0
            for polygon in connector_fill.data.polygons:
                connector_area += float(polygon.area)
            assert abs(connector_area - 4.0) < 0.02, connector_area
            source_values: tuple[float, ...]
            for edge, source_values in connector_sources:
                assert tuple(edge["drawing_samples"]) == source_values
            report["short_offset_connector_does_not_reject_larger_boundary"] = True
            report["fill_start_max_ms"] = round(max(start_times) * 1000.0, 3)
            report["fill_tick_max_ms"] = round(max(tick_times) * 1000.0, 3)
            report["fill_completion_max_ms"] = round(max(completion_times) * 1000.0, 3)
            report["passed"] = True
    finally:
        pipe_fill.cancel(close=True)
        with bpy.context.temp_override(window=window, area=area, region=region):
            current: Any = bpy.context.view_layer.objects.active
            if current is not None and current.mode != "OBJECT":
                if current.mode == pencil.MODE and original_native_tool:
                    bpy.ops.wm.tool_set_by_id(name=original_native_tool, space_type="VIEW_3D")
                bpy.ops.object.mode_set(mode="OBJECT")
            window.scene = original_scene
            view.view_rotation, view.view_location, view.view_distance, view.view_perspective = original_view
            if original_active is not None and original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=original_mode)
            area.spaces.active.show_region_asset_shelf = original_shelf
            view.update()
            area.tag_redraw()
        get_state().active_group = original_group
        get_state().status = original_status
        get_state().pencil.tool = original_tool
        get_state().tool = original_app_tool
        obj: Any
        for obj in tuple(bpy.data.objects):
            if obj.name not in original_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        mesh: Any
        for mesh in tuple(bpy.data.meshes):
            if mesh.name not in original_meshes:
                bpy.data.meshes.remove(mesh)
        data: Any
        for data in tuple(bpy.data.grease_pencils):
            if data.name not in original_pencils:
                bpy.data.grease_pencils.remove(data)
        material: Any
        for material in tuple(bpy.data.materials):
            if material.name not in original_materials:
                bpy.data.materials.remove(material)
        bpy.data.scenes.remove(scene)
        collection: Any
        for collection in tuple(bpy.data.collections):
            if collection.name not in original_collections:
                bpy.data.collections.remove(collection)
        output: Path = Path(__file__).resolve().parents[1] / ".checks/pipe-editing-native-report.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


print(json.dumps(run(), indent=2))
