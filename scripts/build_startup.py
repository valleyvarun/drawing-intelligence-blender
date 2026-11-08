"""Write the application's startup screen to app/template/.../startup.blend.

Run from the repository root:
    powershell -File run.ps1 -NoTemplate -ProfileName startup-file -Script scripts/build_startup.py

The engine starts from its factory screen. The application's own layout code builds the
Tool Box | viewport | Clayers editors; the other workspaces and sample data are removed,
and the result is saved as the startup file the launcher opens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from drawing_app.core import controller
from drawing_app.core.blender import bpy
from drawing_app.core.state import ROOT
from drawing_app.ui import layout

TARGET: Path = ROOT / "app/template/startup/bl_app_templates_system/drawing_intelligence/startup.blend"
SCENE_KEYS: tuple[str, ...] = ("drawing_app_state", "drawing_initialized", "drawing_unsaved_project")
_PHASE: int = 0
_WAITED: float = 0.0


def _window() -> Any:
    return bpy.context.window_manager.windows[0]


def _ready() -> bool:
    roles: list[str] = []
    area: Any
    for area in _window().screen.areas:
        roles.append(layout.role(area))
    return controller._ACTIVE is not None and sorted(roles) == ["layers", "toolbox", "viewport"]


def _remove_workspaces() -> None:
    """Keep only the window's workspace; workspace.delete would remove the active one."""
    window: Any = _window()
    others: list[Any] = []
    workspace: Any
    for workspace in bpy.data.workspaces:
        if workspace != window.workspace:
            others.append(workspace)
    bpy.data.batch_remove(ids=others)


def _clean_data() -> None:
    scene: Any = bpy.context.scene
    key: str
    for key in SCENE_KEYS:
        if key in scene:
            del scene[key]
    item: Any
    for item in list(bpy.data.objects):
        bpy.data.objects.remove(item, do_unlink=True)
    for item in list(bpy.data.collections):
        bpy.data.collections.remove(item)
    for item in list(bpy.data.images):
        bpy.data.images.remove(item)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def step() -> float | None:
    global _PHASE, _WAITED
    if _PHASE == 0:
        if not _ready():
            _WAITED += 0.25
            if _WAITED > 40.0:
                print("Startup file: the application layout did not finish.")
                bpy.ops.wm.quit_blender()
                return None
            return 0.25
        controller.stop_session()
        _remove_workspaces()
        _PHASE = 1
        return 0.3
    window: Any = _window()
    roles: list[str] = []
    area: Any
    for area in window.screen.areas:
        roles.append(f"{area.type}:{layout.role(area)}")
    if len(bpy.data.workspaces) != 1 or len(roles) != 3:
        print(f"Startup file: unexpected screen {roles} with {len(bpy.data.workspaces)} workspaces.")
        bpy.ops.wm.quit_blender()
        return None
    window.workspace.name = "Drawing"
    window.screen.name = "Drawing"
    _clean_data()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(TARGET), copy=True, compress=True, relative_remap=False)
    print(f"Startup file written: {TARGET}")
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(step, first_interval=1.0)
