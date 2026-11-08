"""The application's mouse scheme on Blender's native navigation operators.

Right drag orbits (view3d.rotate) or flies (view3d.walk, held like a game camera), middle
drag pans (view3d.move), and the wheel zooms toward the pointer (view3d.zoom). The bindings
live in the add-on keyconfig, which Blender merges ahead of its own items, so they also win
over the right-click context menus of Object and Grease Pencil Draw mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.blender import bpy
from ..core.state import get_state


@dataclass(frozen=True)
class Binding:
    keymap: str
    space: str
    idname: str
    event: str
    navigation: str
    shift: bool = False
    value: str = "PRESS"


# The 3D View keymap comes after the mode keymaps, so each mode that has its own
# right-click item gets the navigation binding as well.
_map: str
_shift: bool
_value: str
BINDINGS: tuple[Binding, ...] = tuple(
    Binding(_map, "VIEW_3D", "drawing.select", "LEFTMOUSE", "", _shift, _value)
    for _map in ("3D View", "3D View Tool: Tweak", "3D View Tool: Tweak (fallback)")
    for _shift in (False, True)
    for _value in ("PRESS", "CLICK")
) + (
    Binding("3D View", "VIEW_3D", "view3d.rotate", "RIGHTMOUSE", "orbit"),
    Binding("3D View", "VIEW_3D", "view3d.walk", "RIGHTMOUSE", "fly"),
    Binding("3D View", "VIEW_3D", "view3d.move", "MIDDLEMOUSE", ""),
    Binding("Object Mode", "EMPTY", "view3d.rotate", "RIGHTMOUSE", "orbit"),
    Binding("Object Mode", "EMPTY", "view3d.walk", "RIGHTMOUSE", "fly"),
    Binding("Grease Pencil Draw Mode", "EMPTY", "view3d.rotate", "RIGHTMOUSE", "orbit"),
    Binding("Grease Pencil Draw Mode", "EMPTY", "view3d.walk", "RIGHTMOUSE", "fly"),
    Binding("Grease Pencil Edit Mode", "EMPTY", "view3d.rotate", "RIGHTMOUSE", "orbit"),
    Binding("Grease Pencil Edit Mode", "EMPTY", "view3d.walk", "RIGHTMOUSE", "fly"),
)
# Fly is Blender's walk navigation while the right button is held; Ctrl slows it.
WALK_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("CONFIRM", "RIGHTMOUSE", "RELEASE"),
    ("SLOW_ENABLE", "LEFT_CTRL", "PRESS"),
    ("SLOW_DISABLE", "LEFT_CTRL", "RELEASE"),
    ("SLOW_ENABLE", "RIGHT_CTRL", "PRESS"),
    ("SLOW_DISABLE", "RIGHT_CTRL", "RELEASE"),
)
_ADDON: list[tuple[Any, Any, Binding]] = []
_WALK_ADDED: list[int] = []
_WALK_CHANGED: list[tuple[int, bool]] = []
_INSTALLED: bool = False
_WALK_READY: bool = False


def install() -> None:
    """Add the navigation bindings once per session; safe before Blender fills its keymaps."""
    global _INSTALLED
    if _INSTALLED:
        update()
        return
    addon: Any = bpy.context.window_manager.keyconfigs.addon
    if addon is None:
        return
    binding: Binding
    for binding in BINDINGS:
        keymap: Any = addon.keymaps.new(name=binding.keymap, space_type=binding.space, region_type="WINDOW")
        entry: Any = keymap.keymap_items.new(binding.idname, binding.event, binding.value, shift=binding.shift)
        _ADDON.append((keymap, entry, binding))
    preferences: Any = bpy.context.preferences.inputs
    preferences.use_zoom_to_mouse = True
    preferences.view_rotate_method = "TURNTABLE"
    preferences.use_rotate_around_active = False
    preferences.use_mouse_depth_navigate = True
    preferences.walk_navigation.use_gravity = False
    preferences.walk_navigation.walk_speed_factor = 4.0
    _INSTALLED = True
    update()


def install_walk() -> bool:
    """Hold-to-fly in Blender's walk keymap. Returns False until Blender has built the user keymaps."""
    global _WALK_READY
    if _WALK_READY:
        return True
    keyconfig: Any = bpy.context.window_manager.keyconfigs.user
    walk: Any = keyconfig.keymaps.get("View3D Walk Modal") if keyconfig is not None else None
    if walk is None or not len(walk.keymap_items):
        return False
    item: Any
    for item in walk.keymap_items:
        # Releasing the right button keeps the new view instead of cancelling the fly.
        if item.propvalue == "CANCEL" and item.type == "RIGHTMOUSE":
            _WALK_CHANGED.append((int(item.id), bool(item.active)))
            item.active = False
    value: str
    event: str
    state: str
    for value, event, state in WALK_ITEMS:
        item = walk.keymap_items.new_modal(value, event, state, any=True)
        _WALK_ADDED.append(int(item.id))
    _WALK_READY = True
    return True


def update() -> None:
    """Follow the Orbit/Fly choice from the viewport menu."""
    navigation: str = get_state().navigation
    _keymap: Any
    entry: Any
    binding: Binding
    for _keymap, entry, binding in _ADDON:
        enabled: bool = not binding.navigation or binding.navigation == navigation
        if entry.active != enabled:
            entry.active = enabled


def set_walk_speed(view_distance: float) -> None:
    """Scale fly speed with the view, as drawings range from millimetres to kilometres."""
    walk: Any = bpy.context.preferences.inputs.walk_navigation
    speed: float = max(0.01, min(1000.0, view_distance * 0.4))
    if abs(float(walk.walk_speed) - speed) > speed * 0.01:
        walk.walk_speed = speed


def uninstall() -> None:
    """Remove our bindings and restore the native modal map before another registration."""
    global _INSTALLED, _WALK_READY
    keymap: Any
    entry: Any
    identifier: int
    keyconfig: Any = bpy.context.window_manager.keyconfigs.user
    walk: Any = keyconfig.keymaps.get("View3D Walk Modal") if keyconfig is not None else None
    if walk is not None:
        # Blender rebuilds merged keymaps; cached RNA entries can refer to the old copy.
        for identifier in _WALK_ADDED:
            entry = walk.keymap_items.from_id(identifier)
            if entry is not None:
                walk.keymap_items.remove(entry)
        enabled: bool
        for identifier, enabled in _WALK_CHANGED:
            entry = walk.keymap_items.from_id(identifier)
            if entry is not None:
                entry.active = enabled
    _WALK_ADDED.clear()
    _WALK_CHANGED.clear()
    _binding: Binding
    for keymap, entry, _binding in _ADDON:
        try:
            keymap.keymap_items.remove(entry)
        except (ReferenceError, RuntimeError):
            pass
    _ADDON.clear()
    _WALK_READY = False
    _INSTALLED = False


def active_bindings() -> list[str]:
    """Bindings currently in effect, for diagnostics and tests."""
    result: list[str] = []
    _keymap: Any
    entry: Any
    binding: Binding
    for _keymap, entry, binding in _ADDON:
        if entry.active:
            result.append(f"{binding.keymap}:{binding.event}:{binding.idname}")
    return result


def walk_ready() -> bool:
    return _WALK_READY
