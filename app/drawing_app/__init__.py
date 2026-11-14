"""Drawing Intelligence: a standalone drawing application built on Blender's engine and viewport."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .core.blender import bpy
from .core.desktop import DesktopIdentity, identity
from .core.state import AppState, get_state, load_preferences, save_preferences

_REGISTERED: bool = False
_START_ATTEMPTS: int = 0
_WORKSPACE_STARTED: bool = False
_REVEAL_STEP: int = 0


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


def _application_preferences() -> None:
    """Engine preferences for a dedicated application; nothing is written to a Blender profile."""
    preferences: Any = bpy.context.preferences
    preferences.view.show_splash = False
    preferences.view.use_reduce_motion = True
    preferences.filepaths.file_preview_type = "NONE"
    preferences.filepaths.save_version = 1
    preferences.inputs.use_mouse_emulate_3_button = False
    # Keep the three-editor layout while allowing sidebar dividers to move.
    preferences.apps.show_corner_split = False
    preferences.apps.show_regions_visibility_toggle = False
    preferences.apps.show_edge_resize = True


def _reveal() -> float | None:
    """Let the completed workspace draw, then signal the launcher that the app is ready."""
    global _REVEAL_STEP
    error: RuntimeError | ReferenceError | AttributeError | OSError
    try:
        desktop: DesktopIdentity = identity()
        if _REVEAL_STEP == 0:
            desktop.refresh()
            _REVEAL_STEP = 1
            return 0.1
        desktop.reveal()
    except (RuntimeError, ReferenceError, AttributeError, OSError) as error:
        print(f"Window reveal: {error}")
        identity().reveal()
    _REVEAL_STEP = 2
    return None


def _walk_keymap() -> float | None:
    """Blender fills the user keymaps in its first event loop pass; fly settings follow then."""
    from .viewport import keymap

    if not _REGISTERED or keymap.install_walk():
        return None
    return 0.2


def _reveal_fallback() -> float | None:
    """A startup error must not leave the launcher's splash or a hidden window behind."""
    if _REVEAL_STEP < 2:
        identity().reveal()
    return None


def _start_workspace() -> float | None:
    global _START_ATTEMPTS, _WORKSPACE_STARTED
    from .core import controller
    from .core.project import persist_scene, restore_view
    from .ui import layout, overlay
    from .viewport import configure_viewport, install_guides, keymap

    if not _REGISTERED or bpy.app.background:
        return None
    if bpy.context.window is None:
        _START_ATTEMPTS += 1
        return 0.2 if _START_ATTEMPTS < 50 else None
    window: Any = bpy.context.window
    error: RuntimeError | AttributeError | TypeError
    try:
        layout.configure(bpy.context)
        keymap.install()
        if not keymap.walk_ready() and not bpy.app.timers.is_registered(_walk_keymap):
            bpy.app.timers.register(_walk_keymap, first_interval=0.0)
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
        _application_preferences()
        controller.redraw()
    except (RuntimeError, AttributeError, TypeError) as error:
        print(f"Drawing workspace startup: {error}")
        get_state().status = "Workspace startup: " + str(error)
    if _REVEAL_STEP == 0 and not bpy.app.timers.is_registered(_reveal):
        bpy.app.timers.register(_reveal, first_interval=0.05)
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
    from .core.project import persist_scene, saving_startup_file
    from .tools import extruder, printer
    from .ui import layout

    # A 3D print still forming, or a tube still being laid, is written as it is now.
    printer.flush()
    extruder.flush()
    layout.capture_widths(bpy.context)
    # The startup file opens every new session, so it must not carry one drawing's tool state.
    if not saving_startup_file():
        persist_scene()
    save_preferences()


def _install_agent() -> None:
    """Start the Blender MCP bridge and the Claude Code chat service."""
    from . import agent
    from .ui import layout

    def redraw() -> None:
        layout.redraw()

    agent.install(redraw)


@bpy.app.handlers.persistent
def _exit_pre(_interactive: bool) -> None:
    from . import agent
    from .ui import layout

    # Claude Code and the bridge must not outlive the app.
    agent.uninstall()
    layout.capture_widths(bpy.context)
    window: Any = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    if window is not None and identity().is_visible():
        state: AppState = get_state()
        state.window_maximized = identity().is_maximized()
        # Blender's window coordinates, as the launcher passes them back to --window-geometry.
        # For a maximized window they only choose the monitor.
        state.window_geometry = (int(window.x), int(window.y), int(window.width), int(window.height))
    save_preferences()
    _load_pre(None)
    identity().uninstall()


def _identity_tick() -> float | None:
    """Brand windows the engine opens later, such as file browsers."""
    if not _REGISTERED:
        return None
    identity().refresh()
    return 0.5


@bpy.app.handlers.persistent
def _history_post(_scene: Any) -> None:
    from .core.controller import redraw
    from .core.project import restore_scene
    from .tools import extruder, printer, sketch_mesh
    from .tools.layers import ensure_scene
    from .tools.pipe_fill import cancel
    from .ui import overlay

    cancel()
    # Undo restored the prints' meshes and fields; a print still forming, or a tube being laid, is dropped.
    printer.reset()
    extruder.reset()
    # Undo may have brought a sketch's lines back, or taken them away.
    sketch_mesh.reset()
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
        raise RuntimeError("Drawing Intelligence requires its Blender 5.2 engine or newer.")
    load_preferences()
    if not bpy.app.background:
        # Group the window under the application before it is first shown.
        identity().install()
    _application_preferences()
    # Blender's misleading use_asset_indexing property maps to its no_asset_indexing flag.
    if hasattr(bpy.context.preferences.experimental, "use_asset_indexing"):
        bpy.context.preferences.view.show_developer_ui = True
        bpy.context.preferences.experimental.use_asset_indexing = True
    project.register()
    controller.register()
    _REGISTERED = True
    _install_agent()
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
        # Build the workspace now, so the engine's first frame is already the application.
        retry: float | None = _start_workspace()
        if retry is not None:
            bpy.app.timers.register(_start_workspace, first_interval=retry)
        bpy.app.timers.register(_identity_tick, first_interval=0.5, persistent=True)
        bpy.app.timers.register(_reveal_fallback, first_interval=15.0, persistent=True)


def unregister() -> None:
    global _REGISTERED
    from . import agent
    from .core import controller, project
    from .ui import layout, overlay
    from .viewport import keymap, remove_guides

    if not _REGISTERED:
        return
    _REGISTERED = False
    agent.uninstall()
    layout.capture_widths(bpy.context)
    save_preferences()
    timer: Callable[[], float | None]
    for timer in (_start_workspace, _identity_tick, _reveal_fallback, _reveal, _walk_keymap):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
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
    keymap.uninstall()
    identity().uninstall()
    project.unregister()
