"""Windows > Scripting: Blender's text editor and Python console in a separate window.

The window is a copy of the viewport area (screen.area_dupli), turned into a text editor with a
console below it. Scripts are text datablocks, so they are saved with the drawing. The `api` module
gives scripts the same tools the AI agent uses.
"""

from __future__ import annotations

from typing import Any

from ..core.blender import bpy
from ..core.state import get_state
from . import layout

SCRIPT: str = "drawing_script.py"
EXAMPLE: str = '''"""Drawing Intelligence script. Run it with Alt+P or Text > Run Script.

`api` has the same tools the AI agent uses; help(api.draw_stroke) explains one, api.tools() lists all.
Lengths are metres and Z is up. The console below runs Python one line at a time.
"""

import math

from drawing_app import api

print(api.scene_info()["object_count"], "objects")

# A pipe spiral, 1.2 m wide and 2 m tall.
points = [(math.cos(t / 8) * 0.6, math.sin(t / 8) * 0.6, t * 0.02) for t in range(100)]
api.draw_stroke(points, kind="pipe", radius=0.02, color="#D97757")
'''
# Pointer of the scripting window while it is open.
_WINDOW: int = 0
_PENDING: set[int] = set()
_STEP: int = 0


def _script() -> Any:
    text: Any = bpy.data.texts.get(SCRIPT)
    if text is None:
        text = bpy.data.texts.new(SCRIPT)
        text.from_string(EXAMPLE)
    return text


def _window(pointer: int) -> Any:
    window: Any
    for window in bpy.context.window_manager.windows:
        if int(window.as_pointer()) == pointer:
            return window
    return None


def _show(area: Any, kind: str) -> None:
    area.type = kind
    space: Any = area.spaces.active
    if hasattr(space, "show_region_header"):
        space.show_region_header = True
    if kind == "TEXT_EDITOR":
        text: Any = _script()
        space.text = text
        # Open at the top of the script, with the cursor there too.
        text.cursor_set(0)
        space.top = 0
        space.show_line_numbers = True
        space.show_syntax_highlight = True
        space.show_line_highlight = True
        space.show_word_wrap = False


def _configure() -> float | None:
    """Finish the new window over a few event-loop passes, as Blender initializes its areas."""
    global _WINDOW, _STEP
    error: RuntimeError | ReferenceError | AttributeError | TypeError
    try:
        window: Any = None
        pointer: int = 0
        candidate: Any
        for candidate in bpy.context.window_manager.windows:
            if int(candidate.as_pointer()) not in _PENDING:
                window = candidate
                pointer = int(candidate.as_pointer())
        if window is None:
            _STEP += 1
            return 0.05 if _STEP < 40 else None
        screen: Any = window.screen
        areas: list[Any] = list(screen.areas)
        if _STEP < 100:
            _STEP = 100
            with bpy.context.temp_override(window=window, screen=screen, area=areas[0]):
                _show(areas[0], "TEXT_EDITOR")
                bpy.ops.screen.area_split(direction="HORIZONTAL", factor=0.28)
            return 0.05
        areas.sort(key=_area_height)
        area: Any
        for area in areas:
            with bpy.context.temp_override(window=window, screen=screen, area=area):
                _show(area, "CONSOLE" if area == areas[0] and len(areas) > 1 else "TEXT_EDITOR")
        _WINDOW = pointer
        get_state().status = "Scripting: Alt+P runs the script; the console runs single lines"
        layout.redraw()
    except (RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        get_state().status = f"Scripting window: {error}"
    _PENDING.clear()
    _STEP = 0
    return None


def _area_height(area: Any) -> int:
    return int(area.height)


def open_window(context: Any) -> None:
    """Open the scripting window, or leave the open one as it is."""
    global _STEP
    if _WINDOW and _window(_WINDOW) is not None:
        get_state().status = "The scripting window is already open"
        return
    if bpy.app.timers.is_registered(_configure):
        return
    area: Any = layout.viewport_area(context)
    if area is None:
        return
    _PENDING.clear()
    window: Any
    for window in context.window_manager.windows:
        _PENDING.add(int(window.as_pointer()))
    _STEP = 0
    with context.temp_override(area=area):
        bpy.ops.screen.area_dupli("INVOKE_DEFAULT")
    bpy.app.timers.register(_configure, first_interval=0.05)
