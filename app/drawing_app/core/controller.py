"""Application commands and input routing around Blender's native viewport."""

from __future__ import annotations

import math
import time
from typing import Any, cast

from ..tools.drawing import DrawingController
from ..viewport import NavigationController
from .blender import bpy
from .project import persist_scene
from .state import AppState, Tool, get_state, save_preferences


def redraw() -> None:
    window: Any
    area: Any
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def viewport_context() -> tuple[Any, Any]:
    from ..ui import layout

    area: Any = layout.viewport_area(bpy.context)
    region: Any = None
    candidate: Any
    if area is not None:
        for candidate in area.regions:
            if candidate.type == "WINDOW":
                region = candidate
                break
    return area, region


def set_tool(name: str) -> None:
    from ..viewport import configure_viewport

    if name not in ("select", "move", "rotate", "scale", "paint", "pipe", "grease"):
        return
    state: AppState = get_state()
    state.tool = cast(Tool, name)
    area: Any
    region: Any
    area, region = viewport_context()
    if area is not None and region is not None:
        tool_id: str = {"move": "builtin.move", "rotate": "builtin.rotate", "scale": "builtin.scale"}.get(
            name, "builtin.select_box"
        )
        with bpy.context.temp_override(area=area, region=region):
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.wm.tool_set_by_id(name=tool_id)
        configure_viewport(area)
    state.status = {
        "paint": "Paint: drag to draw on the active canvas",
        "pipe": "Pipe: drag a 3D stroke",
        "grease": "Grease Pencil: drag to draw a native pencil stroke",
    }.get(name, "")


def _undo_push(message: str) -> None:
    persist_scene()
    if not bpy.app.background:
        bpy.ops.ed.undo_push(message=message)


def _transform(payload: tuple[str, int, float]) -> None:
    from ..tools.layers import object_locked

    kind: str
    axis: int
    value: float
    kind, axis, value = payload
    obj: Any = bpy.context.view_layer.objects.active
    if obj is None or object_locked(bpy.context, obj) or axis not in (0, 1, 2):
        return
    attribute: str = {
        "move": "location",
        "location": "location",
        "rotate": "rotation_euler",
        "rotation": "rotation_euler",
        "scale": "scale",
    }.get(kind, kind)
    if attribute not in ("location", "rotation_euler", "scale"):
        return
    if attribute == "rotation_euler":
        value = math.radians(value)
    elif attribute == "location":
        value /= max(float(bpy.context.scene.unit_settings.scale_length), 1e-12)
    if not math.isfinite(value):
        return
    delta: float = value - float(getattr(obj, attribute)[axis])
    target: Any
    for target in bpy.context.selected_objects:
        if not object_locked(bpy.context, target):
            getattr(target, attribute)[axis] += delta
    _undo_push("Transform drawing")


def _file_command(command: str) -> None:
    from ..tools.layers import ensure_scene

    if _ACTIVE is not None and _ACTIVE.drawing.is_drawing:
        _ACTIVE.drawing.finish(bpy.context)

    if command == "new":
        bpy.ops.drawing.new_project("INVOKE_DEFAULT")
    elif command == "open":
        bpy.ops.drawing.open_project("INVOKE_DEFAULT")
    elif command == "save" and bpy.data.filepath and not bpy.context.scene.get("drawing_unsaved_project", False):
        persist_scene()
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
        get_state().status = "Drawing saved"
    elif command in ("save", "save_as"):
        bpy.ops.drawing.save_project("INVOKE_DEFAULT")
    elif command in ("import", "import_usd"):
        bpy.ops.drawing.import_usd("INVOKE_DEFAULT")
        ensure_scene()
    elif command in ("export", "export_usd"):
        bpy.ops.drawing.export_usd("INVOKE_DEFAULT")
    elif command == "quit":
        save_preferences()
        bpy.ops.wm.quit_blender("INVOKE_DEFAULT")


def _edit_command(command: str) -> None:
    area: Any
    region: Any
    area, region = viewport_context()
    if area is None or region is None:
        return
    with bpy.context.temp_override(area=area, region=region):
        if command == "undo":
            if bpy.ops.ed.undo.poll():
                bpy.ops.ed.undo()
        elif command == "redo":
            if bpy.ops.ed.redo.poll():
                bpy.ops.ed.redo()
        elif command == "select_all":
            bpy.ops.object.select_all(action="SELECT")
        elif command == "delete":
            bpy.ops.object.delete()
            _undo_push("Delete selection")
        elif command == "duplicate":
            bpy.ops.object.duplicate()
            _undo_push("Duplicate selection")


def _create(kind: str) -> None:
    from ..tools.layers import active_collection, ensure_scene

    area: Any
    region: Any
    area, region = viewport_context()
    if area is None or region is None:
        return
    with bpy.context.temp_override(area=area, region=region):
        location: Any = bpy.context.scene.cursor.location.copy() if get_state().cursor_active else (0.0, 0.0, 0.0)
        if kind == "cube":
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
        elif kind == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.5, location=location)
        elif kind == "plane":
            bpy.ops.mesh.primitive_plane_add(size=2.0, location=location)
        elif kind == "cylinder":
            bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.5, depth=1.0, location=location)
        else:
            return
        obj: Any = bpy.context.object
        obj.color = (0.55, 0.55, 0.55, 1.0)
        obj.show_wire = True
        obj.show_all_edges = True
        ensure_scene()
        destination: Any = active_collection(bpy.context)
        if obj.name not in destination.objects:
            destination.objects.link(obj)
        collection: Any
        for collection in tuple(obj.users_collection):
            if collection != destination:
                collection.objects.unlink(obj)
        _undo_push("Create " + kind)
    set_tool("move")


def _layer_command(payload: tuple[str, str]) -> None:
    from ..tools import layers

    operation: str
    key: str
    operation, key = payload
    context: Any = bpy.context
    if operation == "add":
        layers.create_group(context)
    elif operation == "group":
        layers.create_group(context, selected=True)
    elif operation == "ungroup":
        layers.ungroup(context, key)
    elif operation == "active":
        layers.activate(context, key)
    elif operation == "select":
        layers.select(context, key)
    elif operation == "delete":
        layers.delete(context, key)
    elif operation in ("visible", "lock"):
        row: Any
        for row in layers.layer_rows(context):
            if row.key == key:
                if operation == "visible":
                    layers.set_visible(context, key, not row.visible)
                else:
                    layers.set_locked(context, key, not row.locked)
                break
    _undo_push("Change drawing layer")


def _object_command(payload: tuple[str, str]) -> None:
    from ..tools.layers import object_locked

    operation: str
    name: str
    operation, name = payload
    obj: Any = bpy.data.objects.get(name)
    if obj is None:
        return
    if operation == "visible":
        obj.hide_set(not obj.hide_get())
    elif operation == "lock":
        obj.hide_select = not obj.hide_select
        if obj.hide_select:
            obj.select_set(False)
            if bpy.context.view_layer.objects.active == obj:
                bpy.context.view_layer.objects.active = None
    elif operation == "select" and not object_locked(bpy.context, obj) and not obj.hide_get():
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    if operation in ("visible", "lock"):
        _undo_push("Change object " + operation)


def dispatch(action: str, payload: object = None) -> None:
    """Single command boundary shared by custom widgets and keyboard shortcuts."""
    from ..tools import layers
    from ..ui import layout
    from ..viewport import configure_viewport

    state: AppState = get_state()
    error: RuntimeError | ValueError | TypeError | AttributeError
    try:
        if action == "tool":
            set_tool(str(payload))
        elif action == "option":
            name: str
            enabled: bool
            name, enabled = cast(tuple[str, bool], payload)
            if name in ("drawing_plane", "on_object", "axes_spokes", "cursor_active", "ruler", "hud"):
                setattr(state, name, enabled)
                if name == "cursor_active":
                    state.cursor_placing = enabled and not state.cursor_placed
        elif action == "cursor_place":
            state.cursor_active = True
            state.cursor_placing = True
            state.status = "Click the viewport to place the 3D cursor"
        elif action == "brush":
            field: str
            value: object
            field, value = cast(tuple[str, object], payload)
            if field in ("radius", "hardness", "opacity") and isinstance(value, (float, int)):
                minimum: float = 0.00001 if field == "radius" else 0.0
                maximum: float = 100.0 if field == "radius" else 1.0
                setattr(state.brush, field, max(minimum, min(maximum, float(value))))
            elif field in ("pressure_size", "pressure_hardness", "pressure_opacity"):
                setattr(state.brush, field, bool(value))
            elif field == "shape" and value in ("circle", "square", "triangle"):
                state.brush.shape = str(value)
            elif field == "color" and isinstance(value, tuple) and len(value) == 3:
                state.brush.color = cast(tuple[float, float, float], value)
        elif action == "cursor":
            axis: int
            number: float
            axis, number = cast(tuple[int, float], payload)
            if axis in (0, 1, 2) and math.isfinite(number):
                bpy.context.scene.cursor.location[axis] = number / max(
                    float(bpy.context.scene.unit_settings.scale_length), 1e-12
                )
                state.cursor_placed = True
                state.cursor_placing = False
                _undo_push("Move 3D cursor")
        elif action == "transform":
            _transform(cast(tuple[str, int, float], payload))
        elif action == "navigation" and payload in ("orbit", "fly"):
            state.navigation = str(payload)
        elif action == "theme" and payload in ("light", "dark"):
            state.theme = str(payload)
        elif action == "units" and payload in ("m", "mm"):
            state.units = str(payload)
        elif action == "zoom_speed" and isinstance(payload, (float, int)):
            state.zoom_speed = max(0.1, min(10.0, float(payload)))
        elif action == "fps_limit" and payload in (0, 30, 60, 120):
            state.fps_limit = int(cast(int, payload))
        elif action == "file":
            _file_command(str(payload))
        elif action == "edit":
            _edit_command(str(payload))
        elif action == "create":
            _create(str(payload))
        elif action == "layer":
            _layer_command(cast(tuple[str, str], payload))
        elif action == "object":
            _object_command(cast(tuple[str, str], payload))
        elif action == "rename":
            kind: str
            key: str
            label: str
            kind, key, label = cast(tuple[str, str, str], payload)
            if label.strip():
                if kind == "layer":
                    layers.rename(bpy.context, key, label)
                elif kind == "object":
                    renamed: Any = bpy.data.objects.get(key)
                    if renamed is not None:
                        layers.rename(bpy.context, "object:" + key, label)
                _undo_push("Rename " + kind)
        elif action == "plane":
            if payload in ("new", "add", "create"):
                layers.create_plane(bpy.context)
                _undo_push("Create drawing plane")
            else:
                layers.activate(bpy.context, str(payload))
        elif action == "frame":
            area: Any
            region: Any
            area, region = viewport_context()
            if _ACTIVE is not None and area is not None and region is not None:
                _ACTIVE.navigation.frame_selected(bpy.context, area, region)
        elif action in ("view_axis", "cursor_view"):
            from ..core.blender import Vector
            from ..viewport.navigation import align_axis

            view_area: Any
            view_region: Any
            view_area, view_region = viewport_context()
            if view_area is not None:
                view: Any = view_area.spaces.active.region_3d
                if payload == "PERSPECTIVE":
                    view.view_perspective = "PERSP"
                    view.view_rotation = Vector((8.0, -10.0, 7.0)).to_track_quat("Z", "Y")
                elif payload in ("X", "Y", "Z"):
                    axis = {"X": 0, "Y": 1, "Z": 2}[str(payload)]
                    if action == "cursor_view":
                        state.cursor_side = -state.cursor_side if state.cursor_axis == axis else 1
                        state.cursor_axis = axis
                    align_axis(view, axis, state.cursor_side if action == "cursor_view" else 1)
                if action == "cursor_view" and state.cursor_active:
                    view.view_location = bpy.context.scene.cursor.location
                view.update()
        elif action == "layout_reset":
            from .. import restart_workspace

            restart_workspace()
        area = layout.viewport_area(bpy.context)
        if area is not None:
            configure_viewport(area)
        save_preferences()
        redraw()
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        state.status = str(error)
        print(f"Drawing command {action}: {error}")
        redraw()


_ACTIVE: DRAWING_OT_session | None = None


class DRAWING_OT_session(bpy.types.Operator):
    bl_idname: str = "drawing.session"
    bl_label: str = "3D Drawing Input"
    navigation: NavigationController
    drawing: DrawingController
    timer: Any
    previous_tick: float
    previous_panel_tick: float
    previous_motion_tick: float
    closed: bool

    def invoke(self, context: Any, event: Any) -> set[str]:
        global _ACTIVE
        if _ACTIVE is not None:
            return {"CANCELLED"}
        self.navigation = NavigationController()
        self.drawing = DrawingController()
        self.previous_tick = time.monotonic()
        self.previous_panel_tick = self.previous_tick
        self.previous_motion_tick = 0.0
        self.closed = False
        self.timer = context.window_manager.event_timer_add(1.0 / 120.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        _ACTIVE = self
        return {"RUNNING_MODAL"}

    def modal(self, context: Any, event: Any) -> set[str]:
        from ..ui import overlay

        if self.closed:
            return {"FINISHED"}
        if context.window is None or context.window.screen is None:
            return {"PASS_THROUGH"}
        if event.type == "WINDOW_DEACTIVATE":
            if self.drawing.is_drawing:
                self.drawing.cancel()
            self.drawing.clear_hover()
            self.navigation.cancel()
            overlay.cancel_capture()
            return {"PASS_THROUGH"}
        if event.type == "TIMER":
            from ..viewport.guides import update_clipping

            now: float = time.monotonic()
            limit: int = get_state().fps_limit
            if not limit or now - self.previous_tick >= 1.0 / limit:
                self.navigation.tick(context, min(0.1, now - self.previous_tick))
                self.previous_tick = now
                viewport_area: Any
                viewport_region: Any
                viewport_area, viewport_region = viewport_context()
                if viewport_area is not None:
                    update_clipping(viewport_area)
            if now - self.previous_panel_tick >= 0.25:
                panel: Any
                for panel in context.window.screen.areas:
                    if panel.type == "IMAGE_EDITOR":
                        panel.tag_redraw()
                self.previous_panel_tick = now
            return {"PASS_THROUGH"}
        if event.type.startswith("TIMER"):
            return {"PASS_THROUGH"}
        if overlay.is_capturing() and overlay.handle_event(context, event):
            return {"RUNNING_MODAL"}
        if self.drawing.is_drawing and event.type == "Z" and event.ctrl and event.value == "PRESS":
            self.drawing.cancel()
            return {"RUNNING_MODAL"}
        if self.drawing.is_drawing and self.drawing.viewport_context is not None:
            drawing_area: Any
            drawing_region: Any
            drawing_area, drawing_region = self.drawing.viewport_context
            with context.temp_override(area=drawing_area, region=drawing_region):
                if self.drawing.handle(context, event, drawing_area, drawing_region):
                    return {"RUNNING_MODAL"}
        if self.navigation.active and self.navigation.viewport_context is not None:
            if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
                motion_time: float = time.monotonic()
                limit = get_state().fps_limit
                if limit and motion_time - self.previous_motion_tick < 1.0 / limit:
                    return {"RUNNING_MODAL"}
                self.previous_motion_tick = motion_time
            nav_area: Any
            nav_region: Any
            nav_area, nav_region = self.navigation.viewport_context
            with context.temp_override(area=nav_area, region=nav_region):
                if self.navigation.handle(context, event, nav_area, nav_region):
                    return {"RUNNING_MODAL"}
        area: Any = None
        candidate: Any
        for candidate in context.window.screen.areas:
            if (
                candidate.x <= event.mouse_x < candidate.x + candidate.width
                and candidate.y <= event.mouse_y < candidate.y + candidate.height
            ):
                area = candidate
                break
        if area is None or area.type not in ("VIEW_3D", "IMAGE_EDITOR"):
            return {"PASS_THROUGH"}
        region: Any = None
        for candidate in area.regions:
            if candidate.type == "WINDOW":
                region = candidate
                break
        if region is None:
            return {"PASS_THROUGH"}
        with context.temp_override(area=area, region=region):
            if event.value == "PRESS":
                if event.ctrl and event.type == "S":
                    dispatch("file", "save_as" if event.shift else "save")
                    return {"RUNNING_MODAL"}
                if event.ctrl and event.type == "O":
                    dispatch("file", "open")
                    return {"RUNNING_MODAL"}
                if event.ctrl and event.type == "N":
                    dispatch("file", "new")
                    return {"RUNNING_MODAL"}
                if event.ctrl and event.type == "Z":
                    dispatch("edit", "redo" if event.shift else "undo")
                    return {"RUNNING_MODAL"}
                if event.ctrl and event.type == "D":
                    dispatch("edit", "duplicate")
                    return {"RUNNING_MODAL"}
                if event.type in ("DEL", "X") and get_state().tool not in ("paint", "pipe", "grease"):
                    dispatch("edit", "delete")
                    return {"RUNNING_MODAL"}
                if event.type == "F" and not event.ctrl:
                    dispatch("frame")
                    return {"RUNNING_MODAL"}
                if (
                    event.type in ("TAB", "N", "T", "SPACE")
                    or (
                        event.type.startswith("F") and event.type[1:].isdigit() and event.type != "F2" and not event.alt
                    )
                    or (event.ctrl and event.type in ("PAGE_UP", "PAGE_DOWN"))
                    or (event.shift and event.type.startswith("F") and event.type[1:].isdigit())
                ):
                    return {"RUNNING_MODAL"}
            if overlay.handle_event(context, event):
                self.drawing.clear_hover()
                return {"RUNNING_MODAL"}
            if area.type != "VIEW_3D":
                return {"PASS_THROUGH"}
            if self.navigation.handle(context, event, area, region):
                return {"RUNNING_MODAL"}
            if self.drawing.handle(context, event, area, region):
                return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def cancel(self, context: Any) -> None:
        global _ACTIVE
        if not self.closed:
            self.drawing.destroy()
            self.navigation.destroy()
            context.window_manager.event_timer_remove(self.timer)
            self.closed = True
        _ACTIVE = None


def stop_session() -> None:
    global _ACTIVE
    if _ACTIVE is not None:
        try:
            _ACTIVE.cancel(bpy.context)
        except ReferenceError:
            _ACTIVE = None


def register() -> None:
    bpy.utils.register_class(DRAWING_OT_session)


def unregister() -> None:
    stop_session()
    bpy.utils.unregister_class(DRAWING_OT_session)
