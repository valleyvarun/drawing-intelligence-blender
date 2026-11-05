"""3D Drawing: a focused application running in Blender's native window and viewport."""

from __future__ import annotations

from typing import Any

from .core.blender import bpy
from .core.state import get_state, load_preferences, save_preferences

_REGISTERED: bool = False
_START_ATTEMPTS: int = 0
_WORKSPACE_STARTED: bool = False


def _prepare_scene() -> None:
    from .tools.layers import ensure_scene

    scene: Any = bpy.context.scene
    if not bpy.data.filepath and not scene.get("drawing_initialized", False):
        obj: Any
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        scene["drawing_initialized"] = True
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0
    scene.render.engine = "BLENDER_WORKBENCH"
    ensure_scene()


def _start_workspace() -> float | None:
    global _START_ATTEMPTS, _WORKSPACE_STARTED
    from .core import controller
    from .core.project import persist_scene, restore_view
    from .ui import layout, overlay
    from .viewport import configure_viewport, install_guides

    if not _REGISTERED or bpy.app.background:
        return None
    if bpy.context.window is None:
        _START_ATTEMPTS += 1
        return 0.2 if _START_ATTEMPTS < 50 else None
    window: Any = bpy.context.window
    error: RuntimeError | AttributeError | TypeError
    try:
        layout.configure(bpy.context)
        with bpy.context.temp_override(window=window, screen=window.screen):
            area: Any = layout.viewport_area(bpy.context)
            if area is None:
                _START_ATTEMPTS += 1
                return 0.2 if _START_ATTEMPTS < 50 else None
            with bpy.context.temp_override(area=area):
                configure_viewport(area)
                restore_view(area)
                overlay.install(controller.dispatch)
                install_guides()
                _WORKSPACE_STARTED = True
            region: Any
            for region in area.regions:
                if region.type == "WINDOW":
                    with bpy.context.temp_override(area=area, region=region):
                        controller.set_tool(get_state().tool)
                        bpy.ops.drawing.session("INVOKE_DEFAULT")
                        persist_scene()
                        bpy.ops.ed.undo_push(message="Start drawing")
                    break
        _START_ATTEMPTS = 0
        bpy.context.preferences.view.show_splash = False
        bpy.context.preferences.filepaths.file_preview_type = "NONE"
        bpy.context.preferences.filepaths.save_version = 1
        bpy.context.preferences.inputs.use_mouse_emulate_3_button = False
        controller.redraw()
    except (RuntimeError, AttributeError, TypeError) as error:
        print(f"Drawing workspace startup: {error}")
        get_state().status = "Workspace startup: " + str(error)
    return None


@bpy.app.handlers.persistent
def _load_pre(_unused: Any) -> None:
    global _WORKSPACE_STARTED
    from .core.controller import stop_session
    from .ui import overlay
    from .viewport import remove_guides

    stop_session()
    if _WORKSPACE_STARTED:
        overlay.uninstall()
        remove_guides()
        _WORKSPACE_STARTED = False


def restart_workspace() -> None:
    global _START_ATTEMPTS
    from .core.project import persist_scene
    from .ui import layout

    _START_ATTEMPTS = 0
    persist_scene()
    _load_pre(None)
    layout.reset(bpy.context)
    if not bpy.app.timers.is_registered(_start_workspace):
        bpy.app.timers.register(_start_workspace, first_interval=0.2)


@bpy.app.handlers.persistent
def _load_post(_unused: Any) -> None:
    global _START_ATTEMPTS
    from .core.controller import stop_session
    from .core.project import restore_scene

    stop_session()
    _START_ATTEMPTS = 0
    _prepare_scene()
    restore_scene()
    if not bpy.app.background and not bpy.app.timers.is_registered(_start_workspace):
        bpy.app.timers.register(_start_workspace, first_interval=0.2)


@bpy.app.handlers.persistent
def _save_pre(_unused: Any) -> None:
    from .core.project import persist_scene

    persist_scene()
    save_preferences()


@bpy.app.handlers.persistent
def _exit_pre(_interactive: bool) -> None:
    save_preferences()
    _load_pre(None)


@bpy.app.handlers.persistent
def _history_post(_scene: Any) -> None:
    from .core.controller import redraw
    from .core.project import restore_scene
    from .tools.layers import ensure_scene
    from .ui import overlay

    restore_scene()
    ensure_scene()
    overlay.invalidate_images()
    redraw()


def register() -> None:
    global _REGISTERED
    from .core import controller, project

    if _REGISTERED:
        return
    if bpy.app.version < (5, 2, 0):
        raise RuntimeError("3D Drawing requires Blender 5.2 or newer.")
    load_preferences()
    bpy.context.preferences.view.show_splash = False
    bpy.context.preferences.filepaths.file_preview_type = "NONE"
    # Blender's asset index uses the Windows profile even in portable sessions.
    # This drawing workspace has no asset browser, so disable that disk index.
    if hasattr(bpy.context.preferences.experimental, "use_asset_indexing"):
        bpy.context.preferences.view.show_developer_ui = True
        bpy.context.preferences.experimental.use_asset_indexing = True
    project.register()
    controller.register()
    _REGISTERED = True
    _prepare_scene()
    project.restore_scene()
    bpy.app.handlers.load_post.append(_load_post)
    bpy.app.handlers.load_pre.append(_load_pre)
    bpy.app.handlers.save_pre.append(_save_pre)
    bpy.app.handlers.undo_post.append(_history_post)
    bpy.app.handlers.redo_post.append(_history_post)
    if hasattr(bpy.app.handlers, "exit_pre"):
        bpy.app.handlers.exit_pre.append(_exit_pre)
    if not bpy.app.background:
        bpy.app.timers.register(_start_workspace, first_interval=0.2)


def unregister() -> None:
    global _REGISTERED
    from .core import controller, project
    from .ui import overlay
    from .viewport import remove_guides

    if not _REGISTERED:
        return
    _REGISTERED = False
    save_preferences()
    if bpy.app.timers.is_registered(_start_workspace):
        bpy.app.timers.unregister(_start_workspace)
    bpy.app.handlers.load_post.remove(_load_post)
    bpy.app.handlers.load_pre.remove(_load_pre)
    bpy.app.handlers.save_pre.remove(_save_pre)
    bpy.app.handlers.undo_post.remove(_history_post)
    bpy.app.handlers.redo_post.remove(_history_post)
    if hasattr(bpy.app.handlers, "exit_pre"):
        bpy.app.handlers.exit_pre.remove(_exit_pre)
    controller.unregister()
    overlay.uninstall()
    remove_guides()
    project.unregister()
