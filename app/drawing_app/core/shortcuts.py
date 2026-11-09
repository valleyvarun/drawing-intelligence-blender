"""Every keyboard shortcut in Drawing Intelligence, in one place.

SHORTCUTS is the live table: the app runs these, and adding a row adds a shortcut. Each row runs one
`controller.dispatch(action, payload)` command, the same commands the menus and Tool Box buttons use
(see COMMANDS below for the common ones). A shortcut works whenever the pointer is over the viewport or
a side panel and no text field or chat box is being typed in.

Adding a shortcut:
    Shortcut("E", "tool", "grease", "Grease Pencil Draw tool"),
    Shortcut("B", "tool", "pipe", "3D Pipe tool", shift=True),

`key` is Blender's event type: letters "A".."Z", digits "ONE".."NINE" and "ZERO", "NUMPAD_1", "F1".."F12",
"DEL", "BACK_SPACE", "SPACE", "TAB", "HOME", "END", "PAGE_UP", "COMMA", "PERIOD", "MINUS", "EQUAL",
"LEFT_BRACKET", and so on. Modifiers must match exactly: Ctrl+S does not run a plain "S" row, and a
"S" row with ctrl=True does not run for Ctrl+Shift+S. The first matching row wins, so it also takes
precedence over a Blender key the app otherwise blocks (BLOCKED below).

Mouse buttons ("LEFTMOUSE", "MIDDLEMOUSE", "RIGHTMOUSE") work too, on press. Give them modifiers or
value="DOUBLE_CLICK", and viewport=True: a plain "LEFTMOUSE" press row would take over every click, and
viewport=True keeps the row to the 3D viewport, where commands such as ("plane", "surface") use the
clicked point.

FIXED lists keys that only apply in one situation (typing, chat, dragging, Blender's own Grease Pencil
and transform keys). They are handled where that situation lives, and are listed here for reference.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    key: str
    action: str
    payload: object
    description: str
    ctrl: bool = False
    shift: bool = False
    alt: bool = False
    # App tools ("select", "move", "rotate", "scale", "paint", "pipe", "printer", "extruder", "grease") it is
    # limited to or skips.
    only: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()
    # Whether holding the key down repeats the command; otherwise repeats are swallowed.
    repeat: bool = True
    # Only while the pointer is over the 3D viewport, not the side panels.
    viewport: bool = False
    # "PRESS", or "DOUBLE_CLICK" for the second press of a double-click (the first press acts as usual).
    value: str = "PRESS"


SHORTCUTS: tuple[Shortcut, ...] = (
    # Add. Shift+A opens the app's Create menu at the pointer instead of Blender's Add menu.
    Shortcut("A", "menu", "create", "Add menu: objects, drawing plane, drawing group", shift=True),
    Shortcut(
        "LEFTMOUSE",
        "plane",
        "surface",
        "Drawing plane on the clicked object, aligned to its surface",
        ctrl=True,
        shift=True,
        viewport=True,
    ),
    # A double-click away from a drawing plane stays an ordinary click. Drawing tools draw on click instead.
    Shortcut(
        "LEFTMOUSE",
        "plane",
        "clicked",
        "Make the double-clicked drawing plane the active one",
        skip=("paint", "pipe", "printer", "extruder", "grease"),
        viewport=True,
        value="DOUBLE_CLICK",
    ),
    # File
    Shortcut("N", "file", "new", "New drawing", ctrl=True),
    Shortcut("O", "file", "open", "Open a drawing", ctrl=True),
    Shortcut("S", "file", "save", "Save (asks for a file name the first time)", ctrl=True),
    Shortcut("S", "file", "save_as", "Save As", ctrl=True, shift=True),
    # Edit
    Shortcut("Z", "edit", "undo", "Undo", ctrl=True),
    Shortcut("Z", "edit", "redo", "Redo", ctrl=True, shift=True),
    Shortcut("D", "edit", "duplicate", "Duplicate the selection", ctrl=True),
    Shortcut("DEL", "edit", "delete", "Delete the selection", skip=("paint", "pipe", "printer", "extruder", "grease")),
    Shortcut("X", "edit", "delete", "Delete the selection", skip=("paint", "pipe", "printer", "extruder", "grease")),
    # Navigation. N replaces Blender's sidebar key, which the app blocks.
    Shortcut("N", "navigation", "next", "Switch right-drag navigation: Orbit, Fly, Walk", repeat=False),
    # View. In Grease Pencil Draw, F is Blender's brush-radius key instead.
    Shortcut("F", "frame", None, "Frame the selection", skip=("grease",)),
    Shortcut("NUMPAD_PERIOD", "frame", None, "Frame the selection"),
    Shortcut("ONE", "view_axis", "X", "View along X; again for the other side", repeat=False),
    Shortcut("TWO", "view_axis", "Y", "View along Y; again for the other side", repeat=False),
    Shortcut("THREE", "view_axis", "Z", "View along Z (top); again for the other side", repeat=False),
    Shortcut("ZERO", "view_axis", "PERSPECTIVE", "Perspective view", repeat=False),
    Shortcut("NUMPAD_1", "view_axis", "X", "View along X; again for the other side", repeat=False),
    Shortcut("NUMPAD_2", "view_axis", "Y", "View along Y; again for the other side", repeat=False),
    Shortcut("NUMPAD_3", "view_axis", "Z", "View along Z (top); again for the other side", repeat=False),
    Shortcut("NUMPAD_0", "view_axis", "PERSPECTIVE", "Perspective view", repeat=False),
)

# Commands a shortcut can run: (action, payload, what it does). Others are in controller.dispatch.
COMMANDS: tuple[tuple[str, str, str], ...] = (
    (
        "tool",
        '"select", "move", "rotate", "scale", "paint", "pipe", "printer", "extruder", "grease"',
        "Choose an app tool",
    ),
    (
        "printer",
        '("rate", particles/s) / ("per_drop", 1-500) / ("size", metres) / ("drop", metres) / ("sticky", 0-1)',
        "3D Printer flow / particles per drop / particle size / drop height / stickiness",
    ),
    ("extruder", '("flow", metres/s) / ("drop", metres)', "3D Extruder flow / drop height"),
    ("pencil", '("tool", "builtin.brush")', "Grease Pencil tool: builtin.brush (Draw), builtin_brush.Fill, ..."),
    ("pencil", '("tool", "builtin.box") / ("tool", "builtin.circle")', "Shape tool: Rectangle / Ellipse"),
    ("pencil", '("brush", "Ink Pen")', "Grease Pencil brush, by its Essentials name"),
    ("file", '"new", "open", "save", "save_as", "import_usd", "export_usd", "quit"', "File menu commands"),
    ("edit", '"undo", "redo", "select_all", "deselect_all", "duplicate", "delete"', "Edit menu commands"),
    ("menu", '"file", "edit", "view", "create", "rendering", "windows"', "Open that menu at the pointer"),
    ("create", '"cube", "sphere", "plane", "cylinder"', "Add an object at the 3D cursor (or the origin)"),
    ("plane", '"create" / "surface"', "New drawing plane / one on the clicked surface (viewport=True rows)"),
    ("plane", '"clicked"', "Activate the drawing plane under the pointer; elsewhere the click goes on"),
    ("frame", "None", "Frame the selection"),
    ("view_axis", '"X", "Y", "Z", "PERSPECTIVE"', "Axis view (repeat flips side) or perspective"),
    ("option", '("drawing_plane" | "on_object" | "ruler" | ..., True/False)', "Turn a Canvas or view option on/off"),
    ("navigation", '"orbit", "fly", "walk", "next"', "Right-drag navigation mode; next cycles them"),
    ("walk", '("walk_speed", metres/s) / ("eye_level", metres)', "Walk speed / eye height"),
    ("theme", '"light", "dark"', "Light or dark mode"),
    ("units", '"m", "mm"', "Display units"),
    ("layout_reset", "None", "View > Reset Workspace"),
)

# Keys that only apply in one situation; handled where noted. Reference only.
FIXED: tuple[tuple[str, str], ...] = (
    ("Esc", "Return to the Select tool; cancel 3D cursor placement, a drag, Trim, or field editing"),
    ("Ctrl+Z while drawing", "Cancel the stroke being drawn (tools/drawing.py via the controller)"),
    ("Left click / drag", "Select, or draw with the active tool; Shift adds to the selection"),
    ("Right drag", "Orbit, Fly, or Walk, as chosen with N or the navigation menu (viewport/keymap.py)"),
    ("Middle drag", "Pan (viewport/keymap.py)"),
    ("Wheel", "Zoom toward the pointer"),
    ("Shift+Wheel", "Change zoom speed, saved for the next launch (viewport/navigation.py)"),
    ("W A S D Q E while flying", "Fly; Shift speeds up, Ctrl slows down (Blender's walk navigation)"),
    ("W A S D / arrows while walking", "Walk at eye level; Shift runs, Ctrl slows, Esc returns (viewport/walk.py)"),
    ("G / R / S", "Blender's move / rotate / scale for selected strokes"),
    ("F in Grease Pencil Draw", "Blender's brush-radius control"),
    ("F2 or double-click", "Rename the Clayers item under the pointer (ui/overlay.py)"),
    ("Enter / Shift+Enter in chat", "Send the message / new line (ui/overlay.py)"),
    ("Enter, Tab / Esc in a field", "Confirm / cancel typing (ui/overlay.py)"),
    ("Ctrl+A / C / V in a field", "Select all / copy / paste text (ui/overlay.py)"),
)

# Blender keys the app swallows because they would open the engine's editors, pies, or menus.
# A SHORTCUTS row with the same key and modifiers runs instead (Shift+A opens the app's menu).
BLOCKED_PLAIN: frozenset[str] = frozenset(("TAB", "N", "T", "SPACE", "ACCENT_GRAVE", "Q", "M", "Z", "W", "I", "K"))
BLOCKED_SHIFT: frozenset[str] = frozenset(("A", "S", "SPACE", "TAB", "ACCENT_GRAVE"))
BLOCKED_CTRL: frozenset[str] = frozenset(("SPACE", "TAB", "COMMA", "PAGE_UP", "PAGE_DOWN", "L", "P", "A"))
# Frame changes and playback; the drawing workspace has no timeline.
BLOCKED_ALWAYS: frozenset[str] = frozenset(
    ("LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "DOWN_ARROW", "MEDIA_PLAY", "MEDIA_STOP", "MEDIA_FIRST", "MEDIA_LAST")
)


def shortcut_for(
    key: str, ctrl: bool, shift: bool, alt: bool, tool: str, over_viewport: bool, value: str = "PRESS"
) -> Shortcut | None:
    """The first shortcut for a press with exactly these modifiers, the given app tool, and pointer place."""
    shortcut: Shortcut
    for shortcut in SHORTCUTS:
        if (shortcut.key, shortcut.ctrl, shortcut.shift, shortcut.alt, shortcut.value) != (
            key,
            ctrl,
            shift,
            alt,
            value,
        ):
            continue
        if (shortcut.only and tool not in shortcut.only) or tool in shortcut.skip:
            continue
        if shortcut.viewport and not over_viewport:
            continue
        return shortcut
    return None


def is_blocked(key: str, ctrl: bool, shift: bool, alt: bool) -> bool:
    """Whether the app swallows this Blender key (see BLOCKED_* above)."""
    if key in BLOCKED_ALWAYS:
        return True
    if alt:
        return key in ("WHEELUPMOUSE", "WHEELDOWNMOUSE")
    if key.startswith("F") and key[1:].isdigit():
        # F2 renames in Clayers; other function keys open Blender's editors.
        return key != "F2" or ctrl or shift
    if ctrl:
        return key in BLOCKED_CTRL
    if shift:
        return key in BLOCKED_SHIFT
    return key in BLOCKED_PLAIN
