"""Drawing on the 3D cursor's planes in the X, Y, and Z views, in the real window.

The cursor is placed, then each axis view is chosen with its shortcut (1, 2, 3), flipped, left for perspective,
and chosen again from the orientation gizmo. Every pipe stroke must lie in the plane through the cursor that the
view shows flat, and Grease Pencil must be given the same plane. Requires --enable-event-simulate.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from drawing_app.core import controller
from drawing_app.core.blender import Vector, bpy
from drawing_app.core.state import ROOT, get_state
from drawing_app.tools import pipe_edit
from drawing_app.ui import layout, overlay
from drawing_app.viewport.navigation import AxisHandle, axis_handles, gizmo_hit

CHECKS: Path = ROOT / ".checks"
REPORT: dict[str, object] = {}
STEPS: list[Callable[[], None]] = []
INDEX: int = 0
CURSOR: tuple[float, float, float] = (1.0, 2.0, 0.5)


def window() -> Any:
    return bpy.context.window_manager.windows[0]


def event(kind: str, value: str = "PRESS", x: int = 0, y: int = 0, **modifiers: bool | str) -> None:
    window().event_simulate(type=kind, value=value, x=x, y=y, **modifiers)


def viewport() -> tuple[Any, Any]:
    area: Any = layout.viewport_area(bpy.context)
    assert area is not None
    return area, next(item for item in area.regions if item.type == "WINDOW")


def middle() -> tuple[int, int]:
    region: Any = viewport()[1]
    return region.x + region.width // 2, region.y + region.height // 2


def key(kind: str) -> None:
    """A key press with the pointer over the middle of the viewport."""
    spot: tuple[int, int] = middle()
    event("MOUSEMOVE", "NOTHING", *spot)
    event(kind, "PRESS", *spot)
    event(kind, "RELEASE", *spot)


def draw() -> None:
    """One stroke across the middle of the viewport, wherever that is in the scene."""
    x: int
    y: int
    x, y = middle()
    event("MOUSEMOVE", "NOTHING", x - 90, y - 50)
    event("LEFTMOUSE", "PRESS", x - 90, y - 50)
    event("MOUSEMOVE", "NOTHING", x, y + 40)
    event("MOUSEMOVE", "NOTHING", x + 90, y - 30)
    event("LEFTMOUSE", "RELEASE", x + 90, y - 30)


def pipes() -> list[Any]:
    return [item for item in bpy.context.scene.objects if item.get("drawing_kind") == "pipe"]


def click(key_name: str) -> None:
    area: Any
    for area in window().screen.areas:
        widget: overlay.Widget
        for widget in overlay._WIDGETS.get(int(area.as_pointer()), []):
            if widget.key == key_name:
                region: Any = next(item for item in area.regions if item.type == "WINDOW")
                scale: float = max(1.0, float(bpy.context.preferences.system.ui_scale))
                x: int = round(region.x + (widget.bounds[0] + widget.bounds[2] / 2) * scale)
                y: int = round(region.y + (widget.bounds[1] + widget.bounds[3] / 2) * scale)
                event("MOUSEMOVE", "NOTHING", x, y)
                event("LEFTMOUSE", "PRESS", x, y)
                event("LEFTMOUSE", "RELEASE", x, y)
                return
    raise AssertionError("UI widget not found: " + key_name)


def on_plane(axis: int, expected: int) -> None:
    """The newest of `expected` pipes lies flat in the cursor's plane across `axis`, and spans the other two."""
    drawn: list[Any] = pipes()
    assert len(drawn) == expected, (len(drawn), expected, get_state().status)
    positions: list[tuple[float, float, float]] = [sample.position for sample in pipe_edit.samples(drawn[-1])]
    assert len(positions) > 5, len(positions)
    other: int
    for other in range(3):
        values: list[float] = [position[other] for position in positions]
        if other == axis:
            assert max(abs(value - CURSOR[axis]) for value in values) < 1e-4, (axis, min(values), max(values))
        else:
            assert max(values) - min(values) > 0.05, (axis, other, min(values), max(values))
    assert get_state().cursor_axis == axis


def looking_along(axis: int) -> int:
    """Check the view is flat along `axis`; returns which side it looks from."""
    view: Any = viewport()[0].spaces.active.region_3d
    backward: Any = view.view_rotation @ Vector((0.0, 0.0, 1.0))
    assert view.view_perspective == "ORTHO" and abs(abs(float(backward[axis])) - 1.0) < 1e-5, tuple(backward)
    return 1 if float(backward[axis]) > 0.0 else -1


def setup() -> None:
    assert controller._ACTIVE is not None
    controller.dispatch("tool", "select")
    state: Any = get_state()
    state.on_object = state.drawing_plane = state.axes_spokes = False
    state.brushes["pipe"].radius = 0.03
    state.brushes["pipe"].shape = "circle"
    state.cursor_active = state.cursor_placed = state.cursor_placing = False


def enable_cursor() -> None:
    # The Tool Box's "Place cursor" command (its button can be below the fold in a small window), then a click.
    controller.dispatch("cursor_place")


def place_cursor() -> None:
    assert get_state().cursor_active and get_state().cursor_placing, (
        get_state().cursor_active,
        get_state().cursor_placing,
        get_state().cursor_placed,
        get_state().status,
    )
    spot: tuple[int, int] = middle()
    event("MOUSEMOVE", "NOTHING", *spot)
    event("LEFTMOUSE", "PRESS", *spot)
    event("LEFTMOUSE", "RELEASE", *spot)


def move_cursor() -> None:
    state: Any = get_state()
    assert state.cursor_placed and not state.cursor_placing
    axis: int
    for axis in range(3):
        controller.dispatch("cursor", (axis, CURSOR[axis]))
    assert tuple(round(value, 6) for value in bpy.context.scene.cursor.location) == CURSOR
    # The plane it starts with faces Y, as before.
    assert state.cursor_axis == 1
    click("tool_pipe")


def view_x() -> None:
    assert get_state().tool == "pipe"
    key("ONE")


def view_y() -> None:
    key("TWO")


def view_z() -> None:
    key("THREE")


def perspective() -> None:
    key("ZERO")


def drawn_in_x() -> None:
    looking_along(0)
    on_plane(0, 1)
    REPORT["x_view_draws_on_the_cursors_x_plane"] = True


def drawn_in_y() -> None:
    looking_along(1)
    on_plane(1, 2)
    REPORT["y_view_draws_on_the_cursors_y_plane"] = True


def drawn_in_z() -> None:
    looking_along(2)
    on_plane(2, 3)
    bpy.ops.screen.screenshot(filepath=str(CHECKS / "cursor-views.png"))
    REPORT["z_view_draws_on_the_cursors_z_plane"] = True


def flip_z() -> None:
    # The same key again looks from the other side; the plane is the same one.
    assert looking_along(2) == 1
    key("THREE")


def drawn_from_below() -> None:
    assert looking_along(2) == -1
    on_plane(2, 4)
    REPORT["the_other_side_draws_on_the_same_plane"] = True


def drawn_in_perspective() -> None:
    # Perspective keeps the plane of the last axis view.
    assert viewport()[0].spaces.active.region_3d.view_perspective == "PERSP"
    on_plane(2, 5)
    REPORT["perspective_keeps_the_last_plane"] = True


def click_gizmo_x() -> None:
    area: Any
    region: Any
    area, region = viewport()
    view: Any = area.spaces.active.region_3d
    handle: AxisHandle
    for handle in axis_handles(view):
        if handle.axis == 0 and gizmo_hit(view, (handle.x, handle.y)) == handle:
            x: int = round(region.x + handle.x)
            y: int = round(region.y + handle.y)
            event("MOUSEMOVE", "NOTHING", x, y)
            event("LEFTMOUSE", "PRESS", x, y)
            event("LEFTMOUSE", "RELEASE", x, y)
            return
    raise AssertionError("No X handle of the orientation gizmo can be clicked")


def drawn_after_gizmo() -> None:
    # The gizmo turned the view to X, and the cursor's plane with it.
    view: Any = viewport()[0].spaces.active.region_3d
    backward: Any = view.view_rotation @ Vector((0.0, 0.0, 1.0))
    assert abs(abs(float(backward[0])) - 1.0) < 1e-5, tuple(backward)
    on_plane(0, 6)
    REPORT["gizmo_view_draws_on_its_plane"] = True
    controller.dispatch("tool", "grease")


def grease_y() -> None:
    assert get_state().tool == "grease"
    key("TWO")


def grease_follows() -> None:
    settings: Any = bpy.context.scene.tool_settings
    assert get_state().cursor_axis == 1
    assert (settings.gpencil_stroke_placement_view3d, settings.gpencil_sculpt.lock_axis) == ("CURSOR", "AXIS_Y")
    key("THREE")


def grease_follows_again() -> None:
    assert bpy.context.scene.tool_settings.gpencil_sculpt.lock_axis == "AXIS_Z"
    REPORT["grease_pencil_gets_the_same_plane"] = True
    controller.dispatch("tool", "select")
    REPORT["passed"] = True


def advance() -> float | None:
    global INDEX
    try:
        if INDEX < len(STEPS):
            STEPS[INDEX]()
            INDEX += 1
            return 0.4
    except Exception:
        REPORT["failed_step"] = STEPS[INDEX].__name__
        REPORT["error"] = traceback.format_exc()
        bpy.ops.screen.screenshot(filepath=str(CHECKS / "cursor-views-failure.png"))
    (CHECKS / "cursor-views-ui-report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


STEPS.extend(
    (
        setup,
        enable_cursor,
        place_cursor,
        move_cursor,
        view_x,
        draw,
        drawn_in_x,
        view_y,
        draw,
        drawn_in_y,
        view_z,
        draw,
        drawn_in_z,
        flip_z,
        draw,
        drawn_from_below,
        perspective,
        draw,
        drawn_in_perspective,
        click_gizmo_x,
        draw,
        drawn_after_gizmo,
        grease_y,
        grease_follows,
        grease_follows_again,
    )
)
bpy.app.timers.register(advance, first_interval=4.0)
