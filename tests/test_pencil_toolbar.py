"""Direct Grease Pencil tools and native shelf synchronization without Blender."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import unittest
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from drawing_app.core.controller import DRAWING_OT_select, DRAWING_OT_surface_capture
    from drawing_app.core.state import AppState, Tool
    from drawing_app.tools.layers import LayerRow
    from drawing_app.tools.pencil import PencilView
    from drawing_app.ui.overlay import Widget
    from drawing_app.viewport.keymap import Binding

ROOT: Path = Path(__file__).resolve().parents[1]


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


class PencilToolbarTests(unittest.TestCase):
    def setUp(self) -> None:
        state: ModuleType = module("_toolbar.core.state", "app/drawing_app/core/state.py")
        self.state_module: ModuleType = state
        self.state: AppState = cast("AppState", state.get_state())
        blender: ModuleType = ModuleType("_toolbar.core.blender")
        self.bpy: MagicMock = MagicMock()
        self.blender_module: ModuleType = blender
        setattr(blender, "bpy", self.bpy)
        setattr(blender, "view3d_utils", MagicMock())
        tools: ModuleType = ModuleType("_toolbar.tools")
        setattr(tools, "layers", MagicMock())
        # The 3D Printer's and 3D Extruder's own tests cover them; stopping a session only shuts them down.
        setattr(tools, "printer", MagicMock())
        setattr(tools, "extruder", MagicMock())
        # The sketch's lines live in the scene; here the count is set by each test.
        self.sketch_mesh: MagicMock = MagicMock()
        self.sketch_mesh.count.return_value = 0
        setattr(tools, "sketch_mesh", self.sketch_mesh)
        self.tools_module: ModuleType = tools
        viewport: ModuleType = ModuleType("_toolbar.viewport")
        self.viewport_module: ModuleType = viewport
        self.fills: MagicMock = MagicMock()
        self.fills.selection_surfaces.return_value.__enter__.return_value = False
        setattr(viewport, "fills", self.fills)
        with patch.dict(
            sys.modules,
            {state.__name__: state, blender.__name__: blender, tools.__name__: tools, viewport.__name__: viewport},
        ):
            self.pencil: ModuleType = module("_toolbar.tools.pencil", "app/drawing_app/tools/pencil.py")
        setattr(tools, "pencil", self.pencil)
        self.area: SimpleNamespace = SimpleNamespace(
            type="VIEW_3D",
            tag_redraw=MagicMock(),
            spaces=SimpleNamespace(active=SimpleNamespace(show_region_asset_shelf=False)),
        )
        self.bpy.context.view_layer.objects.active.type = "GREASEPENCIL"
        self.bpy.context.view_layer.objects.active.mode = self.pencil.MODE
        self.bpy.context.view_layer.objects.active.visible_get.return_value = True
        self.pencil.layers.object_locked.return_value = False
        self.bpy.context.area = self.area
        self.bpy.context.preferences.system.ui_scale = 1.0
        self.bpy.context.preferences.inputs.drag_threshold_mouse = 3
        self.bpy.context.preferences.inputs.drag_threshold_tablet = 3
        self.bpy.context.selected_objects = [self.bpy.context.view_layer.objects.active]
        self.bpy.context.scene.ray_cast.return_value = (False, None, None, -1, None, None)
        self.bpy.ops.object.mode_set.side_effect = self.set_mode

    def set_mode(self, *, mode: str) -> None:
        self.bpy.context.view_layer.objects.active.mode = mode

    def load_controller(self) -> ModuleType:
        drawing: ModuleType = ModuleType("_toolbar.tools.drawing")
        setattr(drawing, "DrawingController", MagicMock())
        viewport: ModuleType = self.viewport_module
        setattr(viewport, "NavigationController", MagicMock())
        setattr(viewport, "configure_viewport", MagicMock())
        project: ModuleType = ModuleType("_toolbar.core.project")
        setattr(project, "persist_scene", MagicMock())
        self.bpy.types.Operator = type("Operator", (), {})
        ui: ModuleType = ModuleType("_toolbar.ui")
        setattr(ui, "layout", MagicMock())
        widgets: ModuleType = module("_toolbar.ui.widgets", "app/drawing_app/ui/widgets.py")
        setattr(ui, "widgets", widgets)
        shortcuts: ModuleType = module("_toolbar.core.shortcuts", "app/drawing_app/core/shortcuts.py")
        # The chat panel draws nothing here; agent tests cover the chat itself.
        chat: MagicMock = MagicMock()
        chat.ChatPanel.return_value.draw.return_value = []
        setattr(ui, "chat", chat)
        modules: dict[str, ModuleType] = {}
        item: ModuleType
        for item in (
            self.state_module,
            self.blender_module,
            self.tools_module,
            drawing,
            viewport,
            project,
            shortcuts,
            ui,
            widgets,
        ):
            modules[item.__name__] = item
        with patch.dict(sys.modules, modules):
            controller: ModuleType = module("_toolbar.core.controller", "app/drawing_app/core/controller.py")
        setattr(controller, "viewport_context", MagicMock(return_value=(self.area, MagicMock())))
        # Keep dependencies available for the command boundary's lazy imports.
        self.imports: dict[str, ModuleType] = modules
        return controller

    def test_native_stroke_selection_keeps_object_and_existing_selection_on_reentry(self) -> None:
        self.state.tool = "select"
        original: MagicMock = self.bpy.context.view_layer.objects.active
        undo: MagicMock
        with patch.object(self.pencil, "_undo_push") as undo:
            self.assertTrue(self.pencil.enter_selection(self.bpy.context))
            self.assertEqual(original.mode, "EDIT")
            self.assertIs(self.bpy.context.view_layer.objects.active, original)
            self.bpy.ops.grease_pencil.set_selection_mode.assert_called_once_with(mode="STROKE")
            self.bpy.ops.grease_pencil.select_all.assert_called_once_with(action="DESELECT")
            self.assertFalse(self.bpy.context.scene.tool_settings.use_proportional_edit)
            self.assertFalse(self.bpy.context.scene.tool_settings.use_grease_pencil_multi_frame_editing)
            self.bpy.ops.grease_pencil.select_all.reset_mock()
            self.assertTrue(self.pencil.enter_selection(self.bpy.context))
            self.bpy.ops.grease_pencil.select_all.assert_not_called()
            undo.assert_called_once()
        self.bpy.data.objects.new.assert_not_called()

    def test_locked_or_missing_object_cannot_enter_stroke_selection(self) -> None:
        self.pencil.layers.object_locked.return_value = True
        self.assertFalse(self.pencil.enter_selection(self.bpy.context))
        self.bpy.ops.object.mode_set.assert_not_called()
        self.bpy.context.view_layer.objects.active = None
        with patch.object(self.pencil, "pencil_object", return_value=None):
            self.assertFalse(self.pencil.enter_selection(self.bpy.context))
        self.bpy.data.objects.new.assert_not_called()

    def test_leaving_draw_mode_does_not_select_the_grease_pencil_container(self) -> None:
        obj: MagicMock = self.bpy.context.view_layer.objects.active
        obj.mode = self.pencil.MODE
        with patch.object(self.pencil, "_undo_push"):
            self.pencil.leave(self.bpy.context)
        obj.select_set.assert_called_once_with(False)
        self.assertIsNone(self.bpy.context.view_layer.objects.active)

    def test_transform_and_edit_commands_stay_scoped_to_strokes(self) -> None:
        controller: ModuleType = self.load_controller()
        self.bpy.context.view_layer.objects.active.mode = "EDIT"
        self.state.tool = "select"
        with patch.dict(sys.modules, self.imports), patch.object(self.pencil, "_undo_push"):
            tool: str
            for tool in ("move", "rotate", "scale", "select"):
                controller.set_tool(tool)
                self.assertTrue(self.pencil.editing(self.bpy.context))
            self.bpy.ops.object.mode_set.assert_not_called()
            self.bpy.ops.grease_pencil.select_all.assert_not_called()
            controller._edit_command("select_all")
            controller._edit_command("duplicate")
            controller._edit_command("delete")
            self.bpy.ops.grease_pencil.select_all.assert_called_once_with(action="SELECT")
            self.bpy.ops.grease_pencil.duplicate.assert_called_once()
            self.bpy.ops.grease_pencil.delete.assert_called_once_with(mode="ALL")
            self.bpy.ops.object.delete.assert_not_called()
            self.bpy.ops.object.duplicate.assert_not_called()
            self.bpy.ops.object.select_all.assert_not_called()
            controller.set_tool("select")
            self.assertEqual(self.bpy.context.view_layer.objects.active.mode, "EDIT")

    def stroke_scene(self) -> ModuleType:
        setattr(self.blender_module, "Vector", MagicMock())
        with patch.dict(sys.modules, self.imports):
            layer_module: ModuleType = module("_toolbar.tools.layers", "app/drawing_app/tools/layers.py")
        self.imports[layer_module.__name__] = layer_module
        setattr(self.tools_module, "layers", layer_module)
        setattr(self.pencil, "layers", layer_module)
        setattr(layer_module, "ensure_scene", MagicMock())
        obj: MagicMock = self.bpy.context.view_layer.objects.active
        obj.name = "Ink:Drawing"
        obj.mode = "OBJECT"
        obj.hide_select = False
        obj.hide_get.return_value = False
        obj.select_get.return_value = True
        obj.users_collection = []
        obj.get.side_effect = {"drawing_stroke": True}.get
        self.bpy.data.objects.get.return_value = obj
        self.strokes: list[SimpleNamespace] = [
            SimpleNamespace(select=True),
            SimpleNamespace(select=True),
            SimpleNamespace(select=True),
        ]
        self.native_layer: MagicMock = MagicMock()
        self.native_layer.hide = self.native_layer.lock = False
        self.native_layer.current_frame.return_value = SimpleNamespace(
            frame_number=1, drawing=SimpleNamespace(strokes=self.strokes)
        )
        obj.data.layers.__iter__.return_value = [self.native_layer]
        obj.data.layers.__len__.return_value = 1
        obj.data.layers.__getitem__.return_value = self.native_layer
        self.bpy.ops.grease_pencil.select_all.side_effect = self.select_strokes
        collection: MagicMock = MagicMock()
        collection.name = "Default Drawing"
        collection.hide_select = collection.hide_viewport = False
        collection.children = []
        collection.objects = [obj]
        collection.get.side_effect = {"drawing_group": True}.get
        self.bpy.context.scene.collection.children = [collection]
        self.bpy.context.scene.objects = []
        return layer_module

    def select_strokes(self, *, action: str) -> None:
        stroke: SimpleNamespace
        for stroke in self.strokes:
            stroke.select = action == "SELECT"

    def selection(self) -> list[bool]:
        selected: list[bool] = []
        stroke: SimpleNamespace
        for stroke in self.strokes:
            selected.append(stroke.select)
        return selected

    def test_clayers_click_and_shift_click_select_only_child_strokes(self) -> None:
        controller: ModuleType = self.load_controller()
        layer_module: ModuleType = self.stroke_scene()
        ui: ModuleType = self.imports["_toolbar.ui"]
        setattr(ui, "paint", MagicMock())
        ui.paint.measure.return_value = 0.0
        setattr(ui, "menus", MagicMock())
        setattr(self.blender_module, "gpu", MagicMock())
        region: SimpleNamespace = SimpleNamespace(type="WINDOW", x=0, y=0, width=600, height=900)
        panel: MagicMock = MagicMock()
        panel.x = panel.y = 0
        panel.width, panel.height = 600, 900
        panel.regions = [region]
        self.bpy.context.window.screen.areas = [panel]
        self.bpy.context.preferences.system.ui_scale = 1.0
        ui.layout.role.return_value = "layers"
        with (
            patch.dict(sys.modules, self.imports),
            patch.object(controller, "_undo_push"),
            patch.object(self.pencil, "_undo_push"),
        ):
            overlay: ModuleType = module("_toolbar.ui.overlay", "app/drawing_app/ui/overlay.py")

            def dispatch_row(action: str, payload: object) -> None:
                self.assertEqual(action, "layer")
                controller._layer_command(cast(tuple[str, str], payload))

            setattr(overlay, "_DISPATCH", dispatch_row)
            overlay._layers(self.bpy.context, 600, 900)
            overlay._WIDGETS[int(panel.as_pointer())] = list(overlay._CURRENT)
            widgets: dict[str, Widget] = {}
            widget: Widget
            for widget in overlay._CURRENT:
                widgets[widget.key] = widget
            self.assertNotIn("expand_object:Ink:Drawing", widgets)
            self.assertNotIn("layer_select_object:Ink:Drawing", widgets)
            row: LayerRow
            children: list[LayerRow] = []
            for row in layer_module.layer_rows():
                if row.key.startswith("stroke:"):
                    children.append(row)
            self.assertEqual(len(children), 3)
            self.assertEqual({row.depth for row in children}, {1})

            def click(index: int, shift: bool = False) -> None:
                key: str = f"layer_select_stroke:Ink:Drawing:0:1:{index}"
                widget: Widget = widgets[key]
                event: SimpleNamespace = SimpleNamespace(
                    type="LEFTMOUSE",
                    value="PRESS",
                    mouse_x=widget.bounds[0] + 2,
                    mouse_y=widget.bounds[1] + 2,
                    shift=shift,
                    ctrl=False,
                )
                self.assertTrue(overlay.handle_event(self.bpy.context, event))

            click(0)
            self.assertEqual(self.selection(), [True, False, False])
            self.assertEqual(self.state.tool, "select")
            click(2, shift=True)
            self.assertEqual(self.selection(), [True, False, True])
            click(0, shift=True)
            self.assertEqual(self.selection(), [False, False, True])
            selected: list[str] = [
                row.key for row in layer_module.layer_rows() if row.selected and row.kind not in ("Group", "Plane")
            ]
            self.assertEqual(selected, ["stroke:Ink:Drawing:0:1:2"])
            click(1)
            self.assertEqual(self.selection(), [False, True, False])
            controller._layer_command(("select", "object:Ink:Drawing"))
            self.assertEqual(self.bpy.context.view_layer.objects.active.mode, "OBJECT")
            self.assertFalse(any(row.selected for row in layer_module.layer_rows() if row.key.startswith("stroke:")))
        self.assertEqual(len(self.strokes), 3)
        self.bpy.data.objects.new.assert_not_called()
        self.bpy.data.objects.remove.assert_not_called()

    def test_ctrl_g_command_makes_the_new_group_active_and_shows_it_in_clayers(self) -> None:
        controller: ModuleType = self.load_controller()
        overlay: MagicMock = MagicMock()
        setattr(self.imports["_toolbar.ui"], "overlay", overlay)
        layers: MagicMock = getattr(self.tools_module, "layers")
        layers.create_sibling_group.return_value = "collection:Group.001"
        layers.create_group.return_value = "collection:Group.002"
        undo: MagicMock
        with patch.dict(sys.modules, self.imports), patch.object(controller, "_undo_push") as undo:
            controller.dispatch("layer", ("sibling", ""))
            layers.create_sibling_group.assert_called_once_with(self.bpy.context)
            layers.create_group.assert_not_called()
            self.assertEqual(self.state.status, "New active group: Group.001")
            overlay.reveal_layer.assert_called_once_with("collection:Group.001")
            undo.assert_called_once_with("Change drawing layer")
            # The + button's group, at the top level, is shown the same way.
            controller.dispatch("layer", ("add", ""))
            layers.create_group.assert_called_once_with(self.bpy.context)
            overlay.reveal_layer.assert_called_with("collection:Group.002")
            self.assertEqual(undo.call_count, 2)

    def test_a_group_made_beside_the_active_one_shares_its_parent(self) -> None:
        class Children(list["Collection"]):
            def __contains__(self, name: object) -> bool:
                return any(item.name == name for item in self)

            def link(self, item: Collection) -> None:
                self.append(item)

        class Collection(dict[str, object]):
            def __init__(self, name: str) -> None:
                super().__init__()
                self.name: str = name
                self.children: Children = Children()
                self.objects: Children = Children()

        class Collections(list[Collection]):
            def get(self, name: str) -> Collection | None:
                return next((item for item in self if item.name == name), None)

            def new(self, name: str) -> Collection:
                # Blender numbers a repeated name: Group, Group.001, Group.002.
                taken: int = sum(1 for item in self if item.name == name or item.name[len(name) + 1 :].isdigit())
                created: Collection = Collection(name if not taken else f"{name}.{taken:03d}")
                self.append(created)
                return created

        self.load_controller()
        layer_module: ModuleType = self.stroke_scene()
        collections: Collections = Collections()
        self.bpy.data.collections = collections
        root: Collection = Collection("Scene Collection")
        context: SimpleNamespace = SimpleNamespace(scene=SimpleNamespace(collection=root), selected_objects=[])
        default: Collection = collections.new("Default Drawing")
        default[layer_module.GROUP] = True
        root.children.link(default)
        self.state.active_group = "Default Drawing"
        # Beside a top-level group: at the top level.
        first: str = layer_module.create_sibling_group(context)
        self.assertEqual((first, self.state.active_group), ("collection:Group", "Group"))
        group: Collection | None = collections.get("Group")
        assert group is not None
        self.assertIn("Group", root.children)
        self.assertTrue(group[layer_module.GROUP])
        self.assertTrue(group.children[0][layer_module.STROKES])
        # Beside a group inside another: inside that same group, not at the top level.
        layer_module.create_group(context, "Inner", "collection:Group")
        self.assertEqual(self.state.active_group, "Inner")
        second: str = layer_module.create_sibling_group(context)
        self.assertEqual((second, self.state.active_group), ("collection:Group.001", "Group.001"))
        self.assertIn("Group.001", group.children)
        self.assertIn("Inner", group.children)
        self.assertNotIn("Group.001", root.children)
        # Pressed again, the next one stays at this level too.
        third: str = layer_module.create_sibling_group(context)
        self.assertEqual(third, "collection:Group.002")
        self.assertIn("Group.002", group.children)
        # An active group that no longer exists falls back to the top level.
        self.state.active_group = "Gone"
        fourth: str = layer_module.create_sibling_group(context)
        self.assertIn(fourth.removeprefix("collection:"), root.children)

    def test_clayers_scrolls_to_a_new_group_and_opens_the_groups_that_hold_it(self) -> None:
        self.load_controller()
        layer_module: ModuleType = self.stroke_scene()
        ui: ModuleType = self.imports["_toolbar.ui"]
        setattr(ui, "paint", MagicMock())
        ui.paint.measure.return_value = 0.0
        setattr(ui, "menus", MagicMock())
        setattr(self.blender_module, "gpu", MagicMock())
        rows: list[LayerRow] = [layer_module.LayerRow("ground", "Ground Plane", "Plane", 0, True, False, False)]
        index: int
        for index in range(30):
            rows.append(layer_module.LayerRow(f"collection:G{index}", f"G{index}", "Group", 0, True, False, False))
        rows.append(layer_module.LayerRow("collection:Outer", "Outer", "Group", 0, True, False, False))
        rows.append(layer_module.LayerRow("collection:Middle", "Middle", "Group", 1, True, False, False))
        rows.append(layer_module.LayerRow("collection:New", "New", "Group", 2, True, False, True))
        with patch.dict(sys.modules, self.imports), patch.object(layer_module, "layer_rows", return_value=rows):
            overlay: ModuleType = module("_toolbar.ui.overlay", "app/drawing_app/ui/overlay.py")

            def shown() -> dict[str, float]:
                """Each drawn row's key and height in a 300 x 900 panel."""
                overlay._CURRENT.clear()
                overlay._layers(self.bpy.context, 300, 900)
                found: dict[str, float] = {}
                widget: Widget
                for widget in overlay._CURRENT:
                    if widget.key.startswith("layer_select_"):
                        found[widget.key.removeprefix("layer_select_")] = widget.bounds[1]
                return found

            overlay._COLLAPSED.update(("collection:Outer", "collection:Middle", "collection:G3"))
            before: dict[str, float] = shown()
            self.assertIn("ground", before)
            self.assertNotIn("collection:New", before)
            overlay.reveal_layer("collection:New")
            after: dict[str, float] = shown()
            # The groups above it opened, others stayed closed, and it is the lowest row in view.
            self.assertEqual(overlay._COLLAPSED, {"collection:G3"})
            self.assertIn("collection:New", after)
            self.assertIn("collection:Middle", after)
            self.assertNotIn("ground", after)
            self.assertEqual(after["collection:New"], min(after.values()))
            # Asked once: scrolling away afterwards is not undone by the next redraw.
            overlay._SCROLL["layers"] = 0.0
            self.assertNotIn("collection:New", shown())
            # A row above the view is brought down to its top; one already in view does not move the list.
            overlay._SCROLL["layers"] = 600.0
            overlay.reveal_layer("collection:G2")
            again: dict[str, float] = shown()
            self.assertEqual(again["collection:G2"], max(again.values()))
            scroll: float = overlay._SCROLL["layers"]
            overlay.reveal_layer("collection:G4")
            shown()
            self.assertEqual(overlay._SCROLL["layers"], scroll)

    def test_clayers_take_a_third_and_the_chat_divider_resizes_and_saves(self) -> None:
        self.load_controller()
        self.stroke_scene()
        ui: ModuleType = self.imports["_toolbar.ui"]
        setattr(ui, "paint", MagicMock())
        ui.paint.measure.return_value = 0.0
        setattr(ui, "menus", MagicMock())
        setattr(self.blender_module, "gpu", MagicMock())
        region: SimpleNamespace = SimpleNamespace(type="WINDOW", x=0, y=0, width=600, height=900)
        panel: MagicMock = MagicMock()
        panel.x = panel.y = 0
        panel.width, panel.height = 600, 900
        panel.regions = [region]
        self.bpy.context.window.screen.areas = [panel]
        ui.layout.role.return_value = "layers"
        with patch.dict(sys.modules, self.imports):
            overlay: ModuleType = module("_toolbar.ui.overlay", "app/drawing_app/ui/overlay.py")
            save: MagicMock = MagicMock()
            setattr(overlay, "save_preferences", save)
            overlay._layers(self.bpy.context, 600, 900)
            widgets: dict[str, Widget] = {}
            widget: Widget
            for widget in overlay._CURRENT:
                widgets[widget.key] = widget
            # Clayers keeps the top third; the divider sits two thirds of the way up.
            divider: Widget = widgets["panel_divider"]
            self.assertAlmostEqual(divider.bounds[1] + 5, 600.0)
            overlay._WIDGETS[int(panel.as_pointer())] = list(overlay._CURRENT)

            def event(kind: str, value: str, y: float) -> SimpleNamespace:
                return SimpleNamespace(type=kind, value=value, mouse_x=300, mouse_y=y, shift=False, ctrl=False)

            self.assertTrue(overlay.handle_event(self.bpy.context, event("LEFTMOUSE", "PRESS", 600)))
            self.assertEqual(overlay._DRAG.kind, "divider")
            overlay.handle_event(self.bpy.context, event("MOUSEMOVE", "NOTHING", 450))
            self.assertAlmostEqual(self.state.layers_share, 0.5)
            # Clayers keeps at least 96 units and the chat at least 180.
            overlay.handle_event(self.bpy.context, event("MOUSEMOVE", "NOTHING", 880))
            self.assertAlmostEqual(self.state.layers_share, 96 / 900)
            overlay.handle_event(self.bpy.context, event("MOUSEMOVE", "NOTHING", 5))
            self.assertAlmostEqual(self.state.layers_share, 0.8)
            save.assert_not_called()
            overlay.handle_event(self.bpy.context, event("LEFTMOUSE", "RELEASE", 5))
            save.assert_called_once()
            self.assertIsNone(overlay._DRAG)
            self.bpy.context.window.cursor_set.assert_any_call("MOVE_Y")
            overlay._CURRENT.clear()
            overlay._layers(self.bpy.context, 600, 900)
            for widget in overlay._CURRENT:
                if widget.key == "panel_divider":
                    self.assertAlmostEqual(widget.bounds[1] + 5, 900 - 720)

    def test_chat_and_panel_preferences_are_restored_and_checked(self) -> None:
        state: ModuleType = self.state_module
        saved: dict[str, object] = {
            "agent_model": "sonnet",
            "agent_mode": "acceptEdits",
            "agent_effort": "xhigh",
            "layers_share": 0.4,
        }
        rejected: dict[str, object] = {
            "agent_model": "opus; rm -rf",
            "agent_mode": 3,
            "agent_effort": "x" * 80,
            "layers_share": 2,
        }
        with (
            patch.object(state.Path, "is_file", return_value=True),
            patch.object(state.Path, "read_text", side_effect=[json.dumps(saved), json.dumps(rejected)]),
        ):
            state.load_preferences()
            values: tuple[object, ...] = (
                self.state.agent_model,
                self.state.agent_mode,
                self.state.agent_effort,
                self.state.layers_share,
            )
            self.assertEqual(values, ("sonnet", "acceptEdits", "xhigh", 0.4))
            state.load_preferences()
            values = (self.state.agent_model, self.state.agent_mode, self.state.agent_effort, self.state.layers_share)
            self.assertEqual(values, ("sonnet", "acceptEdits", "xhigh", 0.4))

    def test_clayers_rejects_stale_or_locked_stroke_rows(self) -> None:
        self.load_controller()
        self.stroke_scene()
        self.native_layer.lock = True
        self.assertFalse(self.pencil.select_row(self.bpy.context, "stroke:Ink:Drawing:0:1:1"))
        self.native_layer.lock = False
        self.assertFalse(self.pencil.select_row(self.bpy.context, "stroke:Ink:Drawing:0:2:1"))
        self.assertFalse(self.pencil.select_row(self.bpy.context, "stroke:Ink:Drawing:0:1:9"))
        self.bpy.ops.object.mode_set.assert_not_called()

    def test_active_plane_and_viewport_selection_are_independent(self) -> None:
        self.load_controller()
        layer_module: ModuleType = self.stroke_scene()
        obj: MagicMock = self.bpy.context.view_layer.objects.active
        obj.type = "MESH"
        obj.get.side_effect = {"drawing_plane": True}.get
        self.state.active_plane = obj.name
        obj.select_get.return_value = False
        with patch.dict(sys.modules, self.imports):
            rows: list[LayerRow] = layer_module.layer_rows()
            self.assertTrue(rows[-1].active)
            self.assertFalse(rows[-1].selected)
            obj.select_get.return_value = True
            rows = layer_module.layer_rows()
            self.assertTrue(rows[-1].active)
            self.assertTrue(rows[-1].selected)
            self.state.active_plane = "Ground Plane"
            rows = layer_module.layer_rows()
            self.assertFalse(rows[-1].active)
            self.assertTrue(rows[-1].selected)
            self.assertTrue(rows[0].active)
            self.assertFalse(rows[0].selected)

    def test_normal_viewport_click_selects_one_stroke_and_shift_toggles_once(self) -> None:
        controller: ModuleType = self.load_controller()
        self.stroke_scene()
        obj: MagicMock = self.bpy.context.view_layer.objects.active

        def native_pick(*, location: tuple[int, int], toggle: bool, deselect_all: bool) -> set[str]:
            if obj.mode == "EDIT":
                if deselect_all:
                    self.select_strokes(action="DESELECT")
                stroke: SimpleNamespace = self.strokes[location[0]]
                stroke.select = not stroke.select if toggle else True
            return {"FINISHED"}

        self.bpy.ops.view3d.select.side_effect = native_pick
        event: SimpleNamespace = SimpleNamespace(
            type="LEFTMOUSE", value="PRESS", mouse_region_x=0, mouse_region_y=100, shift=False, is_tablet=False
        )
        with patch.dict(sys.modules, self.imports), patch.object(self.pencil, "_undo_push"):
            operator: DRAWING_OT_select = controller.DRAWING_OT_select()

            def click(index: int, shift: bool = False) -> None:
                event.value, event.mouse_region_x, event.shift = "PRESS", index, shift
                self.assertTrue(operator.poll(self.bpy.context))
                self.assertEqual(operator.invoke(self.bpy.context, event), {"RUNNING_MODAL"})
                event.value = "RELEASE"
                self.assertEqual(operator.modal(self.bpy.context, event), {"FINISHED"})
                event.value = "CLICK"
                self.assertEqual(operator.invoke(self.bpy.context, event), {"FINISHED"})

            tool: str
            for tool in ("select", "move", "rotate", "scale"):
                with self.subTest(tool=tool):
                    self.state.tool = cast("Tool", tool)
                    obj.mode = "OBJECT"
                    self.select_strokes(action="SELECT")
                    click(0)
                    self.assertEqual(self.selection(), [True, False, False])
                    click(1, shift=True)
                    self.assertEqual(self.selection(), [True, True, False])
                    click(1, shift=True)
                    self.assertEqual(self.selection(), [True, False, False])
        self.bpy.context.scene.ray_cast.assert_not_called()

    def fill_object(self) -> MagicMock:
        obj: MagicMock = MagicMock()
        obj.type, obj.mode, obj.display_type = "MESH", "OBJECT", "TEXTURED"
        obj.color = (0.2, 0.3, 0.4, 0.5)
        obj.active_material = None
        obj.get.side_effect = {"drawing_kind": "fill"}.get
        obj.visible_get.return_value = True
        obj.select_get.return_value = False

        def select(value: bool) -> None:
            obj.select_get.return_value = value

        obj.select_set.side_effect = select
        self.bpy.context.scene.ray_cast.return_value = (True, None, None, 0, obj, None)
        return obj

    def test_fill_interior_click_leaves_stroke_edit_and_selects_only_fill(self) -> None:
        pencil: MagicMock = self.bpy.context.view_layer.objects.active
        pencil.mode = "EDIT"
        fill: MagicMock = self.fill_object()
        self.bpy.ops.view3d.select.return_value = {"CANCELLED"}
        with patch.object(self.pencil, "_undo_push"):
            self.pencil.select_at(self.bpy.context, (80, 120))
        self.assertEqual(pencil.mode, "OBJECT")
        pencil.select_set.assert_called_once_with(False)
        fill.select_set.assert_called_once_with(True)
        self.assertIs(self.bpy.context.view_layer.objects.active, fill)

    def test_shift_fill_interior_toggles_once_and_preserves_existing_strokes(self) -> None:
        pencil: MagicMock = self.bpy.context.view_layer.objects.active
        pencil.mode = "EDIT"
        fill: MagicMock = self.fill_object()
        self.bpy.ops.view3d.select.return_value = {"CANCELLED"}
        with patch.object(self.pencil, "_undo_push"):
            self.pencil.select_at(self.bpy.context, (80, 120), extend=True)
            self.assertTrue(fill.select_get())
            self.pencil.select_at(self.bpy.context, (80, 120), extend=True)
            self.assertFalse(fill.select_get())
        self.assertEqual(fill.select_set.call_count, 2)
        pencil.select_set.assert_not_called()
        self.bpy.ops.grease_pencil.select_all.assert_not_called()

    def test_native_fill_pick_is_not_toggled_twice(self) -> None:
        fill: MagicMock = self.fill_object()
        self.bpy.context.view_layer.objects.active = fill

        def native_pick(*, location: tuple[int, int], toggle: bool, deselect_all: bool) -> set[str]:
            fill.select_set(not fill.select_get())
            return {"FINISHED"}

        self.bpy.ops.view3d.select.side_effect = native_pick
        self.pencil.select_at(self.bpy.context, (80, 120), extend=True)
        fill.select_set.assert_called_once_with(True)
        self.bpy.context.scene.ray_cast.assert_not_called()

    def test_fill_interior_picking_uses_material_opacity(self) -> None:
        self.bpy.context.view_layer.objects.active.mode = "OBJECT"
        fill: MagicMock = self.fill_object()
        fill.color = (0.2, 0.3, 0.4, 1.0)
        fill.active_material = SimpleNamespace(diffuse_color=(0.2, 0.3, 0.4, 0.5))
        self.assertTrue(self.pencil._select_fill_at(self.bpy.context, (80, 120), False))
        fill.select_set.reset_mock()
        fill.active_material.diffuse_color = (0.2, 0.3, 0.4, 0.0)
        self.assertFalse(self.pencil._select_fill_at(self.bpy.context, (80, 120), False))
        fill.select_set.assert_not_called()

    def test_fill_pick_does_not_override_nearer_opaque_geometry(self) -> None:
        opaque: MagicMock = self.bpy.context.view_layer.objects.active
        opaque.type, opaque.mode = "MESH", "OBJECT"
        opaque.get.side_effect = {"drawing_kind": "pipe"}.get
        self.bpy.ops.view3d.select.return_value = {"FINISHED"}
        self.bpy.context.scene.ray_cast.return_value = (True, None, None, 0, opaque, None)
        self.pencil.select_at(self.bpy.context, (80, 120))
        self.assertIs(self.bpy.context.view_layer.objects.active, opaque)
        opaque.select_set.assert_not_called()
        self.bpy.ops.object.mode_set.assert_not_called()

    def test_rectangle_adds_fill_interior_without_losing_individual_strokes(self) -> None:
        self.load_controller()
        self.stroke_scene()
        pencil: MagicMock = self.bpy.context.view_layer.objects.active
        fill: MagicMock = self.fill_object()
        surfaces: SimpleNamespace = SimpleNamespace(visible=False)
        calls: list[tuple[str, str, bool]] = []

        def show_surfaces() -> bool:
            surfaces.visible = True
            return True

        def hide_surfaces(_type: object, _error: object, _traceback: object) -> None:
            surfaces.visible = False

        def native_box(*, xmin: int, xmax: int, ymin: int, ymax: int, mode: str, wait_for_input: bool) -> set[str]:
            calls.append((pencil.mode, mode, surfaces.visible))
            if pencil.mode == "EDIT":
                self.assertFalse(surfaces.visible)
                self.select_strokes(action="DESELECT")
                self.strokes[1].select = True
            elif surfaces.visible:
                self.assertEqual(mode, "ADD")
                self.assertIn(pencil, self.bpy.context.selected_objects)
                self.bpy.context.selected_objects = [pencil, fill]
            else:
                self.bpy.context.selected_objects = [pencil]
            return {"FINISHED"}

        self.fills.selection_surfaces.return_value.__enter__.side_effect = show_surfaces
        self.fills.selection_surfaces.return_value.__exit__.side_effect = hide_surfaces
        self.bpy.ops.view3d.select_box.side_effect = native_box
        with patch.object(self.pencil, "_undo_push"):
            self.pencil.select_box(self.bpy.context, (80, 100), (120, 140))
        self.assertEqual(calls, [("OBJECT", "SET", False), ("OBJECT", "ADD", True), ("EDIT", "SET", False)])
        self.assertEqual(self.selection(), [False, True, False])
        fill.select_set.assert_called_with(True)
        self.assertFalse(surfaces.visible)

    def test_rectangle_without_translucent_fills_uses_one_object_query(self) -> None:
        self.bpy.context.view_layer.objects.active = None
        self.bpy.context.selected_objects = []
        self.pencil.select_box(self.bpy.context, (80, 100), (120, 140), extend=True)
        self.bpy.ops.view3d.select_box.assert_called_once_with(
            xmin=80, xmax=120, ymin=100, ymax=140, mode="ADD", wait_for_input=False
        )

    def test_drag_reaches_native_rectangle_without_becoming_a_click(self) -> None:
        controller: ModuleType = self.load_controller()
        self.state.tool = "select"
        self.bpy.context.view_layer.objects.active.mode = "OBJECT"
        event: SimpleNamespace = SimpleNamespace(
            type="LEFTMOUSE", value="PRESS", mouse_region_x=20, mouse_region_y=40, shift=False, is_tablet=False
        )
        with patch.dict(sys.modules, self.imports), patch.object(self.pencil, "_undo_push"):
            operator: DRAWING_OT_select = controller.DRAWING_OT_select()
            self.assertEqual(operator.invoke(self.bpy.context, event), {"RUNNING_MODAL"})
            event.type, event.mouse_region_x = "MOUSEMOVE", 21
            self.assertEqual(operator.modal(self.bpy.context, event), {"RUNNING_MODAL"})
            event.mouse_region_x = 50
            self.assertEqual(operator.modal(self.bpy.context, event), {"RUNNING_MODAL"})
            self.bpy.types.SpaceView3D.draw_handler_add.assert_called_once()
            self.bpy.ops.view3d.select_box.assert_not_called()
            self.bpy.ops.view3d.select.assert_not_called()
            with patch.object(self.pencil, "select_box") as select_box:
                event.type, event.value = "LEFTMOUSE", "RELEASE"
                self.assertEqual(operator.modal(self.bpy.context, event), {"FINISHED"})
                select_box.assert_called_once_with(self.bpy.context, (20, 40), (50, 40), False)
            self.bpy.types.SpaceView3D.draw_handler_remove.assert_called_once()
            event.type, event.value = "LEFTMOUSE", "PRESS"
            operator.invoke(self.bpy.context, event)
            event.type = "ESC"
            self.assertEqual(operator.modal(self.bpy.context, event), {"CANCELLED"})
            self.bpy.ops.view3d.select.assert_not_called()

    def test_toolbox_has_no_select_tool_in_either_selection_mode(self) -> None:
        self.load_controller()
        ui: ModuleType = self.imports["_toolbar.ui"]
        setattr(ui, "paint", MagicMock())
        ui.paint.measure.return_value = 0.0
        setattr(ui, "menus", MagicMock())
        setattr(self.blender_module, "gpu", MagicMock())
        self.bpy.context.scene.unit_settings.scale_length = 1.0
        with patch.dict(sys.modules, self.imports):
            overlay: ModuleType = module("_toolbar.ui.overlay", "app/drawing_app/ui/overlay.py")
            tool: Tool
            for tool in ("select",):
                self.state.tool = tool
                overlay._CURRENT.clear()
                overlay._toolbox(self.bpy.context, 190, 1000)
                widget: Widget
                keys: set[str] = set()
                for widget in overlay._CURRENT:
                    keys.add(widget.key)
                self.assertIn("tool_grease", keys)
                self.assertIn("tool_move", keys)
                self.assertNotIn("pencil_tool_stroke_select", keys)
                self.assertNotIn("select", keys)

    def test_viewport_selection_bindings_cover_native_tool_fallbacks(self) -> None:
        self.load_controller()
        with patch.dict(sys.modules, self.imports):
            keymap: ModuleType = module("_toolbar.viewport.keymap", "app/drawing_app/viewport/keymap.py")
        expected: set[tuple[str, str, bool]] = set()
        name: str
        value: str
        shift: bool
        for name in ("3D View", "3D View Tool: Tweak", "3D View Tool: Tweak (fallback)"):
            for value in ("PRESS", "CLICK"):
                for shift in (False, True):
                    expected.add((name, value, shift))
        actual: set[tuple[str, str, bool]] = set()
        binding: Binding
        for binding in keymap.BINDINGS:
            if binding.idname == "drawing.select":
                actual.add((binding.keymap, binding.value, binding.shift))
        self.assertEqual(actual, expected)

    def test_each_tool_is_directly_activated_from_another_application_tool(self) -> None:
        tool: str
        label: str
        for tool, label in self.pencil.TOOLS:
            self.state.tool = "paint"
            enter: MagicMock
            with self.subTest(tool=label), patch.object(self.pencil, "enter") as enter:
                self.pencil.command(self.bpy.context, "tool", tool)
                self.assertEqual(self.state.tool, "grease")
                self.assertEqual(self.state.pencil.tool, tool)
                enter.assert_called_once()

    def test_rectangle_and_ellipse_share_one_shape_button(self) -> None:
        def buttons() -> list[tuple[str, str]]:
            return list(self.pencil.toolbox_tools())

        self.assertIn(("builtin.box", "Shape"), buttons())
        self.assertNotIn("builtin.circle", dict(buttons()))
        with patch.object(self.pencil, "enter"):
            self.pencil.command(self.bpy.context, "tool", "builtin.circle")
            self.assertEqual(self.state.pencil.shape, "builtin.circle")
            self.assertIn(("builtin.circle", "Shape"), buttons())
            self.assertNotIn("builtin.box", dict(buttons()))
            # Another tool keeps the last shape for the Shape button.
            self.pencil.command(self.bpy.context, "tool", "builtin.brush")
            self.assertIn(("builtin.circle", "Shape"), buttons())
        self.assertEqual(len(buttons()), len(self.pencil.DRAWING_TOOLS) - 1)

    def test_shape_settings_choose_rectangle_or_ellipse(self) -> None:
        self.load_controller()
        ui: ModuleType = self.imports["_toolbar.ui"]
        setattr(ui, "paint", MagicMock())
        ui.paint.measure.return_value = 0.0
        setattr(ui, "menus", MagicMock())
        setattr(self.blender_module, "gpu", MagicMock())
        view: MagicMock = MagicMock()
        view.tool = "builtin.circle"
        view.brushes = ()
        view.brush = ""
        view.material = ""
        view.canvas = ""
        with patch.dict(sys.modules, self.imports):
            overlay: ModuleType = module("_toolbar.ui.overlay", "app/drawing_app/ui/overlay.py")
            overlay._CURRENT.clear()
            with patch.object(self.pencil, "view", return_value=view):
                overlay._pencil(self.bpy.context, 10.0, 800.0, 180.0)
        widget: Widget = next(item for item in overlay._CURRENT if item.key == "pencil_shape")
        self.assertEqual(widget.action, "popup")
        self.assertEqual(
            widget.payload,
            [("Rectangle", "pencil", ("tool", "builtin.box")), ("Ellipse", "pencil", ("tool", "builtin.circle"))],
        )
        labels: list[str] = [str(args[0]) for args, _kwargs in ui.paint.text.call_args_list]
        self.assertIn("Shape", labels)

    def test_shelf_only_shows_for_draw_and_remembers_native_brush_selection(self) -> None:
        self.state.tool = "grease"
        native: MagicMock = self.bpy.context.workspace.tools.from_space_view3d_mode.return_value
        native.idname = "builtin.brush"
        self.bpy.context.scene.tool_settings.gpencil_paint.brush.name = "Ink Pen"
        self.pencil.sync_shelf(self.area)
        self.assertTrue(self.area.spaces.active.show_region_asset_shelf)
        self.assertEqual(self.state.pencil.brush, "Ink Pen")
        native.idname = "builtin_brush.Erase"
        self.bpy.context.scene.tool_settings.gpencil_paint.brush.name = "Eraser Soft"
        self.pencil.sync_shelf(self.area)
        self.assertFalse(self.area.spaces.active.show_region_asset_shelf)
        self.assertEqual(self.state.pencil.eraser, "Eraser Soft")
        self.assertEqual(self.state.pencil.brush, "Ink Pen")
        self.state.tool = "select"
        self.pencil.sync_shelf(self.area)
        self.assertFalse(self.area.spaces.active.show_region_asset_shelf)

    def surface_draw_setup(self) -> tuple[MagicMock, SimpleNamespace]:
        self.state.tool = "grease"
        self.state.on_object = True
        self.state.cursor_active = False
        self.state.pencil.placement = "ORIGIN"
        self.bpy.context.view_layer.objects.active.mode = self.pencil.MODE
        self.bpy.context.workspace.tools.from_space_view3d_mode.return_value.idname = "builtin.brush"
        brush: MagicMock = self.bpy.context.scene.tool_settings.gpencil_paint.brush
        brush.gpencil_brush_type = "DRAW"
        brush.use_smooth_stroke = True
        self.surface_strokes: list[SimpleNamespace] = []
        self.bpy.context.view_layer.objects.active.data.layers.active.current_frame.return_value.drawing.strokes = (
            self.surface_strokes
        )
        self.fills.selection_surfaces.reset_mock()
        self.fills.selection_surfaces.return_value.__enter__.return_value = True
        self.bpy.ops.grease_pencil.brush_stroke.reset_mock()
        self.bpy.ops.grease_pencil.brush_stroke.return_value = {"RUNNING_MODAL"}
        self.bpy.ops.drawing.surface_capture.reset_mock()
        event: SimpleNamespace = SimpleNamespace(type="LEFTMOUSE", value="PRESS", ctrl=False, shift=False, alt=False)
        return brush, event

    def test_surface_draw_immediate_dab_captures_depth_without_changing_stabilization(self) -> None:
        brush: MagicMock
        event: SimpleNamespace
        brush, event = self.surface_draw_setup()
        surfaces: SimpleNamespace = SimpleNamespace(visible=False)

        def show_surfaces() -> bool:
            surfaces.visible = True
            return True

        def hide_surfaces(_type: object, _error: object, _traceback: object) -> None:
            surfaces.visible = False

        def native_draw(execution: str, *, mode: str, brush_toggle: str) -> set[str]:
            self.assertTrue(surfaces.visible)
            self.assertTrue(brush.use_smooth_stroke)
            self.assertEqual((execution, mode, brush_toggle), ("INVOKE_DEFAULT", "NORMAL", "None"))
            self.surface_strokes.append(SimpleNamespace())
            return {"RUNNING_MODAL"}

        self.fills.selection_surfaces.return_value.__enter__.side_effect = show_surfaces
        self.fills.selection_surfaces.return_value.__exit__.side_effect = hide_surfaces
        self.bpy.ops.grease_pencil.brush_stroke.side_effect = native_draw
        self.assertTrue(self.pencil.invoke_surface_draw(self.bpy.context, event))
        self.assertTrue(brush.use_smooth_stroke)
        self.assertFalse(surfaces.visible)
        self.fills.selection_surfaces.assert_called_once_with(include_zero_opacity=True)
        self.bpy.ops.grease_pencil.brush_stroke.assert_called_once()
        self.bpy.ops.drawing.surface_capture.assert_not_called()

    def test_surface_draw_delayed_dab_adds_capture_guard_after_restoring_fill_visibility(self) -> None:
        brush: MagicMock
        event: SimpleNamespace
        brush, event = self.surface_draw_setup()
        surfaces: SimpleNamespace = SimpleNamespace(visible=False)

        def show_surfaces() -> bool:
            surfaces.visible = True
            return True

        def hide_surfaces(_type: object, _error: object, _traceback: object) -> None:
            surfaces.visible = False

        def capture_guard(execution: str, *, stroke_count: int) -> set[str]:
            self.assertFalse(surfaces.visible)
            self.assertTrue(brush.use_smooth_stroke)
            self.assertEqual((execution, stroke_count), ("INVOKE_DEFAULT", 0))
            return {"RUNNING_MODAL"}

        self.fills.selection_surfaces.return_value.__enter__.side_effect = show_surfaces
        self.fills.selection_surfaces.return_value.__exit__.side_effect = hide_surfaces
        self.bpy.ops.drawing.surface_capture.side_effect = capture_guard
        self.assertTrue(self.pencil.invoke_surface_draw(self.bpy.context, event))
        self.bpy.ops.drawing.surface_capture.assert_called_once_with("INVOKE_DEFAULT", stroke_count=0)
        self.assertTrue(brush.use_smooth_stroke)

    def test_surface_draw_completed_or_cancelled_native_operation_needs_no_capture_guard(self) -> None:
        brush: MagicMock
        event: SimpleNamespace
        result: str
        for result in ("FINISHED", "CANCELLED"):
            with self.subTest(result=result):
                brush, event = self.surface_draw_setup()
                self.bpy.ops.grease_pencil.brush_stroke.return_value = {result}
                self.assertEqual(self.pencil.invoke_surface_draw(self.bpy.context, event), result == "FINISHED")
                self.bpy.ops.drawing.surface_capture.assert_not_called()
                self.assertTrue(brush.use_smooth_stroke)

    def test_surface_draw_keeps_native_modifiers_and_unstabilized_brush_settings(self) -> None:
        brush: MagicMock
        event: SimpleNamespace
        brush, event = self.surface_draw_setup()
        brush.use_smooth_stroke = False
        ctrl: bool
        shift: bool
        mode: str
        toggle: str
        for ctrl, shift, mode, toggle in (
            (False, False, "NORMAL", "None"),
            (True, False, "INVERT", "None"),
            (False, True, "NORMAL", "SMOOTH"),
            (True, True, "INVERT", "SMOOTH"),
        ):
            with self.subTest(ctrl=ctrl, shift=shift):
                event.ctrl, event.shift = ctrl, shift
                self.bpy.ops.grease_pencil.brush_stroke.reset_mock()
                self.assertTrue(self.pencil.invoke_surface_draw(self.bpy.context, event))
                self.bpy.ops.grease_pencil.brush_stroke.assert_called_once_with(
                    "INVOKE_DEFAULT", mode=mode, brush_toggle=toggle
                )
                self.assertFalse(brush.use_smooth_stroke)

    def test_surface_draw_leaves_unrelated_events_and_tools_to_blender(self) -> None:
        case: str
        for case in ("placement", "inactive", "other_tool", "eraser", "alt", "motion", "release"):
            with self.subTest(case=case):
                brush: MagicMock
                event: SimpleNamespace
                brush, event = self.surface_draw_setup()
                if case == "placement":
                    self.state.on_object = False
                    self.state.drawing_plane = False
                elif case == "inactive":
                    self.bpy.context.view_layer.objects.active.mode = "EDIT"
                elif case == "other_tool":
                    self.bpy.context.workspace.tools.from_space_view3d_mode.return_value.idname = "builtin.polyline"
                elif case == "eraser":
                    brush.gpencil_brush_type = "ERASE"
                elif case == "alt":
                    event.alt = True
                elif case == "motion":
                    event.type = "MOUSEMOVE"
                elif case == "release":
                    event.value = "RELEASE"
                self.assertFalse(self.pencil.invoke_surface_draw(self.bpy.context, event))
                self.fills.selection_surfaces.assert_not_called()
                self.bpy.ops.grease_pencil.brush_stroke.assert_not_called()
                self.assertTrue(brush.use_smooth_stroke)

    def test_surface_draw_without_custom_fills_preserves_normal_native_input(self) -> None:
        brush: MagicMock
        event: SimpleNamespace
        brush, event = self.surface_draw_setup()
        self.fills.selection_surfaces.return_value.__enter__.return_value = False
        self.assertFalse(self.pencil.invoke_surface_draw(self.bpy.context, event))
        self.bpy.ops.grease_pencil.brush_stroke.assert_not_called()
        self.assertTrue(brush.use_smooth_stroke)

    def test_surface_draw_failure_restores_brush_and_exits_surface_capture(self) -> None:
        brush: MagicMock
        event: SimpleNamespace
        brush, event = self.surface_draw_setup()
        self.bpy.ops.grease_pencil.brush_stroke.side_effect = RuntimeError("Native stroke unavailable")
        with self.assertRaisesRegex(RuntimeError, "Native stroke unavailable"):
            self.pencil.invoke_surface_draw(self.bpy.context, event)
        self.assertTrue(brush.use_smooth_stroke)
        self.fills.selection_surfaces.return_value.__exit__.assert_called_once()
        self.bpy.ops.drawing.surface_capture.assert_not_called()

    def surface_guard_setup(self) -> tuple[ModuleType, DRAWING_OT_surface_capture]:
        controller: ModuleType = self.load_controller()
        self.surface_draw_setup()
        self.guard_event: SimpleNamespace = SimpleNamespace(type="MOUSEMOVE", value="NOTHING")
        self.guard_surfaces: SimpleNamespace = SimpleNamespace(visible=False)
        self.guard_timers: list[Callable[[], None]] = []
        self.bpy.context.window.modal_operators = [
            SimpleNamespace(bl_idname="GREASE_PENCIL_OT_brush_stroke", as_pointer=MagicMock(return_value=77))
        ]

        def show_surfaces() -> bool:
            self.guard_surfaces.visible = True
            return True

        def hide_surfaces(_type: object, _error: object, _traceback: object) -> None:
            self.guard_surfaces.visible = False

        def register(callback: Callable[[], None], *, first_interval: float) -> None:
            self.assertEqual(first_interval, 0.0)
            self.guard_timers.append(callback)

        self.fills.selection_surfaces.return_value.__enter__.side_effect = show_surfaces
        self.fills.selection_surfaces.return_value.__exit__.side_effect = hide_surfaces
        self.bpy.app.timers.register.side_effect = register
        self.bpy.app.timers.is_registered.side_effect = self.guard_timers.__contains__
        self.bpy.app.timers.unregister.side_effect = self.guard_timers.remove
        operator: DRAWING_OT_surface_capture = controller.DRAWING_OT_surface_capture()
        operator.stroke_count = 0
        return controller, operator

    def test_surface_guard_exposes_before_native_motion_then_restores_before_redraw(self) -> None:
        controller: ModuleType
        operator: DRAWING_OT_surface_capture
        controller, operator = self.surface_guard_setup()
        with patch.dict(sys.modules, self.imports):
            self.assertEqual(operator.invoke(self.bpy.context, self.guard_event), {"RUNNING_MODAL"})
            self.assertEqual(operator.modal(self.bpy.context, self.guard_event), {"PASS_THROUGH"})
            self.assertTrue(self.guard_surfaces.visible)
            self.assertEqual(len(self.guard_timers), 1)
            self.assertEqual(len(controller._SURFACE_CLEANUPS), 1)
            self.assertEqual(operator.modal(self.bpy.context, self.guard_event), {"PASS_THROUGH"})
            self.assertEqual(len(self.guard_timers), 1)
            callback: Callable[[], None] = self.guard_timers.pop()
            callback()
            self.assertFalse(self.guard_surfaces.visible)
            self.assertFalse(controller._SURFACE_CLEANUPS)
            self.surface_strokes.append(SimpleNamespace())
            self.assertEqual(operator.modal(self.bpy.context, self.guard_event), {"FINISHED", "PASS_THROUGH"})
            self.fills.selection_surfaces.assert_called_once_with(include_zero_opacity=True)

    def test_surface_guard_release_keeps_faces_available_for_native_final_dab(self) -> None:
        controller: ModuleType
        operator: DRAWING_OT_surface_capture
        controller, operator = self.surface_guard_setup()
        self.guard_event.type, self.guard_event.value = "LEFTMOUSE", "RELEASE"
        with patch.dict(sys.modules, self.imports):
            self.assertEqual(operator.invoke(self.bpy.context, self.guard_event), {"RUNNING_MODAL"})
            self.assertEqual(operator.modal(self.bpy.context, self.guard_event), {"FINISHED", "PASS_THROUGH"})
            self.assertTrue(self.guard_surfaces.visible)
            self.assertEqual(len(self.guard_timers), 1)
            callback: Callable[[], None] = self.guard_timers.pop()
            callback()
            self.assertFalse(self.guard_surfaces.visible)
            self.assertFalse(controller._SURFACE_CLEANUPS)

    def test_surface_guard_closes_pending_capture_when_native_stroke_or_window_ends(self) -> None:
        case: str
        for case in ("captured", "native_ended", "deactivate", "escape"):
            with self.subTest(case=case):
                controller: ModuleType
                operator: DRAWING_OT_surface_capture
                controller, operator = self.surface_guard_setup()
                with patch.dict(sys.modules, self.imports):
                    self.assertEqual(operator.invoke(self.bpy.context, self.guard_event), {"RUNNING_MODAL"})
                    operator.modal(self.bpy.context, self.guard_event)
                    self.assertTrue(self.guard_surfaces.visible)
                    if case == "captured":
                        self.surface_strokes.append(SimpleNamespace())
                    elif case == "native_ended":
                        self.bpy.context.window.modal_operators = []
                    elif case == "deactivate":
                        self.guard_event.type = "WINDOW_DEACTIVATE"
                    else:
                        self.guard_event.type = "ESC"
                    expected: set[str] = {"PASS_THROUGH"} if case == "escape" else {"FINISHED", "PASS_THROUGH"}
                    self.assertEqual(operator.modal(self.bpy.context, self.guard_event), expected)
                    self.assertFalse(self.guard_surfaces.visible)
                    self.assertFalse(self.guard_timers)
                    self.assertFalse(controller._SURFACE_CLEANUPS)

    def test_surface_guard_rejects_missing_native_operator_without_changing_faces(self) -> None:
        controller: ModuleType
        operator: DRAWING_OT_surface_capture
        controller, operator = self.surface_guard_setup()
        self.bpy.context.window.modal_operators = []
        with patch.dict(sys.modules, self.imports):
            self.assertEqual(operator.invoke(self.bpy.context, self.guard_event), {"CANCELLED"})
        self.assertFalse(self.guard_surfaces.visible)
        self.fills.selection_surfaces.assert_not_called()
        self.bpy.context.window_manager.modal_handler_add.assert_not_called()
        self.assertFalse(controller._SURFACE_CLEANUPS)

    def test_surface_guard_failure_during_timer_registration_closes_faces(self) -> None:
        controller: ModuleType
        operator: DRAWING_OT_surface_capture
        controller, operator = self.surface_guard_setup()
        self.bpy.app.timers.register.side_effect = RuntimeError("Timer unavailable")
        with patch.dict(sys.modules, self.imports):
            self.assertEqual(operator.invoke(self.bpy.context, self.guard_event), {"RUNNING_MODAL"})
            with self.assertRaisesRegex(RuntimeError, "Timer unavailable"):
                operator.modal(self.bpy.context, self.guard_event)
        self.assertFalse(self.guard_surfaces.visible)
        self.assertFalse(self.guard_timers)
        self.assertFalse(controller._SURFACE_CLEANUPS)
        self.fills.selection_surfaces.return_value.__exit__.assert_called_once()

    def test_stopping_session_closes_pending_surface_capture(self) -> None:
        controller: ModuleType
        operator: DRAWING_OT_surface_capture
        controller, operator = self.surface_guard_setup()
        pipe_fill: ModuleType = ModuleType("_toolbar.tools.pipe_fill")
        setattr(pipe_fill, "cancel", MagicMock())
        self.imports[pipe_fill.__name__] = pipe_fill
        with patch.dict(sys.modules, self.imports):
            self.assertEqual(operator.invoke(self.bpy.context, self.guard_event), {"RUNNING_MODAL"})
            operator.modal(self.bpy.context, self.guard_event)
            self.assertTrue(self.guard_surfaces.visible)
            controller.stop_session()
        self.assertFalse(self.guard_surfaces.visible)
        self.assertFalse(self.guard_timers)
        self.assertFalse(controller._SURFACE_CLEANUPS)

    def test_fill_opacity_command_and_restore_preserve_palette_and_reject_nonfinite_values(self) -> None:
        self.assertEqual(self.state.pencil.fill_opacity, 1.0)
        self.state.pencil.material = "Blue"
        self.state.pencil.tool = "builtin_brush.Fill"
        value: float
        expected: float
        for value, expected in ((0.35, 0.35), (-1.0, 0.0), (2.0, 1.0)):
            enter: MagicMock
            with self.subTest(command=value), patch.object(self.pencil, "enter") as enter:
                self.pencil.command(self.bpy.context, "fill_opacity", value)
                self.assertEqual(self.state.pencil.fill_opacity, expected)
                self.assertEqual(self.state.pencil.material, "Blue")
                self.assertEqual(self.state.pencil.tool, "builtin_brush.Fill")
                enter.assert_called_once()
            self.state_module.restore_pencil({"fill_opacity": value})
            self.assertEqual(self.state.pencil.fill_opacity, expected)
        invalid: object
        for invalid in (float("nan"), float("inf"), -float("inf"), "0.5", None):
            self.state.pencil.fill_opacity = 0.35
            with self.subTest(invalid=invalid), patch.object(self.pencil, "enter") as enter:
                self.pencil.command(self.bpy.context, "fill_opacity", invalid)
                self.assertEqual(self.state.pencil.fill_opacity, 0.35)
                enter.assert_not_called()
            self.state_module.restore_pencil({"fill_opacity": invalid})
            self.assertEqual(self.state.pencil.fill_opacity, 0.35)
        self.state_module.restore_pencil({"material": "Red"})
        self.assertEqual(self.state.pencil.fill_opacity, 0.35)
        self.assertEqual(self.state.pencil.material, "Red")

    def test_fill_settings_offer_opacity_and_draw_keeps_radius_and_strength(self) -> None:
        self.load_controller()
        self.state.pencil.fill_opacity = 0.35
        ui: ModuleType = self.imports["_toolbar.ui"]
        setattr(ui, "paint", MagicMock())
        ui.paint.measure.return_value = 0.0
        setattr(ui, "menus", MagicMock())
        setattr(self.blender_module, "gpu", MagicMock())
        self.bpy.context.scene.unit_settings.scale_length = 1.0
        with patch.dict(sys.modules, self.imports):
            overlay: ModuleType = module("_toolbar.ui.overlay", "app/drawing_app/ui/overlay.py")
            tool: str
            for tool in ("builtin_brush.Fill", "builtin.brush"):
                view: PencilView = self.pencil.PencilView(
                    tool,
                    "Fill" if tool == "builtin_brush.Fill" else "Pen",
                    (),
                    30.0,
                    False,
                    (1.0, 500.0),
                    1.0,
                    1.0,
                    False,
                    False,
                    False,
                    "",
                    "ORIGIN",
                    "VIEW",
                    "",
                )
                overlay._CURRENT.clear()
                with patch.object(self.pencil, "view", return_value=view):
                    overlay._pencil(self.bpy.context, 10.0, 800.0, 180.0)
                widget: Widget
                keys: set[str] = set()
                for widget in overlay._CURRENT:
                    keys.add(widget.key)
                    if widget.key == "slider_fill_opacity":
                        self.assertEqual(widget.tooltip, "Opacity")
                        self.assertEqual(widget.value, 35.0)
                        self.assertEqual((widget.minimum, widget.maximum, widget.scale), (0, 100, 0.01))
                        self.assertEqual(widget.action, "pencil")
                        self.assertEqual(widget.prefix, ("fill_opacity",))
                self.assertEqual("slider_size" in keys, tool == "builtin.brush")
                self.assertEqual("pressure_size" in keys, tool == "builtin.brush")
                self.assertEqual("slider_fill_opacity" in keys, tool == "builtin_brush.Fill")
                self.assertEqual("slider_strength" in keys, tool == "builtin.brush")
                self.assertEqual("pressure_strength" in keys, tool == "builtin.brush")

    def test_hex_colors_round_trip_through_linear_storage(self) -> None:
        state: ModuleType = self.state_module
        self.assertEqual(state.color_hex(state.parse_hex("FF8000")), "FF8000")
        self.assertEqual(state.color_hex(state.parse_hex("#f80")), "FF8800")
        self.assertEqual(state.color_hex((0.0, 0.0, 0.0)), "000000")
        text: str
        for text in ("GG0000", "12345", "", "#1234567"):
            self.assertIsNone(state.parse_hex(text), text)
        self.assertIsNone(state.clean_color((0.1, float("nan"), 0.2)))
        self.assertIsNone(state.clean_color((True, 0.0, 0.0)))
        self.assertEqual(state.clean_color([2, -1, 0.5]), (1.0, 0.0, 0.5))
        self.assertEqual(self.pencil.color_label((0.65, 0.06, 0.04)), "Red")
        self.assertEqual(self.pencil.color_label(state.parse_hex("FF8000")), "#FF8000")

    def test_every_tool_opens_on_the_default_magenta(self) -> None:
        state: ModuleType = self.state_module
        default: tuple[float, float, float] = state.DEFAULT_COLOR
        self.assertEqual(state.color_hex(default), "70197C")
        self.assertEqual(self.state.pencil.material, "#70197C")
        saved: dict[str, object] = {
            "color": [0.5, 0.5, 0.5],
            "brushes": {"grease": {"color": [0.1, 0.2, 0.3], "radius": 0.05}},
        }
        with (
            patch.object(state.Path, "is_file", return_value=True),
            patch.object(state.Path, "read_text", return_value=json.dumps(saved)),
        ):
            state.load_preferences()
        # Last session's colours are not restored at launch; other brush settings are.
        self.assertEqual(self.state.color, default)
        name: str
        for name in ("paint", "pipe", "grease"):
            self.assertEqual(self.state.brushes[name].color, default)
        self.assertEqual(self.state.brushes["grease"].radius, 0.05)
        # Opening a saved drawing restores its own tool colours.
        state.restore_brushes({"pipe": {"color": [0.1, 0.2, 0.3]}})
        self.assertEqual(self.state.brushes["pipe"].color, (0.1, 0.2, 0.3))

    def toolbox_widgets(self, tool: Tool, width: float, sketching: bool = False) -> dict[str, Widget]:
        """Draw the Tool Box with text measured at 7.5 px per character (13 px font)."""

        def measure(label: str, size: float = 13.0) -> float:
            return len(label) * 7.5 * size / 13.0

        self.load_controller()
        ui: ModuleType = self.imports["_toolbar.ui"]
        self.paint: MagicMock = MagicMock()
        setattr(ui, "paint", self.paint)
        self.paint.measure.side_effect = measure
        setattr(ui, "menus", MagicMock())
        setattr(self.blender_module, "gpu", MagicMock())
        self.bpy.context.scene.unit_settings.scale_length = 1.0
        self.state.tool = tool
        # A sketch is open with its own tool, or with another one while its brush is down.
        self.state.sketching = tool == "sketch" or sketching
        widgets: dict[str, Widget] = {}
        with patch.dict(sys.modules, self.imports):
            overlay: ModuleType = module("_toolbar.ui.overlay", "app/drawing_app/ui/overlay.py")
            overlay._CURRENT.clear()
            overlay._toolbox(self.bpy.context, width, 1400)
            widget: Widget
            for widget in overlay._CURRENT:
                widgets[widget.key] = widget
        self.overlay: ModuleType = overlay
        return widgets

    def test_a_chosen_tool_shows_only_its_own_color(self) -> None:
        widgets: dict[str, Widget] = self.toolbox_widgets("select", 220)
        self.assertIn("toolbox_hex", widgets)
        tool: Tool
        for tool in ("paint", "pipe", "printer", "extruder", "sketch"):
            widgets = self.toolbox_widgets(tool, 220)
            self.assertNotIn("toolbox_hex", widgets, tool)
            self.assertIn(f"tool_{tool}_hex", widgets, tool)
            # Sketch Mesh names its own colour, which it also shows while its brush is down.
            self.assertEqual(widgets[f"tool_{tool}_hex"].prefix, ("sketch",) if tool == "sketch" else ("tool",))

    def test_printer_settings_send_nozzle_flow_and_drop(self) -> None:
        widgets: dict[str, Widget] = self.toolbox_widgets("select", 220)
        self.assertIn("tool_printer", widgets)
        self.assertEqual((widgets["tool_printer"].action, widgets["tool_printer"].payload), ("tool", "printer"))
        widgets = self.toolbox_widgets("printer", 220)
        nozzle: Widget = widgets["slider_radius"]
        self.assertEqual((nozzle.action, nozzle.prefix, nozzle.maximum, nozzle.log), ("brush", ("radius",), 10.0, True))
        # Particles from 1 mm up to the nozzle's width (6 cm for the 3 cm radius), on a log scale.
        size: Widget = widgets["slider_size"]
        self.assertEqual((size.action, size.prefix, size.log), ("printer", ("size",), True))
        self.assertEqual((size.minimum, size.value), (0.001, 0.02))
        self.assertAlmostEqual(size.maximum, 0.06)
        self.state.printer.size = 0.5
        self.assertAlmostEqual(self.toolbox_widgets("printer", 220)["slider_size"].value, 0.06)
        flow: Widget = widgets["slider_rate"]
        self.assertEqual((flow.action, flow.prefix, flow.minimum, flow.maximum), ("printer", ("rate",), 1.0, 2000.0))
        self.assertEqual((flow.value, flow.scale), (200.0, 1.0))
        per_drop: Widget = widgets["slider_per_drop"]
        self.assertEqual((per_drop.action, per_drop.prefix, per_drop.minimum), ("printer", ("per_drop",), 1.0))
        self.assertEqual(per_drop.value, 1.0)
        drop: Widget = widgets["slider_drop"]
        self.assertEqual((drop.action, drop.prefix, drop.maximum), ("printer", ("drop",), 2.0))
        # Sticky shows a percentage and sends a share: 0 slumps like sand, 1 stands almost upright.
        sticky: Widget = widgets["slider_sticky"]
        self.assertEqual((sticky.action, sticky.prefix, sticky.tooltip), ("printer", ("sticky",), "Sticky"))
        self.assertEqual((sticky.minimum, sticky.maximum, sticky.scale, sticky.value), (0, 100, 0.01, 50.0))
        # A brush shape and pressure toggles do not apply to pouring.
        self.assertNotIn("brush_shape", widgets)
        self.assertNotIn("pressure_radius", widgets)

    def test_printer_settings_are_bounded(self) -> None:
        controller: ModuleType = self.load_controller()
        with patch.dict(sys.modules, self.imports):
            controller.dispatch("printer", ("rate", 5000.0))
            controller.dispatch("printer", ("per_drop", 2.6))
            controller.dispatch("printer", ("drop", -1.0))
            controller.dispatch("printer", ("flow", 1.0))
            controller.dispatch("printer", ("drop", float("inf")))
        self.assertEqual((self.state.printer.rate, self.state.printer.per_drop), (2000.0, 3))
        self.assertIsInstance(self.state.printer.per_drop, int)
        self.assertEqual(self.state.printer.drop, 0.0)
        with patch.dict(sys.modules, self.imports):
            controller.dispatch("printer", ("per_drop", 0))
            controller.dispatch("printer", ("size", 50.0))
        self.assertEqual(self.state.printer.per_drop, 1)
        self.assertEqual(self.state.printer.size, 20.0)
        # Stickiness keeps whole percents, from 0 to 1.
        with patch.dict(sys.modules, self.imports):
            controller.dispatch("printer", ("sticky", 0.4567))
        self.assertEqual(self.state.printer.sticky, 0.46)
        with patch.dict(sys.modules, self.imports):
            controller.dispatch("printer", ("sticky", 1.7))
        self.assertEqual(self.state.printer.sticky, 1.0)
        with patch.dict(sys.modules, self.imports):
            controller.dispatch("printer", ("sticky", -0.2))
        self.assertEqual(self.state.printer.sticky, 0.0)
        # In use, a particle is never wider than the nozzle.
        self.assertEqual(self.state.printer.particle(0.03), 0.06)
        self.assertFalse(hasattr(self.state.printer, "flow"))

    def test_extruder_settings_send_tube_size_flow_and_drop(self) -> None:
        widgets: dict[str, Widget] = self.toolbox_widgets("select", 220)
        self.assertEqual((widgets["tool_extruder"].action, widgets["tool_extruder"].payload), ("tool", "extruder"))
        widgets = self.toolbox_widgets("extruder", 220)
        tube: Widget = widgets["slider_radius"]
        self.assertEqual((tube.action, tube.prefix, tube.log), ("brush", ("radius",), True))
        self.assertEqual((tube.minimum, tube.maximum, tube.value), (0.001, 2.0, 0.01))
        flow: Widget = widgets["slider_flow"]
        self.assertEqual((flow.action, flow.prefix, flow.log), ("extruder", ("flow",), True))
        self.assertEqual((flow.minimum, flow.maximum, flow.value), (0.01, 3.0, 0.3))
        drop: Widget = widgets["slider_drop"]
        self.assertEqual((drop.action, drop.prefix, drop.maximum, drop.value), ("extruder", ("drop",), 2.0, 0.08))
        # Particle settings, a brush shape, and pressure toggles belong to other tools.
        self.assertNotIn("slider_rate", widgets)
        self.assertNotIn("brush_shape", widgets)
        self.assertNotIn("pressure_radius", widgets)

    def test_extruder_settings_are_bounded(self) -> None:
        controller: ModuleType = self.load_controller()
        with patch.dict(sys.modules, self.imports):
            controller.dispatch("extruder", ("flow", 50.0))
            controller.dispatch("extruder", ("drop", -1.0))
            controller.dispatch("extruder", ("drop", float("nan")))
            controller.dispatch("extruder", ("rate", 5.0))
        self.assertEqual((self.state.extruder.flow, self.state.extruder.drop), (3.0, 0.0))
        self.assertFalse(hasattr(self.state.extruder, "rate"))
        state: ModuleType = self.state_module
        with (
            patch.object(state.Path, "is_file", return_value=True),
            patch.object(state.Path, "read_text", return_value=json.dumps({"extruder": {"flow": 0.001, "drop": 0.5}})),
        ):
            state.load_preferences()
        self.assertEqual((self.state.extruder.flow, self.state.extruder.drop), (0.01, 0.5))

    def test_sketch_mesh_settings_offer_open_closed_curved_straight_and_detail(self) -> None:
        widgets: dict[str, Widget] = self.toolbox_widgets("select", 220)
        self.assertEqual((widgets["tool_sketch"].action, widgets["tool_sketch"].payload), ("tool", "sketch"))
        self.assertEqual(widgets["tool_sketch"].tooltip, "Sketch Mesh")
        widgets = self.toolbox_widgets("sketch", 220)
        line: Widget = widgets["slider_radius"]
        # The sketch's own brush, whichever tool is active while the brush is down.
        self.assertEqual((line.action, line.prefix, line.tooltip), ("sketch", ("radius",), "Line Size (m)"))
        self.assertEqual((line.minimum, line.maximum, line.value), (0.001, 0.2, 0.01))
        key: str
        payload: tuple[str, bool]
        for key, payload in (
            ("sketch_open", ("closed", False)),
            ("sketch_closed", ("closed", True)),
            ("sketch_curved", ("curved", True)),
            ("sketch_straight", ("curved", False)),
        ):
            self.assertEqual((widgets[key].action, widgets[key].payload), ("sketch", payload), key)
        # The two buttons of a row share its width.
        self.assertAlmostEqual(widgets["sketch_open"].bounds[2] * 2 + 4, 200.0)
        self.assertGreater(widgets["sketch_closed"].bounds[0], widgets["sketch_open"].bounds[0])
        detail: Widget = widgets["slider_detail"]
        self.assertEqual((detail.action, detail.prefix), ("sketch", ("detail",)))
        self.assertEqual((detail.minimum, detail.maximum, detail.value, detail.scale), (8.0, 64.0, 24.0, 1.0))
        self.assertEqual(widgets["axes_spokes"].payload, ("axes_spokes", True))
        self.assertEqual(widgets["tool_sketch_hex"].prefix, ("sketch",))
        self.assertEqual(cast("tuple[object, ...]", widgets["tool_color_preset_0"].payload)[0], "sketch")
        # Lines are round and of one width: no brush shape, no pressure toggle, no hardness or opacity.
        for key in ("brush_shape", "pressure_radius", "slider_hardness", "slider_opacity"):
            self.assertNotIn(key, widgets)
        # The check and cross wait for the first line.
        self.assertNotIn("sketch_confirm", widgets)
        self.assertNotIn("sketch_cancel", widgets)
        self.sketch_mesh.count.return_value = 3
        widgets = self.toolbox_widgets("sketch", 220)
        self.assertEqual((widgets["sketch_confirm"].action, widgets["sketch_confirm"].payload), ("sketch", "confirm"))
        # The cross leaves the sketch, which asks first.
        self.assertEqual((widgets["sketch_cancel"].action, widgets["sketch_cancel"].payload), ("sketch", "leave"))
        self.assertIn("Enter", widgets["sketch_confirm"].tooltip)
        self.assertIn("asks first", widgets["sketch_cancel"].tooltip)
        icons: list[str] = [call.args[0] for call in self.paint.icon.call_args_list]
        self.assertIn("check", icons)
        self.assertIn("cancel", icons)
        labels: list[str] = [call.args[0] for call in self.paint.text.call_args_list]
        self.assertIn("Sketch: 3 lines", labels)

    def test_an_open_sketch_keeps_its_panel_while_the_brush_is_down(self) -> None:
        widgets: dict[str, Widget] = self.toolbox_widgets("sketch", 220)
        # Brush up: the tool's icon leaves the sketch, and the brush button puts the brush down.
        self.assertEqual((widgets["tool_sketch"].action, widgets["tool_sketch"].payload), ("sketch", "leave"))
        self.assertEqual(
            (widgets["sketch_brush"].action, widgets["sketch_brush"].payload), ("sketch", ("brush", False))
        )
        self.assertEqual((widgets["tool_move"].action, widgets["tool_move"].payload), ("tool", "move"))
        self.assertEqual(widgets["tool_rotate"].payload, "rotate")
        self.assertEqual(widgets["tool_scale"].payload, "scale")
        self.assertIn("Esc puts the brush down.", [call.args[0] for call in self.paint.text.call_args_list])
        # The brush and the three move tools sit in one row under the icon.
        self.assertEqual(widgets["sketch_brush"].bounds[1], widgets["tool_scale"].bounds[1])
        self.assertLess(widgets["sketch_brush"].bounds[1], widgets["tool_sketch"].bounds[1])
        # Brush down, with Select: the same panel, not the tool chooser; the brush button picks it up again.
        self.state.sketching = True
        widgets = self.toolbox_widgets("select", 220, sketching=True)
        self.assertEqual(widgets["sketch_brush"].payload, ("brush", True))
        key: str
        for key in (
            "tool_sketch",
            "sketch_open",
            "sketch_straight",
            "slider_detail",
            "slider_radius",
            "tool_sketch_hex",
        ):
            self.assertIn(key, widgets, key)
        for key in ("tool_paint", "tool_pipe", "tool_grease", "toolbox_hex"):
            self.assertNotIn(key, widgets, key)
        self.assertIn("Brush is down: move your planes,", [call.args[0] for call in self.paint.text.call_args_list])
        # Brush down, with Move: the selected object's fields above the sketch's settings; Move again is Select.
        with patch.object(self.pencil, "editing", return_value=False):
            widgets = self.toolbox_widgets("move", 220, sketching=True)
        self.assertEqual(widgets["tool_move"].payload, "select")
        self.assertIn("transform_location_0", widgets)
        self.assertIn("sketch_open", widgets)
        self.assertGreater(widgets["transform_location_0"].bounds[1], widgets["slider_radius"].bounds[1])
        # A narrow Tool Box wraps the row instead of cutting it off.
        widgets = self.toolbox_widgets("sketch", 110)
        self.assertLess(widgets["tool_scale"].bounds[1], widgets["sketch_brush"].bounds[1])
        self.assertLess(widgets["slider_radius"].bounds[1], widgets["tool_scale"].bounds[1])

    def test_sketch_mesh_commands_are_bounded_and_record_one_undo_step(self) -> None:
        controller: ModuleType = self.load_controller()
        overlay: MagicMock = MagicMock()
        setattr(self.imports["_toolbar.ui"], "overlay", overlay)
        undo: MagicMock
        with (
            patch.dict(sys.modules, self.imports),
            patch.object(controller, "_undo_push") as undo,
            patch.object(self.pencil, "leave"),
        ):
            controller.dispatch("sketch", ("closed", True))
            controller.dispatch("sketch", ("curved", False))
            controller.dispatch("sketch", ("detail", 500))
            self.assertEqual((self.state.sketch.closed, self.state.sketch.curved), (True, False))
            self.assertEqual(self.state.sketch.detail, 64)
            controller.dispatch("sketch", ("detail", 11.6))
            self.assertEqual(self.state.sketch.detail, 12)
            self.assertIsInstance(self.state.sketch.detail, int)
            controller.dispatch("sketch", ("detail", float("nan")))
            controller.dispatch("sketch", ("detail", True))
            controller.dispatch("sketch", ("closed", "yes"))
            controller.dispatch("sketch", ("smooth", True))
            controller.dispatch("sketch", "explode")
            self.assertEqual((self.state.sketch.closed, self.state.sketch.detail), (True, 12))
            self.assertFalse(hasattr(self.state.sketch, "smooth"))
            # The line size is the sketch's own, whatever tool is active.
            self.state.tool = "select"
            controller.dispatch("sketch", ("radius", 5.0))
            controller.dispatch("sketch", ("radius", float("inf")))
            self.assertEqual((self.state.brushes["sketch"].radius, self.state.brushes["paint"].radius), (0.2, 0.02))
            undo.assert_not_called()
            # A mesh that was built is one undo step; a sketch that could not make one records nothing.
            controller.dispatch("sketch", "confirm")
            undo.assert_called_once_with("Sketch Mesh")
            self.sketch_mesh.confirm.return_value = None
            controller.dispatch("sketch", "confirm")
            undo.assert_called_once()

    def test_the_brush_goes_down_and_up_without_closing_the_sketch(self) -> None:
        controller: ModuleType = self.load_controller()
        overlay: MagicMock = MagicMock()
        setattr(self.imports["_toolbar.ui"], "overlay", overlay)
        undo: MagicMock
        with (
            patch.dict(sys.modules, self.imports),
            patch.object(controller, "_undo_push") as undo,
            patch.object(self.pencil, "leave"),
        ):
            controller.dispatch("tool", "sketch")
            self.assertEqual((self.state.tool, self.state.sketching), ("sketch", True))
            self.assertIn("Esc puts the brush down", self.state.status)
            controller.dispatch("sketch", ("brush", False))
            self.assertEqual((self.state.tool, self.state.sketching), ("select", True))
            self.assertIn("brush down", self.state.status)
            # Moving things with the brush down keeps the sketch open.
            tool: str
            for tool in ("move", "rotate", "scale", "select"):
                controller.dispatch("tool", tool)
                self.assertEqual((self.state.tool, self.state.sketching), (tool, True))
            controller.dispatch("sketch", ("brush", "toggle"))
            self.assertEqual((self.state.tool, self.state.sketching), ("sketch", True))
            controller.dispatch("sketch", ("brush", "toggle"))
            self.assertEqual((self.state.tool, self.state.sketching), ("select", True))
            controller.dispatch("sketch", ("brush", True))
            controller.dispatch("sketch", ("brush", True))
            controller.dispatch("sketch", ("brush", "up"))
            self.assertEqual((self.state.tool, self.state.sketching), ("sketch", True))
            self.sketch_mesh.cancel.assert_not_called()
            # Leaving a sketch that has lines asks first, and changes nothing by itself.
            self.sketch_mesh.lines.return_value = [MagicMock()]
            controller.dispatch("sketch", "leave")
            overlay.ask.assert_called_once()
            self.assertEqual(overlay.ask.call_args.args[0], "Do you want to cancel?")
            self.assertIn("will be removed", overlay.ask.call_args.args[1])
            self.assertEqual(overlay.ask.call_args.args[2:], ("sketch", "cancel"))
            self.assertEqual((self.state.tool, self.state.sketching), ("sketch", True))
            self.sketch_mesh.cancel.assert_not_called()
            # Yes: the lines go, the sketch closes, and it is one undo step.
            controller.dispatch("sketch", "cancel")
            self.sketch_mesh.cancel.assert_called_once()
            self.assertEqual((self.state.tool, self.state.sketching), ("select", False))
            self.assertEqual(self.state.status, "Sketch cancelled")
            undo.assert_called_once_with("Cancel sketch")
            # A sketch without lines closes without asking, and there is nothing to undo.
            controller.dispatch("tool", "sketch")
            self.sketch_mesh.lines.return_value = []
            self.sketch_mesh.cancel.return_value = False
            controller.dispatch("sketch", "leave")
            overlay.ask.assert_called_once()
            self.assertEqual((self.state.tool, self.state.sketching), ("select", False))
            undo.assert_called_once()
            # Another drawing tool closes the sketch; the saved preferences never hold an open one.
            controller.dispatch("tool", "sketch")
            controller.dispatch("tool", "pipe")
            self.assertEqual((self.state.tool, self.state.sketching), ("pipe", False))
        self.state.sketching = True
        written: MagicMock
        with (
            patch.object(self.state_module.Path, "write_text") as written,
            patch.object(self.state_module.Path, "mkdir"),
            patch.object(self.state_module.Path, "replace"),
            patch.object(self.state_module.Path, "unlink"),
        ):
            self.state_module.save_preferences()
        self.assertFalse(json.loads(written.call_args.args[0])["sketching"])

    def test_sketch_mesh_settings_load_within_bounds_and_lines_stay_round(self) -> None:
        state: ModuleType = self.state_module
        saved: dict[str, object] = {
            "sketch": {"closed": True, "curved": False, "detail": 500},
            "brushes": {"sketch": {"radius": 0.05, "shape": "square"}, "pipe": {"shape": "square"}},
            "sketching": True,
        }
        with (
            patch.object(state.Path, "is_file", return_value=True),
            patch.object(state.Path, "read_text", return_value=json.dumps(saved)),
        ):
            state.load_preferences()
        self.assertEqual(
            (self.state.sketch.closed, self.state.sketch.curved, self.state.sketch.detail), (True, False, 64)
        )
        self.assertEqual((self.state.brushes["sketch"].radius, self.state.brushes["sketch"].shape), (0.05, "circle"))
        self.assertEqual(self.state.brushes["pipe"].shape, "square")
        self.assertFalse(self.state.sketching)
        state.restore_sketch({"closed": "yes", "detail": float("inf")})
        state.restore_sketch(None)
        self.assertEqual((self.state.sketch.closed, self.state.sketch.detail), (True, 64))

    def test_escape_puts_the_brush_down_then_asks_before_cancelling_the_sketch(self) -> None:
        controller: ModuleType = self.load_controller()
        session: MagicMock = MagicMock()
        session.closed = False
        session.drawing.is_drawing = False
        session.navigation.active = False
        escape: SimpleNamespace = SimpleNamespace(type="ESC", value="PRESS", mouse_x=10, mouse_y=10)
        overlay: MagicMock = MagicMock()
        overlay.is_capturing.return_value = False
        setattr(self.imports["_toolbar.ui"], "overlay", overlay)
        dispatched: MagicMock
        with patch.dict(sys.modules, self.imports), patch.object(controller, "dispatch") as dispatched:
            # Brush up: Esc only puts it down.
            self.state.tool, self.state.sketching = "sketch", True
            result: set[str] = controller.DRAWING_OT_session.modal(session, self.bpy.context, escape)
            self.assertEqual(result, {"RUNNING_MODAL"})
            dispatched.assert_called_once_with("sketch", ("brush", False))
            # Brush down, with Select or a move tool: Esc leaves the sketch, which asks when it has lines.
            tool: Tool
            for tool in ("select", "move", "rotate", "scale"):
                self.state.tool = tool
                controller.DRAWING_OT_session.modal(session, self.bpy.context, escape)
                dispatched.assert_called_with("sketch", "leave")
            # Without a sketch, Esc returns to the Select tool as before.
            self.state.tool, self.state.sketching = "pipe", False
            controller.DRAWING_OT_session.modal(session, self.bpy.context, escape)
            dispatched.assert_called_with("tool", "select")
            self.assertEqual(dispatched.call_count, 6)

    def test_grease_pencil_follows_the_3d_cursors_plane_when_an_axis_view_turns_it(self) -> None:
        controller: ModuleType = self.load_controller()
        guides: ModuleType = ModuleType("_toolbar.viewport.guides")
        setattr(guides, "update_clipping", MagicMock())
        self.imports[guides.__name__] = guides
        setattr(self.imports["_toolbar.ui"], "overlay", MagicMock())
        session: MagicMock = MagicMock()
        session.closed = False
        session.previous_tick = 0.0
        session.previous_panel_tick = math.inf
        session.navigation.active = False
        session.cursor_axis = 1
        timer: SimpleNamespace = SimpleNamespace(type="TIMER", value="NOTHING", mouse_x=0, mouse_y=0)
        self.state.tool = "grease"
        self.state.cursor_active = self.state.cursor_placed = True
        self.state.cursor_axis = 1
        refresh: MagicMock

        def tick() -> None:
            session.previous_tick = 0.0
            self.assertEqual(controller.DRAWING_OT_session.modal(session, self.bpy.context, timer), {"PASS_THROUGH"})

        with (
            patch.dict(sys.modules, self.imports),
            patch.object(self.pencil, "sync_shelf"),
            patch.object(self.pencil, "refresh") as refresh,
        ):
            tick()
            refresh.assert_not_called()
            # Pressing 1 turned the plane to X: Draw mode is given it once.
            self.state.cursor_axis = 0
            tick()
            tick()
            refresh.assert_called_once()
            self.assertEqual(session.cursor_axis, 0)
            # While the view is still being turned, it waits.
            self.state.cursor_axis = 2
            session.navigation.active = True
            tick()
            refresh.assert_called_once()
            session.navigation.active = False
            tick()
            self.assertEqual(refresh.call_count, 2)
            # Without a placed cursor the plane is not in use, and other tools read it when they draw.
            self.state.cursor_placed = False
            self.state.cursor_axis = 1
            tick()
            self.state.cursor_placed = True
            self.state.tool = "pipe"
            self.state.cursor_axis = 0
            tick()
            self.assertEqual((refresh.call_count, session.cursor_axis), (2, 0))

    def test_a_question_holds_input_until_enter_escape_or_a_button_answers_it(self) -> None:
        self.toolbox_widgets("select", 220)
        overlay: ModuleType = self.overlay
        layout: MagicMock = self.imports["_toolbar.ui"].layout
        layout.role.return_value = "viewport"
        self.paint.wrap.return_value = [(0, 23), (24, 35)]
        dispatched: MagicMock = MagicMock()
        setattr(overlay, "_DISPATCH", dispatched)
        region: SimpleNamespace = SimpleNamespace(type="WINDOW", x=100, y=50)
        area: SimpleNamespace = SimpleNamespace(as_pointer=lambda: 77, regions=[region])
        context: SimpleNamespace = SimpleNamespace(
            preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1.0)),
            window=SimpleNamespace(screen=SimpleNamespace(areas=[area])),
        )

        def event(kind: str, value: str = "PRESS", x: float = 0.0, y: float = 0.0) -> SimpleNamespace:
            return SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y, shift=False, ctrl=False)

        def show() -> dict[str, Widget]:
            """Ask, and draw the question in a 600 x 400 viewport."""
            overlay.ask("Do you want to cancel?", "The lines you drew will be removed.", "sketch", "cancel")
            overlay._CURRENT.clear()
            overlay._question(600.0, 400.0)
            overlay._WIDGETS[77] = list(overlay._CURRENT)
            return {widget.key: widget for widget in overlay._CURRENT}

        def middle(widget: Widget) -> tuple[float, float]:
            return (100 + widget.bounds[0] + widget.bounds[2] / 2, 50 + widget.bounds[1] + widget.bounds[3] / 2)

        buttons: dict[str, Widget] = show()
        self.assertTrue(overlay.is_capturing())
        self.assertEqual((buttons["question_yes"].payload, buttons["question_no"].payload), (True, False))
        self.assertGreater(buttons["question_no"].bounds[0], buttons["question_yes"].bounds[0])
        texts: list[str] = [call.args[0] for call in self.paint.text.call_args_list]
        self.assertIn("Do you want to cancel?", texts)
        self.assertIn("The lines you drew will", texts)
        # Other keys and clicks elsewhere are held back and answer nothing.
        self.assertTrue(overlay.handle_event(context, event("A")))
        self.assertTrue(overlay.handle_event(context, event("LEFTMOUSE", x=105.0, y=55.0)))
        self.assertTrue(overlay.handle_event(context, event("RIGHTMOUSE")))
        self.assertTrue(overlay.handle_event(context, event("MOUSEMOVE", "NOTHING", 300.0, 200.0)))
        self.assertTrue(overlay.is_capturing())
        dispatched.assert_not_called()
        # Enter is yes.
        self.assertTrue(overlay.handle_event(context, event("RET")))
        dispatched.assert_called_once_with("sketch", "cancel")
        self.assertFalse(overlay.is_capturing())
        # Esc is no.
        show()
        self.assertTrue(overlay.handle_event(context, event("ESC")))
        self.assertFalse(overlay.is_capturing())
        dispatched.assert_called_once()
        # The buttons answer too.
        buttons = show()
        self.assertTrue(overlay.handle_event(context, event("LEFTMOUSE", "PRESS", *middle(buttons["question_no"]))))
        self.assertFalse(overlay.is_capturing())
        dispatched.assert_called_once()
        buttons = show()
        self.assertTrue(overlay.handle_event(context, event("LEFTMOUSE", "PRESS", *middle(buttons["question_yes"]))))
        self.assertEqual(dispatched.call_count, 2)
        self.assertFalse(overlay.is_capturing())
        # Numpad Enter also says yes, and losing the window says no.
        show()
        self.assertTrue(overlay.handle_event(context, event("NUMPAD_ENTER")))
        self.assertEqual(dispatched.call_count, 3)
        show()
        overlay.cancel_capture()
        self.assertFalse(overlay.is_capturing())
        self.assertEqual(dispatched.call_count, 3)

    def test_log_sliders_space_values_by_ratio(self) -> None:
        self.toolbox_widgets("printer", 220)
        overlay: ModuleType = self.overlay
        nozzle: Widget = overlay.Widget("slider_radius", (0.0, 0.0, 100.0, 24.0), minimum=0.002, maximum=10.0, log=True)
        self.assertAlmostEqual(overlay.slider_value(nozzle, 0.0), 0.002)
        self.assertAlmostEqual(overlay.slider_value(nozzle, 1.0), 10.0)
        # The middle of the track is the geometric middle, to three significant figures: 0.141 m between
        # 2 mm and 10 m.
        self.assertAlmostEqual(overlay.slider_value(nozzle, 0.5), 0.141)
        self.assertAlmostEqual(overlay.slider_value(nozzle, 0.5), math.sqrt(0.002 * 10.0), places=3)
        value: float
        for value in (0.002, 0.03, 0.5, 10.0):
            fraction: float = overlay.slider_fraction(value, 0.002, 10.0, True)
            self.assertAlmostEqual(overlay.slider_value(nozzle, fraction), value)
        flat: Widget = overlay.Widget("slider_rate", (0.0, 0.0, 100.0, 24.0), minimum=1.0, maximum=2001.0)
        self.assertAlmostEqual(overlay.slider_value(flat, 0.5), 1001.0)

    def test_printer_preferences_from_the_flow_share_start_at_the_default_rate(self) -> None:
        state: ModuleType = self.state_module
        texts: list[str] = [
            json.dumps({"printer": {"flow": 0.5, "drop": 0.3}}),
            json.dumps({"printer": {"rate": 50, "per_drop": 7.4, "drop": 0.3}}),
        ]
        with (
            patch.object(state.Path, "is_file", return_value=True),
            patch.object(state.Path, "read_text", side_effect=texts),
        ):
            state.load_preferences()
            # The old share is not read as particles per second, which would have been 1 per second.
            self.assertEqual((self.state.printer.rate, self.state.printer.drop), (200.0, 0.3))
            state.load_preferences()
            self.assertEqual((self.state.printer.rate, self.state.printer.per_drop), (50.0, 7))
            # Preferences saved before the Sticky setting start from its default.
            self.assertEqual(self.state.printer.sticky, 0.5)

    def test_rgb_fields_fit_three_digits_in_a_narrow_tool_box(self) -> None:
        # 159 is a saved Tool Box width whose fields used to show "...".
        widgets: dict[str, Widget] = self.toolbox_widgets("select", 159)
        blue: Widget = widgets["toolbox_channel_2"]
        self.assertLessEqual(blue.bounds[0] + blue.bounds[2], 159 - 10 + 0.01)
        shown: dict[str, tuple[float, float]] = {}
        args: tuple[object, ...]
        kwargs: dict[str, object]
        for args, kwargs in self.paint.text.call_args_list:
            drawn: str = str(args[0])
            size: float = cast(float, args[3]) if len(args) > 3 else 13.0
            shown[drawn] = (size, cast(float, kwargs.get("max_width", 0.0)))
        # The default colour's channels, each drawn whole inside its field.
        label: str
        for label in [str(round(c * 255)) for c in self.state_module.linear_to_srgb(self.state_module.DEFAULT_COLOR)]:
            size, room = shown[label]
            self.assertGreaterEqual(room, len(label) * 7.5 * size / 13.0, label)

    def test_navigation_cycles_orbit_fly_walk_and_bounds_walk_settings(self) -> None:
        controller: ModuleType = self.load_controller()
        keymap: ModuleType = ModuleType("_toolbar.viewport.keymap")
        setattr(keymap, "update", MagicMock())
        setattr(self.viewport_module, "keymap", keymap)
        self.imports[keymap.__name__] = keymap
        self.state.navigation = "orbit"
        seen: list[str] = []
        with patch.dict(sys.modules, self.imports):
            _turn: int
            for _turn in range(4):
                controller.dispatch("navigation", "next")
                seen.append(self.state.navigation)
            self.assertEqual(seen, ["fly", "walk", "orbit", "fly"])
            self.assertEqual(self.state.status, "Navigation: Fly")
            controller.dispatch("navigation", "walk")
            self.assertEqual(self.state.navigation, "walk")
            controller.dispatch("navigation", "zoom")
            self.assertEqual(self.state.navigation, "walk")
            # Slider drags round to 0.1 m/s and 1 cm.
            controller.dispatch("walk", ("walk_speed", 3.04))
            controller.dispatch("walk", ("eye_level", 99.0))
            controller.dispatch("walk", ("eye_level", float("nan")))
            controller.dispatch("walk", ("zoom_speed", 5.0))
        self.assertEqual((self.state.walk_speed, self.state.eye_level), (3.0, 10.0))
        self.assertEqual(self.state.zoom_speed, 1.0)

    def test_walk_settings_load_within_bounds(self) -> None:
        state: ModuleType = self.state_module
        saved: dict[str, object] = {"navigation": "walk", "walk_speed": 0.0, "eye_level": 1.75}
        with (
            patch.object(state.Path, "is_file", return_value=True),
            patch.object(state.Path, "read_text", return_value=json.dumps(saved)),
        ):
            state.load_preferences()
        self.assertEqual((self.state.navigation, self.state.walk_speed, self.state.eye_level), ("walk", 0.1, 1.75))

    def test_toolbox_color_sets_every_tool_and_tool_color_only_the_active_tool(self) -> None:
        controller: ModuleType = self.load_controller()
        blue: tuple[float, float, float] = (0.2, 0.4, 0.6)
        red: tuple[float, float, float] = (1.0, 0.0, 0.0)
        self.state.tool = "paint"
        with patch.dict(sys.modules, self.imports):
            controller._set_color(("toolbox", "rgb", blue))
            self.assertEqual(self.state.color, blue)
            name: str
            for name in ("paint", "pipe", "grease"):
                self.assertEqual(self.state.brushes[name].color, blue)
            controller._set_color(("tool", "rgb", red))
            self.assertEqual(self.state.brushes["paint"].color, red)
            self.assertEqual(self.state.brushes["pipe"].color, blue)
            self.assertEqual(self.state.color, blue)
            # The G field edits one sRGB channel of the active tool's colour.
            controller._set_color(("tool", 1, 255))
            self.assertEqual(self.state_module.color_hex(self.state.brushes["paint"].color), "FFFF00")
            # Without a drawing tool there is no tool colour to change.
            self.state.tool = "select"
            controller._set_color(("tool", "rgb", blue))
            self.assertEqual(self.state_module.color_hex(self.state.brushes["paint"].color), "FFFF00")

    def test_grease_pencil_material_follows_color_after_drag_release_only(self) -> None:
        controller: ModuleType = self.load_controller()
        orange: tuple[float, float, float] = (1.0, 0.2, 0.0)
        self.state.tool = "grease"
        command: MagicMock
        with patch.dict(sys.modules, self.imports), patch.object(self.pencil, "command") as command:
            controller._set_color(("tool", "preview", orange))
            self.assertEqual(self.state.brushes["grease"].color, orange)
            command.assert_not_called()
            controller._set_color(("tool", "rgb", orange))
            command.assert_called_once()
            self.assertEqual(command.call_args.args[1:], ("color", orange))
            command.reset_mock()
            self.state.tool = "pipe"
            controller._set_color(("toolbox", "rgb", orange))
            command.assert_not_called()

    def test_pinned_brush_materials_and_tint_take_the_grease_pencil_color(self) -> None:
        purple: tuple[float, float, float] = self.state_module.DEFAULT_COLOR
        airbrush: FakeMaterial = FakeMaterial("Airbrush", (0.0, 0.0, 0.0, 0.8))
        plain: FakeMaterial = FakeMaterial("#70197C", (*purple, 1.0))
        plain["drawing_pencil"] = "#70197C"
        materials: list[FakeMaterial] = [airbrush, plain]
        obj: FakePencil = FakePencil([plain])
        settings: SimpleNamespace = SimpleNamespace(use_material_pin=True, material=airbrush)
        brush: SimpleNamespace = SimpleNamespace(
            name="Airbrush", gpencil_brush_type="DRAW", color=(0.2, 1.0, 0.2), gpencil_settings=settings
        )
        self.bpy.data.materials = materials
        self.pencil.color_brush(obj, brush, purple)
        variant: FakeMaterial | None = obj.active_material
        assert variant is not None
        self.assertIsNot(variant, airbrush)
        self.assertEqual(variant.name, "Airbrush #70197C")
        # A linked brush keeps its pin's target, so the pin is off and the copy is the active material.
        self.assertFalse(settings.use_material_pin)
        # As in Blender, an unpinned brush then reports no material.
        settings.material = None
        # The brush material's texture and opacity stay; only the colour changes.
        self.assertEqual(variant.grease_pencil.color, (*purple, 0.8))
        self.assertEqual(airbrush.grease_pencil.color, (0.0, 0.0, 0.0, 0.8))
        materials.append(variant)
        # Repeated calls reuse the copy; a new colour makes another from the same style.
        self.pencil.color_brush(obj, brush, purple)
        self.assertEqual(obj.data.materials, [plain, variant])
        self.pencil.color_brush(obj, brush, (1.0, 0.0, 0.0))
        red: FakeMaterial | None = obj.active_material
        assert red is not None
        self.assertEqual(red.name, "Airbrush #FF0000")
        self.assertEqual(red.get("drawing_pencil_style"), "Airbrush")
        self.assertEqual(brush.color, (0.2, 1.0, 0.2))
        # A plain brush afterwards goes back to the plain colour material.
        pencil_brush: SimpleNamespace = SimpleNamespace(
            name="Pencil",
            gpencil_brush_type="DRAW",
            color=(0.2, 1.0, 0.2),
            gpencil_settings=SimpleNamespace(use_material_pin=False, material=None),
        )
        with patch.object(self.pencil, "material_slot", return_value=0) as slot:
            self.pencil.color_brush(obj, pencil_brush, purple)
        slot.assert_called_once_with(obj, purple, False)
        self.assertIs(obj.active_material, plain)
        tint: SimpleNamespace = SimpleNamespace(
            name="Tint",
            gpencil_brush_type="TINT",
            color=(0.2, 1.0, 0.2),
            gpencil_settings=SimpleNamespace(use_material_pin=False, material=None),
        )
        self.pencil.color_brush(obj, tint, purple)
        self.assertEqual(self.state_module.color_hex(self.state_module.srgb_to_linear(tint.color)), "70197C")


class FakePencil:
    """The app's Grease Pencil object, with material slots as `color_brush` uses them."""

    def __init__(self, materials: list[FakeMaterial]) -> None:
        self.type: str = "GREASEPENCIL"
        self.data: SimpleNamespace = SimpleNamespace(materials=materials)
        self.active_material_index: int = 0

    def get(self, key: str) -> bool:
        return key == "drawing_pencil"

    @property
    def material_slots(self) -> list[SimpleNamespace]:
        materials: list[FakeMaterial] = self.data.materials
        return [SimpleNamespace(material=material) for material in materials]

    @property
    def active_material(self) -> FakeMaterial | None:
        materials: list[FakeMaterial] = self.data.materials
        return materials[self.active_material_index] if self.active_material_index < len(materials) else None


class FakeMaterial(dict[str, object]):
    """A Grease Pencil material with custom properties, as `color_brush` reads and copies it."""

    def __init__(self, name: str, color: tuple[float, float, float, float]) -> None:
        super().__init__()
        self.name: str = name
        self.grease_pencil: SimpleNamespace = SimpleNamespace(color=color, fill_color=(*color[:3], 0.0))

    def copy(self) -> FakeMaterial:
        twin: FakeMaterial = FakeMaterial(self.name + ".001", self.grease_pencil.color)
        twin.grease_pencil.fill_color = self.grease_pencil.fill_color
        twin.update(self)
        return twin


if __name__ == "__main__":
    unittest.main()
