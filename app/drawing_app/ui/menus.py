"""Application menus replacing Blender's default workspace strip."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..core.blender import bpy

_DISPATCH: Callable[[str, object], None] | None = None
_NATIVE: Any = None
_REGISTERED: list[type] = []


def _item(layout: Any, label: str, action: str, payload: object = None) -> None:
    operator: Any = layout.operator("drawing.ui_action", text=label)
    operator.action = action
    operator.payload = json.dumps(payload)


class DRAWING_OT_ui_action(bpy.types.Operator):
    bl_idname: str = "drawing.ui_action"
    bl_label: str = "Drawing Action"
    action: str
    payload: str

    def execute(self, context: Any) -> set[str]:
        if _DISPATCH is not None:
            _DISPATCH(self.action, json.loads(self.payload))
        return {"FINISHED"}


class DRAWING_OT_rename_item(bpy.types.Operator):
    bl_idname: str = "drawing.rename_item"
    bl_label: str = "Rename"
    target_kind: str
    target_key: str
    new_name: str

    def invoke(self, context: Any, event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context: Any) -> None:
        row: Any = self.layout.row()
        row.activate_init = True
        row.prop(self, "new_name", text="Name")

    def execute(self, context: Any) -> set[str]:
        if _DISPATCH is not None and self.new_name.strip():
            _DISPATCH("rename", (self.target_kind, self.target_key, self.new_name.strip()))
        return {"FINISHED"}


def rename(kind: str, key: str, name: str) -> None:
    bpy.ops.drawing.rename_item("INVOKE_DEFAULT", target_kind=kind, target_key=key, new_name=name)


class DRAWING_MT_file(bpy.types.Menu):
    bl_label: str = "File"
    bl_idname: str = "DRAWING_MT_file"

    def draw(self, context: Any) -> None:
        layout: Any = self.layout
        _item(layout, "New Drawing", "file", "new")
        _item(layout, "Open...", "file", "open")
        layout.separator()
        _item(layout, "Save", "file", "save")
        _item(layout, "Save As...", "file", "save_as")
        layout.separator()
        _item(layout, "Import USD...", "file", "import_usd")
        _item(layout, "Export USD...", "file", "export_usd")
        layout.separator()
        _item(layout, "Quit", "file", "quit")


class DRAWING_MT_edit(bpy.types.Menu):
    bl_label: str = "Edit"
    bl_idname: str = "DRAWING_MT_edit"

    def draw(self, context: Any) -> None:
        layout: Any = self.layout
        _item(layout, "Undo", "edit", "undo")
        _item(layout, "Redo", "edit", "redo")
        layout.separator()
        _item(layout, "Select All", "edit", "select_all")
        _item(layout, "Duplicate", "edit", "duplicate")
        _item(layout, "Delete", "edit", "delete")


class DRAWING_MT_view(bpy.types.Menu):
    bl_label: str = "View"
    bl_idname: str = "DRAWING_MT_view"

    def draw(self, context: Any) -> None:
        layout: Any = self.layout
        _item(layout, "Frame Selected", "frame")
        _item(layout, "Perspective", "view_axis", "PERSPECTIVE")
        axis: str
        for axis in ("X", "Y", "Z"):
            _item(layout, f"View {axis} Axis", "view_axis", axis)
        layout.separator()
        _item(layout, "Reset Workspace", "layout_reset")


class DRAWING_MT_create(bpy.types.Menu):
    bl_label: str = "Create"
    bl_idname: str = "DRAWING_MT_create"

    def draw(self, context: Any) -> None:
        kind: str
        for kind in ("cube", "sphere", "plane", "cylinder"):
            _item(self.layout, kind.title(), "create", kind)
        self.layout.separator()
        _item(self.layout, "Drawing Plane", "plane", "create")
        _item(self.layout, "Drawing Group", "layer", ("add", ""))


class DRAWING_MT_rendering(bpy.types.Menu):
    bl_label: str = "Rendering"
    bl_idname: str = "DRAWING_MT_rendering"

    def draw(self, context: Any) -> None:
        _item(self.layout, "Light mode", "theme", "light")
        _item(self.layout, "Dark mode", "theme", "dark")


class DRAWING_HT_topbar(bpy.types.Header):
    bl_space_type: str = "TOPBAR"

    def draw(self, context: Any) -> None:
        if context.region.alignment == "RIGHT":
            return
        row: Any = self.layout.row(align=True)
        row.scale_x = 1.05
        menu: type
        for menu in (DRAWING_MT_file, DRAWING_MT_edit, DRAWING_MT_view, DRAWING_MT_create, DRAWING_MT_rendering):
            row.menu(menu.bl_idname)


_CLASSES: tuple[type, ...] = (
    DRAWING_OT_ui_action,
    DRAWING_OT_rename_item,
    DRAWING_MT_file,
    DRAWING_MT_edit,
    DRAWING_MT_view,
    DRAWING_MT_create,
    DRAWING_MT_rendering,
    DRAWING_HT_topbar,
)


def install(dispatch: Callable[[str, object], None]) -> None:
    global _DISPATCH, _NATIVE
    _DISPATCH = dispatch
    if _REGISTERED:
        return
    _NATIVE = getattr(bpy.types, "TOPBAR_HT_upper_bar", None)
    if _NATIVE is not None:
        bpy.utils.unregister_class(_NATIVE)
    DRAWING_OT_ui_action.__annotations__["action"] = bpy.props.StringProperty()
    DRAWING_OT_ui_action.__annotations__["payload"] = bpy.props.StringProperty(default="null")
    DRAWING_OT_rename_item.__annotations__["target_kind"] = bpy.props.StringProperty(options={"HIDDEN"})
    DRAWING_OT_rename_item.__annotations__["target_key"] = bpy.props.StringProperty(options={"HIDDEN"})
    DRAWING_OT_rename_item.__annotations__["new_name"] = bpy.props.StringProperty(name="Name")
    cls: type
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
        _REGISTERED.append(cls)


def uninstall() -> None:
    global _DISPATCH, _NATIVE
    cls: type
    for cls in reversed(_REGISTERED):
        bpy.utils.unregister_class(cls)
    _REGISTERED.clear()
    if _NATIVE is not None:
        bpy.utils.register_class(_NATIVE)
    _NATIVE = None
    _DISPATCH = None
