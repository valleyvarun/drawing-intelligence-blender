"""Native viewport picking in a temporary scene; restores the user's scene and view."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from drawing_app.core import controller
from drawing_app.core.blender import Quaternion, Vector, bpy, view3d_utils
from drawing_app.core.state import Tool, get_state
from drawing_app.tools import drawing as drawing_module
from drawing_app.tools import layers, pencil
from drawing_app.tools.canvas import Hit
from drawing_app.tools.drawing import DrawingController


def run() -> dict[str, object]:
    window: Any = bpy.context.window
    area: Any = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region: Any = next(region for region in area.regions if region.type == "WINDOW")
    space: Any = area.spaces.active
    view: Any = space.region_3d
    original_scene: Any = window.scene
    original_object: Any = bpy.context.view_layer.objects.active
    original_mode: str = original_object.mode if original_object is not None else "OBJECT"
    original_tool: Tool = get_state().tool
    original_status: str = get_state().status
    original_group: str = get_state().active_group
    original_materials: set[str] = set(bpy.data.materials.keys())
    original_collections: set[str] = set(bpy.data.collections.keys())
    original_view: tuple[Any, Any, float, str] = (
        view.view_rotation.copy(),
        view.view_location.copy(),
        float(view.view_distance),
        str(view.view_perspective),
    )
    original_shelf: bool = bool(space.show_region_asset_shelf)
    original_shading: str = str(space.shading.type)
    scene: Any = bpy.data.scenes.new("Stroke selection validation")
    objects: list[Any] = []
    data_blocks: list[Any] = []
    cube_data: Any = None
    stroke_meshes: list[Any] = []
    report: dict[str, object] = {"passed": False}
    material: Any = bpy.data.materials.new("Stroke selection validation")
    bpy.data.materials.create_gpencil_data(material)
    material.grease_pencil.color = (0.0, 0.0, 0.0, 1.0)

    def create_pencil(name: str, xs: tuple[float, ...]) -> Any:
        data: Any = bpy.data.grease_pencils.new(name)
        data_blocks.append(data)
        data.materials.append(material)
        layer: Any = data.layers.new("Strokes", set_active=True)
        drawing: Any = layer.frames.new(1).drawing
        drawing.add_strokes([3] * len(xs))
        index: int
        stroke: Any
        for index, stroke in enumerate(drawing.strokes):
            point_index: int
            point: Any
            for point_index, point in enumerate(stroke.points):
                point.position = (xs[index], float(point_index - 1), 0.0)
                point.radius = 0.04
                point.opacity = 1.0
        drawing.tag_positions_changed()
        obj: Any = bpy.data.objects.new(name, data)
        layers.link_stroke(obj, bpy.context)
        objects.append(obj)
        return obj

    def click(x: float, y: float, extend: bool = False) -> None:
        coordinate: Any = view3d_utils.location_3d_to_region_2d(region, view, Vector((x, y, 0.0)))
        assert coordinate is not None
        event: SimpleNamespace = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            mouse_region_x=round(coordinate.x),
            mouse_region_y=round(coordinate.y),
            shift=extend,
            is_tablet=False,
        )
        operator: Any = SimpleNamespace()
        assert controller.DRAWING_OT_select.poll(bpy.context)
        assert controller.DRAWING_OT_select.invoke(operator, input_context, event) == {"RUNNING_MODAL"}
        event.value = "RELEASE"
        assert controller.DRAWING_OT_select.modal(operator, bpy.context, event) == {"FINISHED"}
        event.value = "CLICK"
        assert controller.DRAWING_OT_select.invoke(operator, bpy.context, event) == {"FINISHED"}

    def register_modal(_operator: object) -> None:
        """Drive modal events synchronously without leaving a handler in the user's window."""

    input_context: Any = SimpleNamespace(
        area=area, preferences=bpy.context.preferences, window_manager=SimpleNamespace(modal_handler_add=register_modal)
    )

    def box(start: tuple[float, float], end: tuple[float, float], extend: bool = False) -> None:
        first: Any = view3d_utils.location_3d_to_region_2d(region, view, Vector((*start, 0.0)))
        last: Any = view3d_utils.location_3d_to_region_2d(region, view, Vector((*end, 0.0)))
        assert first is not None and last is not None
        operator: Any = SimpleNamespace()
        event: SimpleNamespace = SimpleNamespace(
            type="LEFTMOUSE",
            value="PRESS",
            mouse_region_x=round(first.x),
            mouse_region_y=round(first.y),
            shift=extend,
            is_tablet=False,
        )
        assert controller.DRAWING_OT_select.invoke(operator, input_context, event) == {"RUNNING_MODAL"}
        event.type, event.mouse_region_x, event.mouse_region_y = "MOUSEMOVE", round(last.x), round(last.y)
        assert controller.DRAWING_OT_select.modal(operator, bpy.context, event) == {"RUNNING_MODAL"}
        assert operator.handle is not None
        event.type, event.value = "LEFTMOUSE", "RELEASE"
        assert controller.DRAWING_OT_select.modal(operator, bpy.context, event) == {"FINISHED"}
        assert operator.handle is None

    def selected(obj: Any) -> list[bool]:
        values: list[bool] = []
        stroke: Any
        for stroke in obj.data.layers[0].current_frame().drawing.strokes:
            values.append(bool(stroke.select))
        return values

    try:
        with bpy.context.temp_override(window=window, area=area, region=region), patch.object(pencil, "_undo_push"):
            if original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            window.scene = scene
            space.shading.type = "SOLID"
            view.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
            view.view_location = Vector((0.0, 0.0, 0.0))
            view.view_distance = 10.0
            view.view_perspective = "ORTHO"
            obj: Any = create_pencil("Three strokes", (-2.0, 0.0, 2.0))
            second: Any = create_pencil("Another drawing", (3.5,))
            bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0.0, -2.5, 0.0))
            cube: Any = bpy.context.object
            objects.append(cube)
            cube_data = cube.data
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = None
            get_state().tool = "select"
            bpy.context.view_layer.update()
            view.update()

            click(-2.0, 0.0)
            report["first_click"] = selected(obj)
            assert obj.mode == "EDIT" and selected(obj) == [True, False, False], report
            pencil.leave(bpy.context)
            box((-2.5, -1.25), (0.5, 1.25))
            assert selected(obj) == [True, True, False], selected(obj)
            report["rectangle_selects_two_strokes"] = True
            box((1.5, -1.25), (2.5, 1.25), extend=True)
            assert selected(obj) == [True, True, True], selected(obj)
            report["shift_rectangle_adds_strokes"] = True
            box((-1.5, 1.25), (-2.5, -1.25))
            assert selected(obj) == [True, False, False], selected(obj)
            report["reverse_rectangle_replaces_selection"] = True
            kind: Tool
            for kind in ("pipe", "paint"):
                with (
                    patch.object(controller, "viewport_context", return_value=(area, region)),
                    patch("drawing_app.viewport.configure_viewport"),
                    patch.object(
                        drawing_module,
                        "bpy",
                        SimpleNamespace(
                            data=bpy.data,
                            context=bpy.context,
                            ops=SimpleNamespace(ed=SimpleNamespace(undo_push=MagicMock())),
                        ),
                    ),
                    patch("drawing_app.core.project.persist_scene"),
                ):
                    controller.set_tool(kind)
                    drawing: DrawingController = DrawingController()
                    point_event: SimpleNamespace = SimpleNamespace(
                        mouse_x=region.x + 100, mouse_y=region.y + 100, is_tablet=False, pressure=1.0
                    )
                    with patch.object(
                        drawing, "_target", return_value=Hit((0.0, -2.5, 0.0), (0.0, 0.0, 1.0), "ground")
                    ):
                        assert drawing.begin(bpy.context, point_event, area, region)
                    mesh_stroke: Any = drawing._object
                    objects.append(mesh_stroke)
                    stroke_meshes.append(mesh_stroke.data)
                    drawing.finish(bpy.context)
                    drawing.destroy()
                    assert not mesh_stroke.select_get()
                    assert bpy.context.view_layer.objects.active != mesh_stroke
                    report[kind + "_completion_does_not_select_stroke"] = True
                    mesh_stroke.select_set(True)
                    bpy.context.view_layer.objects.active = mesh_stroke
                    controller.set_tool("select")
                    box((-2.5, -1.25), (-1.5, 1.25))
                    assert selected(obj) == [True, False, False], (kind, selected(obj))
                    report[kind + "_then_rectangle_selects_one_grease_pencil_stroke"] = True
                    box((-2.5, -1.25), (0.5, 1.25))
                    assert selected(obj) == [True, True, False], (kind, selected(obj))
                    report[kind + "_then_rectangle_selects_two_grease_pencil_strokes"] = True
                    box((1.5, -1.25), (2.5, 1.25), extend=True)
                    assert selected(obj) == [True, True, True], (kind, selected(obj))
                    box((-2.5, -2.75), (0.5, 1.25))
                    assert selected(obj) == [True, True, False], (kind, selected(obj))
                    assert mesh_stroke.select_get()
                    report[kind + "_and_grease_pencil_share_rectangle_selection"] = True
                    click(-2.0, 0.0)
                    assert obj.mode == "EDIT" and selected(obj) == [True, False, False], (kind, selected(obj))
                    assert not mesh_stroke.select_get()
                    click(0.0, 0.0)
                    assert selected(obj) == [False, True, False]
                    report[kind + "_then_grease_pencil_selects_individual_strokes"] = True
            row: layers.LayerRow
            clayers: list[layers.LayerRow] = layers.layer_rows(bpy.context)
            gp_rows: list[layers.LayerRow] = []
            for row in clayers:
                assert row.key != "object:" + obj.name
                if row.key.startswith("stroke:" + obj.name + ":"):
                    gp_rows.append(row)
            assert len(gp_rows) == 3
            assert all(row.depth == 2 for row in gp_rows)
            report["clayers_has_strokes_without_grease_pencil_parent"] = True
            click(0.0, 0.0)
            assert selected(obj) == [False, True, False], selected(obj)
            click(2.0, 0.0, extend=True)
            report["shift_add"] = selected(obj)
            assert selected(obj) == [False, True, True], report
            click(0.0, 0.0, extend=True)
            report["shift_remove"] = selected(obj)
            assert selected(obj) == [False, False, True], report
            click(4.0, 2.5, extend=True)
            assert obj.mode == "EDIT" and selected(obj) == [False, False, True]
            report["shift_empty_keeps_stroke_selection"] = True
            bpy.ops.transform.translate(value=(0.25, 0.0, 0.0))
            assert abs(obj.data.layers[0].current_frame().drawing.strokes[0].points[0].position.x + 2.0) < 1e-6
            assert abs(obj.data.layers[0].current_frame().drawing.strokes[2].points[0].position.x - 2.25) < 1e-6
            report["only_selected_stroke_moved"] = True
            click(0.0, -2.5)
            assert bpy.context.view_layer.objects.active == cube and cube.select_get()
            report["mesh_selects_on_first_click"] = True
            click(3.5, 0.0)
            assert bpy.context.view_layer.objects.active == second and selected(second) == [True]
            click(-2.0, 0.0)
            assert bpy.context.view_layer.objects.active == obj and selected(obj) == [True, False, False]
            report["switch_between_drawings"] = True
            pencil.leave(bpy.context)
            get_state().tool = "move"
            click(0.0, 0.0)
            assert obj.mode == "EDIT" and selected(obj) == [False, True, False]
            assert bpy.context.workspace.tools.from_space_view3d_mode("EDIT_GREASE_PENCIL").idname == "builtin.move"
            report["move_tool_selects_one_stroke"] = True
            click(4.0, 2.5)
            assert not bpy.context.selected_objects
            report["empty_click_clears_selection"] = True
            view.view_perspective = "PERSP"
            view.update()
            get_state().tool = "select"
            click(-2.0, 0.0)
            assert obj.mode == "EDIT" and selected(obj) == [True, False, False]
            report["perspective_click_selects_one_stroke"] = True
            assert pencil.selection_command(bpy.context, "delete")
            assert len(obj.data.layers[0].current_frame().drawing.strokes) == 2
            report["delete_keeps_other_strokes_and_parent"] = True
            report["passed"] = True
    finally:
        with bpy.context.temp_override(window=window, area=area, region=region):
            current: Any = bpy.context.view_layer.objects.active
            if current is not None and current.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            window.scene = original_scene
            view.view_rotation, view.view_location, view.view_distance, view.view_perspective = original_view
            space.shading.type = original_shading
            if original_object is not None and original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=original_mode)
            try:
                if space.show_region_asset_shelf != original_shelf:
                    space.show_region_asset_shelf = original_shelf
            except (AttributeError, TypeError):
                pass
            get_state().tool = original_tool
            get_state().status = original_status
            get_state().active_group = original_group
            view.update()
            area.tag_redraw()
        obj: Any
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        data: Any
        for data in data_blocks:
            bpy.data.grease_pencils.remove(data)
        if cube_data is not None:
            bpy.data.meshes.remove(cube_data)
        for data in stroke_meshes:
            bpy.data.meshes.remove(data)
        bpy.data.materials.remove(material)
        bpy.data.scenes.remove(scene)
        collection: Any
        for collection in tuple(bpy.data.collections):
            if collection.name not in original_collections:
                bpy.data.collections.remove(collection)
        extra_material: Any
        for extra_material in tuple(bpy.data.materials):
            if extra_material.name not in original_materials and extra_material.users == 0:
                bpy.data.materials.remove(extra_material)
        output: Path = Path(__file__).resolve().parents[1] / ".checks/pencil-viewport-report.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


print(json.dumps(run(), indent=2))
