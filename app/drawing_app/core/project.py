"""Native .blend projects and USD interchange with application metadata."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .blender import bpy
from .state import ROOT, get_state, restore_brushes, restore_pencil, restore_printer

SCENE_STATE: str = "drawing_app_state"
# Blender writes compressed .blend files as Zstandard frames.
ZSTD_MAGIC: bytes = b"\x28\xb5\x2f\xfd"
# Windows, workspaces and screens; libraries are written with the data linked from them.
INTERFACE_TYPES: tuple[Any, ...] = (
    bpy.types.WindowManager,
    bpy.types.WorkSpace,
    bpy.types.Screen,
    bpy.types.Library,
)
_STARTUP_FILE: bool = False


class DRAWING_OT_new_project(bpy.types.Operator):
    bl_idname: str = "drawing.new_project"
    bl_label: str = "New Drawing"
    bl_description: str = "Start a new drawing; unsaved changes will be discarded"

    def invoke(self, context: Any, event: Any) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: Any) -> set[str]:
        from ..tools.layers import ensure_scene
        from .controller import set_tool

        obj: Any
        collection: Any
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in list(bpy.data.collections):
            bpy.data.collections.remove(collection)
        if SCENE_STATE in context.scene:
            del context.scene[SCENE_STATE]
        context.scene.cursor.location = (0.0, 0.0, 0.0)
        context.scene.unit_settings.system = "METRIC"
        context.scene.unit_settings.scale_length = 1.0
        get_state().cursor_placed = False
        get_state().cursor_placing = get_state().cursor_active
        get_state().active_group = "Default Drawing"
        get_state().active_plane = "Ground Plane"
        ensure_scene(context)
        set_tool("select")
        # A new session must not overwrite the previously opened project on Ctrl+S.
        context.scene["drawing_unsaved_project"] = True
        get_state().status = "New drawing"
        persist_scene()
        if not bpy.app.background:
            bpy.ops.ed.undo_push(message="New drawing")
        return {"FINISHED"}


def persist_scene() -> None:
    data: dict[str, object] = asdict(get_state())
    # Personal agent settings stay in local preferences, not in drawings that may be shared.
    key: str
    for key in ("agent_cli", "agent_model", "agent_mode", "agent_effort"):
        data.pop(key, None)
    if not bpy.app.background:
        from ..ui.layout import role

        window: Any
        area: Any
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if role(area) == "viewport" and area.type == "VIEW_3D":
                    view: Any = area.spaces.active.region_3d
                    data["viewport"] = {
                        "location": tuple(view.view_location),
                        "rotation": tuple(view.view_rotation),
                        "distance": float(view.view_distance),
                        "perspective": str(view.view_perspective),
                    }
                    break
    bpy.context.scene[SCENE_STATE] = json.dumps(data)


def restore_view(area: Any) -> None:
    try:
        data: dict[str, Any] = json.loads(str(bpy.context.scene.get(SCENE_STATE, "{}")))
        view_data: dict[str, Any] = data.get("viewport", {})
        location: Any = view_data.get("location")
        rotation: Any = view_data.get("rotation")
        distance: Any = view_data.get("distance")
        if not isinstance(location, list) or len(location) != 3:
            return
        if not isinstance(rotation, list) or len(rotation) != 4:
            return
        number: Any
        for number in [*location, *rotation, distance]:
            if not isinstance(number, (int, float)) or not math.isfinite(number):
                return
        view: Any = area.spaces.active.region_3d
        view.view_location = location
        view.view_rotation = rotation
        view.view_distance = max(0.001, float(distance))
        view.view_perspective = "ORTHO" if view_data.get("perspective") == "ORTHO" else "PERSP"
        if not bpy.app.background:
            view.update()
    except (ValueError, TypeError, AttributeError):
        get_state().status = "Saved view could not be restored."


def restore_scene() -> None:
    name: str
    for name in ("drawing_plane", "on_object", "axes_spokes", "cursor_active", "cursor_placed", "cursor_placing"):
        setattr(get_state(), name, False)
    get_state().active_plane = "Ground Plane"
    get_state().active_group = "Default Drawing"
    get_state().cursor_axis = 1
    get_state().cursor_side = 1
    raw: Any = bpy.context.scene.get(SCENE_STATE, "")
    if not raw:
        return
    try:
        data: dict[str, Any] = json.loads(str(raw))
        for name in ("drawing_plane", "on_object", "axes_spokes", "cursor_active", "cursor_placed"):
            if isinstance(data.get(name), bool):
                setattr(get_state(), name, data[name])
        for name in ("active_plane", "active_group"):
            if isinstance(data.get(name), str):
                setattr(get_state(), name, data[name])
        if data.get("cursor_axis") in (0, 1, 2):
            get_state().cursor_axis = int(data["cursor_axis"])
        if data.get("cursor_side") in (-1, 1):
            get_state().cursor_side = int(data["cursor_side"])
        restore_brushes(data.get("brushes"))
        restore_pencil(data.get("pencil"))
        restore_printer(data.get("printer"))
    except (ValueError, TypeError, AttributeError):
        get_state().status = "Project drawing preferences could not be restored."


def drawing_data() -> set[Any]:
    """Every data-block a native save writes, except the application's editor layout."""
    blocks: set[Any] = set()
    item: Any
    for item in bpy.data.user_map():
        if not (item.is_embedded_data or item.is_runtime_data or isinstance(item, INTERFACE_TYPES)):
            blocks.add(item)
    return blocks


def write_without_interface(path: Path) -> None:
    """Rewrite a saved drawing without windows, workspaces or screens.

    Blender then opens it like a library file and keeps its own layout instead of the
    application's three-editor screen. The native save has already set the file path,
    backup, relative paths and dirty state; this pass replaces only the file's contents.
    """
    with path.open("rb") as handle:
        compress: bool = handle.read(len(ZSTD_MAGIC)) == ZSTD_MAGIC
    bpy.data.libraries.write(str(path), drawing_data(), path_remap="RELATIVE", compress=compress)


@contextmanager
def startup_file() -> Iterator[None]:
    """Saves here write the application's startup file: its editor layout, without drawing state."""
    global _STARTUP_FILE
    previous: bool = _STARTUP_FILE
    _STARTUP_FILE = True
    try:
        yield
    finally:
        _STARTUP_FILE = previous


def saving_startup_file() -> bool:
    return _STARTUP_FILE


@bpy.app.handlers.persistent
def _save_post(filepath: str) -> None:
    """Covers every save path: menus, Ctrl+S, the quit prompt, and scripts."""
    if _STARTUP_FILE or not filepath:
        return
    error: OSError | RuntimeError | ReferenceError | TypeError | ValueError
    try:
        write_without_interface(Path(filepath))
    except (OSError, RuntimeError, ReferenceError, TypeError, ValueError) as error:
        # The native save succeeded; Blender would still open it with the application's layout.
        get_state().status = f"Saved with the app layout included: {error}"
        print(get_state().status)


class DRAWING_OT_save_project(bpy.types.Operator):
    bl_idname: str = "drawing.save_project"
    bl_label: str = "Save Drawing"
    filepath: str
    filter_glob: str

    def invoke(self, context: Any, event: Any) -> set[str]:
        self.filepath = bpy.data.filepath or str(ROOT / "Drawing.blend")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: Any) -> set[str]:
        persist_scene()
        path: Path = Path(self.filepath).with_suffix(".blend")
        context.scene["drawing_unsaved_project"] = False
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        get_state().status = f"Saved {path.name}"
        return {"FINISHED"}


class DRAWING_OT_open_project(bpy.types.Operator):
    bl_idname: str = "drawing.open_project"
    bl_label: str = "Open Drawing"
    filepath: str
    filter_glob: str

    def invoke(self, context: Any, event: Any) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: Any) -> set[str]:
        bpy.ops.wm.open_mainfile(filepath=self.filepath, load_ui=False)
        return {"FINISHED"}


class DRAWING_OT_import_usd(bpy.types.Operator):
    bl_idname: str = "drawing.import_usd"
    bl_label: str = "Import USD"
    bl_options: set[str] = {"REGISTER", "UNDO"}
    filepath: str
    filter_glob: str

    def invoke(self, context: Any, event: Any) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: Any) -> set[str]:
        bpy.ops.wm.usd_import(filepath=self.filepath)
        get_state().status = "USD imported"
        return {"FINISHED"}


class DRAWING_OT_export_usd(bpy.types.Operator):
    bl_idname: str = "drawing.export_usd"
    bl_label: str = "Export USD"
    filepath: str
    filter_glob: str

    def invoke(self, context: Any, event: Any) -> set[str]:
        self.filepath = str(Path(bpy.data.filepath or str(ROOT / "Drawing.blend")).with_suffix(".usdc"))
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: Any) -> set[str]:
        bpy.ops.wm.usd_export(filepath=self.filepath, export_materials=True)
        get_state().status = "USD exported"
        return {"FINISHED"}


CLASSES: tuple[type, ...] = (
    DRAWING_OT_new_project,
    DRAWING_OT_save_project,
    DRAWING_OT_open_project,
    DRAWING_OT_import_usd,
    DRAWING_OT_export_usd,
)


def register() -> None:
    cls: Any
    for cls in CLASSES:
        if cls != DRAWING_OT_new_project:
            cls.__annotations__["filepath"] = bpy.props.StringProperty(subtype="FILE_PATH")
            cls.__annotations__["filter_glob"] = bpy.props.StringProperty(
                default=(
                    "*.blend"
                    if cls in (DRAWING_OT_open_project, DRAWING_OT_save_project)
                    else "*.usd;*.usda;*.usdc;*.usdz"
                ),
                options={"HIDDEN"},
            )
        bpy.utils.register_class(cls)
    # First, so translucent fills are still in their saved state (see viewport/fills.py).
    if _save_post not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.insert(0, _save_post)


def unregister() -> None:
    if _save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_save_post)
    cls: Any
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
